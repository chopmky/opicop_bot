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
OPINION_POSITIONS_URL = "https://openapi.opinion.trade/openapi/positions/user/{wallet}"
POLL_SECONDS = 5
HEARTBEAT_SECONDS = 3600
DAILY_FILE = "daily_summary.json"
STATE_FILE = "state.json"

SAFE_PROXY_FACTORY = "0xa6B71E26C5e0845f74c812102Ca7114b6a896AB2"

MORALIS_API_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJub25jZSI6IjY3NWUwMGY4LTdiMTYtNDRiMS04ZWMyLTc0ZWU5ODg4NDkwZCIsIm9yZ0lkIjoiNTAxOTg4IiwidXNlcklkIjoiNTE2NTIzIiwidHlwZUlkIjoiYmZlOTMyYjQtZWM2My00NzFmLTk5YzktNTJiMjJlNjFlMDQ4IiwidHlwZSI6IlBST0pFQ1QiLCJpYXQiOjE3NzE4NjEzNzIsImV4cCI6NDkyNzYyMTM3Mn0.fJE8A3LO4FYDmC967VWOab6W4uREUUumm84XYaFWkh8"

TELEGRAM_CHAT_ID = "508551859"

TG_BASE = "https://api.telegram.org/bot{token}/{method}"


# ============================================================
# FIND SMART WALLET
# ============================================================

def find_smart_wallet(eoa_address: str) -> str | None:
    factory_lower = SAFE_PROXY_FACTORY.lower()
    headers = {
        "accept": "application/json",
        "X-API-Key": MORALIS_API_KEY,
    }

    try:
        url = f"https://deep-index.moralis.io/api/v2/{eoa_address}"
        params = {"chain": "bsc", "limit": 100}
        print(f"  Đang lấy tx list từ Moralis...")
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        data = resp.json()

        txs = data.get("result", [])
        print(f"  Tìm thấy {len(txs)} tx")

        factory_txs = [
            tx for tx in txs
            if (tx.get("to_address") or "").lower() == factory_lower
        ]

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

        print(f"  Tx tới factory: {len(factory_txs)}")

        if not factory_txs:
            return None

        for tx in factory_txs:
            tx_hash = tx["hash"]
            print(f"  Đang đọc tx: {tx_hash[:20]}...")

            tx_url = f"https://deep-index.moralis.io/api/v2/transaction/{tx_hash}"
            tx_resp = requests.get(tx_url, headers=headers, params={"chain": "bsc"}, timeout=30)
            tx_data = tx_resp.json()

            logs = tx_data.get("logs", []) if isinstance(tx_data, dict) else []

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
# FETCH POSITIONS
# ============================================================

def fetch_positions(api_key: str, wallet: str) -> str:
    url = OPINION_POSITIONS_URL.format(wallet=wallet)
    headers = {"apikey": api_key}

    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        positions = data.get("data", data) if isinstance(data, dict) else data

        if not positions or not isinstance(positions, list) or len(positions) == 0:
            return f"📭 Ví `{wallet[:10]}...` không có position nào."

        lines = [f"📊 Positions của ví `{wallet[:10]}...`\n"]
        for i, p in enumerate(positions, 1):
            market = p.get("marketTitle") or p.get("marketName") or p.get("title") or f"Market {p.get('marketId', '?')}"
            outcome = "YES" if str(p.get("outcomeSide", "")) == "1" else "NO" if str(p.get("outcomeSide", "")) == "2" else str(p.get("outcomeSide", "?"))
            shares = p.get("shares") or p.get("amount") or "?"
            value = p.get("currentValue") or p.get("value") or p.get("usdValue") or "?"
            pnl = p.get("pnl") or p.get("unrealizedPnl") or ""

            lines.append(f"{i}. *{market[:50]}*")
            lines.append(f"   {outcome} | Shares: {shares} | Value: {value}")
            if pnl:
                lines.append(f"   PnL: {pnl}")

        return "\n".join(lines)

    except Exception as e:
        print("❌ fetch_positions error:", repr(e))
        return "❌ Không lấy được positions. Thử lại sau."


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
        [{"text": "📊 Xem Positions",     "callback_data": "view_positions"}],
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
    state["chat_id"] = str(chat_id)
    save_state(state)

    if monitor_thread and monitor_thread.is_alive():
        monitor_thread.stop()
        monitor_thread.join(timeout=10)

    monitor_thread = MonitorThread(token, chat_id, api_key, wallet)
    monitor_thread.start()


def ask_confirm_change(token, chat_id, current_wallet, new_wallet):
    send_message(
        token, chat_id,
        f"⚠️ Đang monitor ví:\n`{current_wallet}`\n\nBạn có muốn chuyển sang monitor ví mới không?\n`{new_wallet}`",
        reply_markup={
            "inline_keyboard": [
                [
                    {"text": "✅ Có, đổi ví", "callback_data": f"confirm_change:{new_wallet}"},
                    {"text": "❌ Không, giữ nguyên", "callback_data": "cancel_change"},
                ]
            ]
        },
        parse_mode="Markdown"
    )


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

    if text == "/positions":
        state = load_state()
        wallet = state.get("monitored_wallet")
        if wallet:
            msg = fetch_positions(api_key, wallet)
            send_message(token, chat_id, msg, parse_mode="Markdown",
                reply_markup={"inline_keyboard": [[{"text": "🏠 Menu chính", "callback_data": "main_menu"}]]})
        else:
            send_message(token, chat_id, "⚠️ Chưa monitor ví nào. Bấm Monitor Wallet trước nhé!",
                reply_markup={"inline_keyboard": [[{"text": "🏠 Menu chính", "callback_data": "main_menu"}]]})
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
            state = load_state()
            current_wallet = state.get("monitored_wallet")

            if current_wallet and monitor_thread and monitor_thread.is_alive():
                send_message(token, chat_id,
                    f"✅ Tìm thấy Smart Wallet!\n\nEOA: `{eoa}`\nSmart Wallet: `{smart_wallet}`",
                    parse_mode="Markdown")
                ask_confirm_change(token, chat_id, current_wallet, smart_wallet)
            else:
                send_message(token, chat_id,
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
            send_message(token, chat_id,
                "❌ Không tìm thấy smart wallet cho EOA này.\n\nCó thể ví chưa từng dùng Opinion.",
                reply_markup={"inline_keyboard": [[{"text": "🏠 Menu chính", "callback_data": "main_menu"}]]}
            )
        return

    if step == "waiting_smart_wallet":
        wallet = text
        clear_chat_step(chat_id)
        state = load_state()
        current_wallet = state.get("monitored_wallet")

        if current_wallet and monitor_thread and monitor_thread.is_alive():
            ask_confirm_change(token, chat_id, current_wallet, wallet)
        else:
            start_monitoring(token, chat_id, api_key, wallet)
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

    if data == "view_positions":
        state = load_state()
        wallet = state.get("monitored_wallet")
        if wallet:
            edit_message(token, chat_id, message_id, "⏳ Đang lấy positions...")
            msg = fetch_positions(api_key, wallet)
            edit_message(token, chat_id, message_id, msg,
                reply_markup={"inline_keyboard": [[{"text": "🏠 Menu chính", "callback_data": "main_menu"}]]})
        else:
            edit_message(token, chat_id, message_id,
                "⚠️ Chưa monitor ví nào. Bấm Monitor Wallet trước nhé!",
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
        set_chat_step(chat_id, "waiting_smart_wallet")
        state = load_state()
        current_wallet = state.get("monitored_wallet")

        if current_wallet and monitor_thread and monitor_thread.is_alive():
            edit_message(token, chat_id, message_id,
                f"👁 Đang monitor ví:\n`{current_wallet}`\n\nNhập ví smart wallet mới muốn monitor:",
                reply_markup={"inline_keyboard": [[{"text": "🏠 Hủy bỏ", "callback_data": "main_menu"}]]})
        else:
            edit_message(token, chat_id, message_id,
                "👁 *Monitor Wallet*\n\nNhập địa chỉ Smart Wallet muốn monitor:",
                reply_markup={"inline_keyboard": [[{"text": "🏠 Menu chính", "callback_data": "main_menu"}]]})
        return

    if data.startswith("monitor_found:"):
        wallet = data.split("monitor_found:")[1]
        start_monitoring(token, chat_id, api_key, wallet)
        return

    if data.startswith("confirm_change:"):
        new_wallet = data.split("confirm_change:")[1]
        clear_chat_step(chat_id)
        edit_message(token, chat_id, message_id,
            f"✅ Đang chuyển sang monitor ví mới:\n`{new_wallet}`")
        start_monitoring(token, chat_id, api_key, new_wallet)
        return

    if data == "cancel_change":
        clear_chat_step(chat_id)
        edit_message(token, chat_id, message_id,
            "✅ Giữ nguyên ví đang monitor.",
            reply_markup={"inline_keyboard": [[{"text": "🏠 Menu chính", "callback_data": "main_menu"}]]})
        return


# ============================================================
# TELEGRAM UPDATE LOOP
# ============================================================

def run_bot(token, api_key):
    print("🤖 Bot started, polling Telegram updates...")
    offset = 0
    last_summary_date = None

    # Auto-resume monitor ví cũ khi restart
    state = load_state()
    saved_wallet = state.get("monitored_wallet")
    if saved_wallet:
        print(f"🔄 Auto-resume monitor ví: {saved_wallet}")
        global monitor_thread
        monitor_thread = MonitorThread(token, TELEGRAM_CHAT_ID, api_key, saved_wallet)
        monitor_thread.start()

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

            # Daily summary lúc 23:58
            now = datetime.now()
            today_str = str(date.today())
            if now.hour == 23 and now.minute >= 58 and last_summary_date != today_str:
                if monitor_thread and monitor_thread.is_alive():
                    daily = load_daily()
                    send_message(token, TELEGRAM_CHAT_ID,
                        build_daily_summary(monitor_thread.wallet, daily))
                    save_daily({"date": today_str, "total": 0, "markets": []})
                    last_summary_date = today_str
                    print(f"📊 Daily summary sent for {today_str}")

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