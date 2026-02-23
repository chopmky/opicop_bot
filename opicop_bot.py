import os
import re
import time
import json
import threading
import requests
from datetime import datetime, date
from dotenv import load_dotenv
from web3 import Web3

# ============================================================
# CONFIG
# ============================================================
OPINION_TRADE_URL = "https://openapi.opinion.trade/openapi/trade/user/{wallet}"
POLL_SECONDS = 5
HEARTBEAT_SECONDS = 3600
DAILY_FILE = "daily_summary.json"
STATE_FILE = "state.json"

# Opinion Safe Proxy Factory contract
SAFE_PROXY_FACTORY = "0xa6B71E26C5e0845f74c812102Ca7114b6a896AB2"

# Moralis API
MORALIS_API_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJub25jZSI6IjY3NWUwMGY4LTdiMTYtNDRiMS04ZWMyLTc0ZWU5ODg4NDkwZCIsIm9yZ0lkIjoiNTAxOTg4IiwidXNlcklkIjoiNTE2NTIzIiwidHlwZUlkIjoiYmZlOTMyYjQtZWM2My00NzFmLTk5YzktNTJiMjJlNjFlMDQ4IiwidHlwZSI6IlBST0pFQ1QiLCJpYXQiOjE3NzE4NjEzNzIsImV4cCI6NDkyNzYyMTM3Mn0.fJE8A3LO4FYDmC967VWOab6W4uREUUumm84XYaFWkh8"

# Telegram API base
TG_BASE = "https://api.telegram.org/bot{token}/{method}"


# ============================================================
# FIND SMART WALLET (Moralis API - endpoint đúng)
# ============================================================

def find_smart_wallet(eoa_address: str) -> str | None:
    factory_lower = SAFE_PROXY_FACTORY.lower()
    headers = {
        "accept": "application/json",
        "X-API-Key": MORALIS_API_KEY,
    }

    try:
        # Lấy danh sách tx của EOA
        url = f"https://deep-index.moralis.io/api/v2/{eoa_address}"
        params = {"chain": "bsc", "limit": 100}
        print(f"  Đang lấy tx list từ Moralis...")
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        data = resp.json()

        txs = data.get("result", [])
        print(f"  Tìm thấy {len(txs)} tx")

        # Lọc tx gửi tới factory
        factory_txs = [
            tx for tx in txs
            if (tx.get("to_address") or "").lower() == factory_lower
        ]
        print(f"  Tx tới factory: {len(factory_txs)}")

        # Nếu không thấy, thử trang tiếp
        cursor = data.get("cursor")
        page = 1
        while not factory_txs and cursor and page < 5:
            params["cursor"] = cursor
            resp2 = requests.get(url, headers=headers, params=params, timeout=30)
            data2 = resp2.json()
            more_txs = data2.get("result", [])
            factory_txs = [
                tx for tx in more_txs
                if (tx.get("to_address") or "").lower() == factory_lower
            ]
            cursor = data2.get("cursor")
            page += 1

        if not factory_txs:
            print("  ❌ Không tìm thấy tx nào tới factory")
            return None

        # Đọc receipt để lấy địa chỉ smart wallet từ log
        for tx in factory_txs:
            tx_hash = tx["hash"]
            print(f"  Đang đọc receipt: {tx_hash[:20]}...")

            receipt_url = f"https://deep-index.moralis.io/api/v2/transaction/{tx_hash}/receipt"
            receipt_resp = requests.get(
                receipt_url,
                headers=headers,
                params={"chain": "bsc"},
                timeout=30
            )
            receipt = receipt_resp.json()
            logs = receipt.get("logs", []) if isinstance(receipt, dict) else []

            for log in logs:
                if (log.get("address") or "").lower() == factory_lower:
                    log_data = (log.get("data") or "").replace("0x", "")
                    if len(log_data) >= 64:
                        proxy_address = "0x" + log_data[24:64]
                        return Web3.to_checksum_address(proxy_address)

    except Exception as e:
        print("❌ find_smart_wallet error:", repr(e))

    return None


# ============================================================
# TELEGRAM HELPERS
# ============================================================

def tg(token, method, **kwargs):
    url = TG_BASE.format(token=token, method=method)
    try:
        resp = requests.post(url, json=kwargs, timeout=30)
        return resp.json()
    except Exception as e:
        print("❌ Telegram error:", repr(e))
        return {}


def send_message(token, chat_id, text, reply_markup=None, parse_mode=None):
    kwargs = {"chat_id": chat_id, "text": text}
    if reply_markup:
        kwargs["reply_markup"] = reply_markup
    if parse_mode:
        kwargs["parse_mode"] = parse_mode
    return tg(token, "sendMessage", **kwargs)


def answer_callback(token, callback_query_id, text=None):
    kwargs = {"callback_query_id": callback_query_id}
    if text:
        kwargs["text"] = text
    tg(token, "answerCallbackQuery", **kwargs)


def edit_message(token, chat_id, message_id, text, reply_markup=None):
    kwargs = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if reply_markup:
        kwargs["reply_markup"] = reply_markup
    tg(token, "editMessageText", **kwargs)


# ============================================================
# MAIN MENU
# ============================================================

MAIN_MENU_MARKUP = {
    "inline_keyboard": [
        [{"text": "🔍 Find Smart Wallet", "callback_data": "find_wallet"}],
        [{"text": "👁 Monitor Wallet",    "callback_data": "monitor_wallet"}],
        [{"text": "🤖 Copy Trade",        "callback_data": "copy_trade"}],
    ]
}


def send_main_menu(token, chat_id):
    send_message(
        token, chat_id,
        "👋 Chào mừng! Chọn tính năng bạn muốn dùng:",
        reply_markup=MAIN_MENU_MARKUP
    )


# ============================================================
# STATE MANAGEMENT
# ============================================================

def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ============================================================
# DAILY SUMMARY
# ============================================================

def load_daily():
    today_str = str(date.today())
    try:
        with open(DAILY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("date") != today_str:
            return {"date": today_str, "total": 0, "markets": []}
        return data
    except Exception:
        return {"date": today_str, "total": 0, "markets": []}


def save_daily(daily):
    with open(DAILY_FILE, "w", encoding="utf-8") as f:
        json.dump(daily, f, ensure_ascii=False, indent=2)


def add_trade_to_daily(daily, trade):
    today_str = str(date.today())
    if daily.get("date") != today_str:
        daily = {"date": today_str, "total": 0, "markets": []}
    daily["total"] = int(daily.get("total", 0)) + 1
    market = extract_market_name(trade)
    if market not in daily["markets"]:
        daily["markets"].append(market)
    save_daily(daily)
    return daily


def extract_market_name(t):
    for k in ["marketName", "marketTitle", "title", "question"]:
        v = t.get(k)
        if v:
            return str(v)
    if t.get("marketId"):
        return f"marketId:{t['marketId']}"
    if t.get("rootMarketId"):
        return f"rootMarketId:{t['rootMarketId']}"
    return "unknown"


def build_daily_summary(wallet, daily):
    d = daily.get("date", str(date.today()))
    total = daily.get("total", 0)
    markets = daily.get("markets", [])
    lines = [
        f"📊 Daily Summary ({d})",
        f"Wallet: {wallet}",
        f"Total executed trades: {total}",
        "Markets traded today:",
    ]
    for m in (markets or ["(none)"]):
        lines.append(f"- {m}")
    return "\n".join(lines)


# ============================================================
# TRADE POLLING
# ============================================================

def pick_id(trade):
    for k in ["txHash", "tradeNo", "createdAt", "id"]:
        v = trade.get(k)
        if v:
            return str(v)
    return str(trade)


def fmt_outcome(side):
    return "YES" if str(side) == "1" else "NO" if str(side) == "2" else str(side)


def format_trade_message(wallet, t):
    lines = [
        "✅ TRADE EXECUTED (Opinion)",
        f"Wallet: `{wallet}`",
        f"Side: {t.get('side', '')} | Outcome: {fmt_outcome(t.get('outcomeSide'))}",
        f"Price: {t.get('price')}",
        f"Amount: {t.get('amount')} | USD: {t.get('usdAmount')}",
        f"Shares: {t.get('shares')}",
        f"Fee: {t.get('fee')}",
    ]
    if t.get("txHash"):
        lines.append(f"Tx: `{t['txHash']}`")
    if t.get("createdAt"):
        lines.append(f"Time: {t['createdAt']}")
    return "\n".join(lines)


def fetch_trades(api_key, wallet):
    url = OPINION_TRADE_URL.format(wallet=wallet)
    headers = {"apikey": api_key}
    last_err = None
    for _ in range(3):
        try:
            resp = requests.get(url, headers=headers, timeout=45)
            resp.raise_for_status()
            data = resp.json()
            return data["data"] if isinstance(data, dict) and "data" in data else data
        except Exception as e:
            last_err = e
            time.sleep(2)
    raise last_err


# ============================================================
# MONITOR THREAD
# ============================================================

class MonitorThread(threading.Thread):
    def __init__(self, token, chat_id, api_key, wallet):
        super().__init__(daemon=True)
        self.token = token
        self.chat_id = chat_id
        self.api_key = api_key
        self.wallet = wallet
        self.stop_event = threading.Event()

        state = load_state()
        self.last_seen_id = state.get("last_seen_id")
        self.daily = load_daily()
        self.last_heartbeat = 0
        self.last_summary_date = None

    def stop(self):
        self.stop_event.set()

    def run(self):
        print(f"🚀 Monitor started: {self.wallet}")
        send_message(
            self.token, self.chat_id,
            f"👁 Bắt đầu monitor ví:\n`{self.wallet}`",
            parse_mode="Markdown"
        )
        consecutive_errors = 0

        while not self.stop_event.is_set():
            try:
                now_ts = time.time()
                if now_ts - self.last_heartbeat >= HEARTBEAT_SECONDS:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Monitor alive: {self.wallet}")
                    self.last_heartbeat = now_ts

                trades = fetch_trades(self.api_key, self.wallet)
                consecutive_errors = 0

                if isinstance(trades, list) and len(trades) > 0:
                    if self.last_seen_id is None:
                        self.last_seen_id = pick_id(trades[0])
                        state = load_state()
                        state["last_seen_id"] = self.last_seen_id
                        save_state(state)
                    else:
                        new_trades = []
                        for tr in trades:
                            if pick_id(tr) == self.last_seen_id:
                                break
                            new_trades.append(tr)

                        for tr in reversed(new_trades):
                            send_message(
                                self.token, self.chat_id,
                                format_trade_message(self.wallet, tr),
                                parse_mode="Markdown"
                            )
                            self.daily = add_trade_to_daily(self.daily, tr)

                        if new_trades:
                            self.last_seen_id = pick_id(trades[0])
                            state = load_state()
                            state["last_seen_id"] = self.last_seen_id
                            save_state(state)

                now = datetime.now()
                today_str = str(date.today())
                if now.hour == 23 and now.minute >= 59 and self.last_summary_date != today_str:
                    self.daily = load_daily()
                    send_message(self.token, self.chat_id, build_daily_summary(self.wallet, self.daily))
                    save_daily({"date": today_str, "total": 0, "markets": []})
                    self.last_summary_date = today_str

            except Exception as e:
                print("⚠️ Poll error:", repr(e))
                consecutive_errors += 1
                if consecutive_errors == 10:
                    send_message(self.token, self.chat_id,
                        f"⚠️ Bot lỗi liên tục 10 lần!\nLỗi cuối: {repr(e)}")

            self.stop_event.wait(POLL_SECONDS)

        print(f"🛑 Monitor stopped: {self.wallet}")


# ============================================================
# BOT CONVERSATION STATE
# ============================================================

CHAT_STATE = {}
monitor_thread: MonitorThread | None = None


def get_chat_step(chat_id):
    return CHAT_STATE.get(str(chat_id), {}).get("step")


def set_chat_step(chat_id, step, data=None):
    CHAT_STATE[str(chat_id)] = {"step": step, "data": data or {}}


def clear_chat_step(chat_id):
    CHAT_STATE.pop(str(chat_id), None)


def start_monitoring(token, chat_id, api_key, wallet):
    global monitor_thread
    state = load_state()
    state["last_seen_id"] = None
    state["monitored_wallet"] = wallet
    save_state(state)

    if monitor_thread and monitor_thread.is_alive():
        monitor_thread.stop()
        monitor_thread.join(timeout=10)

    monitor_thread = MonitorThread(token, chat_id, api_key, wallet)
    monitor_thread.start()


# ============================================================
# HANDLE MESSAGES
# ============================================================

def handle_message(token, api_key, message):
    chat_id = message["chat"]["id"]
    text = message.get("text", "").strip()

    if text in ("/start", "/menu"):
        clear_chat_step(chat_id)
        send_main_menu(token, chat_id)
        return

    step = get_chat_step(chat_id)

    if step == "waiting_eoa":
        eoa = text
        send_message(token, chat_id,
            f"🔍 Đang tìm smart wallet cho EOA:\n`{eoa}`\n\nVui lòng chờ...",
            parse_mode="Markdown")
        clear_chat_step(chat_id)

        smart_wallet = find_smart_wallet(eoa)

        if smart_wallet:
            send_message(
                token, chat_id,
                f"✅ Tìm thấy Smart Wallet!\n\nEOA: `{eoa}`\nSmart Wallet: `{smart_wallet}`",
                reply_markup={
                    "inline_keyboard": [
                        [{"text": "👁 Monitor ví này", "callback_data": f"monitor_found:{smart_wallet}"}],
                        [{"text": "🏠 Menu chính", "callback_data": "main_menu"}],
                    ]
                },
                parse_mode="Markdown"
            )
        else:
            send_message(
                token, chat_id,
                "❌ Không tìm thấy smart wallet cho EOA này.\n\nCó thể ví chưa từng dùng Opinion.",
                reply_markup={"inline_keyboard": [[{"text": "🏠 Menu chính", "callback_data": "main_menu"}]]}
            )
        return

    if step == "waiting_smart_wallet":
        wallet = text
        clear_chat_step(chat_id)
        start_monitoring(token, chat_id, api_key, wallet)
        return

    if step == "waiting_new_wallet_for_change":
        new_wallet = text
        state = load_state()
        current_wallet = state.get("monitored_wallet")
        set_chat_step(chat_id, "confirm_change_wallet", {"new_wallet": new_wallet})
        send_message(
            token, chat_id,
            f"⚠️ Đang monitor ví:\n`{current_wallet}`\n\nBạn có muốn chuyển sang monitor ví mới không?\n`{new_wallet}`\n\nGõ *có* để xác nhận, *không* để giữ nguyên.",
            parse_mode="Markdown"
        )
        return

    if step == "confirm_change_wallet":
        if text.lower() in ("có", "co", "yes", "y"):
            new_wallet = CHAT_STATE.get(str(chat_id), {}).get("data", {}).get("new_wallet")
            clear_chat_step(chat_id)
            if new_wallet:
                start_monitoring(token, chat_id, api_key, new_wallet)
        else:
            clear_chat_step(chat_id)
            send_message(token, chat_id, "✅ Giữ nguyên ví đang monitor.",
                reply_markup={"inline_keyboard": [[{"text": "🏠 Menu chính", "callback_data": "main_menu"}]]})
        return

    send_message(token, chat_id, "Dùng /start để mở menu nhé!",
        reply_markup={"inline_keyboard": [[{"text": "🏠 Menu chính", "callback_data": "main_menu"}]]})


# ============================================================
# HANDLE CALLBACKS
# ============================================================

def handle_callback(token, api_key, callback_query):
    global monitor_thread

    chat_id = callback_query["message"]["chat"]["id"]
    message_id = callback_query["message"]["message_id"]
    data = callback_query.get("data", "")
    cq_id = callback_query["id"]

    answer_callback(token, cq_id)

    if data == "main_menu":
        clear_chat_step(chat_id)
        edit_message(token, chat_id, message_id,
            "👋 Chào mừng! Chọn tính năng bạn muốn dùng:",
            reply_markup=MAIN_MENU_MARKUP)
        return

    if data == "copy_trade":
        edit_message(token, chat_id, message_id,
            "🤖 Tính năng Copy Trade đang được phát triển...\n\nVui lòng quay lại sau!",
            reply_markup={"inline_keyboard": [[{"text": "🏠 Menu chính", "callback_data": "main_menu"}]]})
        return

    if data == "find_wallet":
        clear_chat_step(chat_id)
        set_chat_step(chat_id, "waiting_eoa")
        edit_message(token, chat_id, message_id,
            "🔍 *Find Smart Wallet*\n\nNhập địa chỉ EOA wallet (ví gốc) của trader muốn tìm:",
            reply_markup={"inline_keyboard": [[{"text": "🏠 Menu chính", "callback_data": "main_menu"}]]})
        return

    if data == "monitor_wallet":
        clear_chat_step(chat_id)
        state = load_state()
        current_wallet = state.get("monitored_wallet")

        if current_wallet and monitor_thread and monitor_thread.is_alive():
            set_chat_step(chat_id, "waiting_new_wallet_for_change")
            edit_message(token, chat_id, message_id,
                f"👁 Đang monitor ví:\n`{current_wallet}`\n\nNhập ví smart wallet mới muốn monitor:",
                reply_markup={"inline_keyboard": [[{"text": "🏠 Hủy bỏ", "callback_data": "main_menu"}]]})
        else:
            set_chat_step(chat_id, "waiting_smart_wallet")
            edit_message(token, chat_id, message_id,
                "👁 *Monitor Wallet*\n\nNhập địa chỉ Smart Wallet muốn monitor:",
                reply_markup={"inline_keyboard": [[{"text": "🏠 Menu chính", "callback_data": "main_menu"}]]})
        return

    if data.startswith("monitor_found:"):
        wallet = data.split("monitor_found:")[1]
        state = load_state()
        current_wallet = state.get("monitored_wallet")

        if current_wallet and monitor_thread and monitor_thread.is_alive():
            set_chat_step(chat_id, "confirm_change_wallet", {"new_wallet": wallet})
            send_message(token, chat_id,
                f"⚠️ Đang monitor ví:\n`{current_wallet}`\n\nBạn có muốn chuyển sang monitor ví mới không?\n`{wallet}`\n\nGõ *có* để xác nhận, *không* để giữ nguyên.",
                parse_mode="Markdown")
        else:
            start_monitoring(token, chat_id, api_key, wallet)
        return


# ============================================================
# TELEGRAM UPDATE LOOP
# ============================================================

def run_bot(token, api_key):
    print("🤖 Bot started, polling Telegram updates...")
    offset = 0

    while True:
        try:
            resp = requests.get(
                TG_BASE.format(token=token, method="getUpdates"),
                params={"offset": offset, "timeout": 30},
                timeout=40
            )
            updates = resp.json().get("result", [])

            for update in updates:
                offset = update["update_id"] + 1
                if "message" in update:
                    handle_message(token, api_key, update["message"])
                elif "callback_query" in update:
                    handle_callback(token, api_key, update["callback_query"])

        except Exception as e:
            print("⚠️ Update loop error:", repr(e))
            time.sleep(5)


# ============================================================
# MAIN
# ============================================================

def main():
    load_dotenv()

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    api_key = os.getenv("OPINION_API_KEY")

    missing = []
    for name, val in [
        ("TELEGRAM_BOT_TOKEN", token),
        ("OPINION_API_KEY", api_key),
    ]:
        if not val:
            missing.append(name)

    if missing:
        for m in missing:
            print(f"❌ Thiếu biến môi trường: {m}")
        return

    print("✅ Config loaded. Starting bot...")
    run_bot(token, api_key)


if __name__ == "__main__":
    main()