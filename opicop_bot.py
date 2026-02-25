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
# FETCH POSITIONS (dùng EOA)
# ============================================================

def fetch_positions(api_key: str, eoa: str) -> str:
    url = OPINION_POSITIONS_URL.format(wallet=eoa)
    headers = {"apikey": api_key}

    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        result = data.get("result", {})
        positions = result.get("list", [])

        if not positions:
            return "📭 Ví không có position nào đang mở."

        lines = [f"📊 *Positions ({len(positions)} vị thế)*\n"]
        for i, p in enumerate(positions, 1):
            root_market = p.get("rootMarketTitle") or p.get("marketTitle") or f"Market {p.get('marketId', '?')}"
            sub_market = p.get("marketTitle") or ""
            outcome = "YES" if p.get("outcomeSide") == 1 else "NO"
            shares = p.get("sharesOwned") or "?"
            value = p.get("currentValueInQuoteToken") or "?"
            avg_cost = p.get("avgEntryPrice") or "?"
            pnl = p.get("unrealizedPnl") or "0"
            pnl_pct = p.get("unrealizedPnlPercent") or "0"

            try:
                pnl_float = float(pnl)
                pnl_pct_float = float(pnl_pct) * 100
                pnl_str = f"+${pnl_float:.2f}" if pnl_float >= 0 else f"-${abs(pnl_float):.2f}"
                pnl_pct_str = f"+{pnl_pct_float:.1f}%" if pnl_pct_float >= 0 else f"{pnl_pct_float:.1f}%"
            except Exception:
                pnl_str = str(pnl)
                pnl_pct_str = str(pnl_pct)

            lines.append(f"{i}. *{root_market}*")
            if sub_market and sub_market != root_market:
                lines.append(f"   📅 {sub_market}")
            lines.append(f"   {outcome} | Shares: {shares} | Value: ${value}")
            lines.append(f"   Avg Cost: {avg_cost}¢ | PnL: {pnl_str} ({pnl_pct_str})")

        return "\n".join(lines)

    except Exception as e:
        print("❌ fetch_positions error:", repr(e))
        return "❌ Không lấy được positions. Thử lại sau."


# ============================================================
# FETCH HISTORY (dùng smart wallet, 10 trade gần nhất)
# ============================================================

def fetch_history(api_key: str, eoa: str) -> str:
    url = OPINION_TRADE_URL.format(wallet=eoa)
    headers = {"apikey": api_key}

    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        result = data.get("result", {})
        trades = result.get("list") or []
        if not isinstance(trades, list):
            return "❌ Không lấy được lịch sử trade."

        trades = trades[:10]  # Lấy 10 gần nhất

        if not trades:
            return "📭 Ví chưa có trade nào."

        lines = ["📜 *10 Trade gần nhất*\n"]
        for i, t in enumerate(trades, 1):
            side = str(t.get("side", "")).upper()
            outcome = "YES" if str(t.get("outcomeSide", "")) == "1" else "NO"
            market = t.get("rootMarketTitle") or t.get("marketTitle") or f"Market {t.get('marketId', '?')}"

            # Giá tính bằng cent (nhân 100)
            try:
                price_raw = float(t.get("price") or 0)
                price_str = f"{price_raw * 100:.1f}c"
            except Exception:
                price_str = "?"

            # Dùng amount (USD thực tế)
            try:
                usd_str = f"${float(t.get('amount') or 0):.2f}"
            except Exception:
                usd_str = "?"

            # Thời gian từ Unix timestamp
            try:
                ts = int(t.get("createdAt") or 0)
                dt = datetime.fromtimestamp(ts)
                time_str = dt.strftime("%d/%m %H:%M")
            except Exception:
                time_str = "?"

            side_emoji = "🟢" if side == "BUY" else "🔴"
            lines.append(
                f"{i}. {side_emoji} *{side} {outcome}* — {usd_str} @ {price_str}\n"
                f"   📌 {market[:50]}\n"
                f"   🕐 {time_str}"
            )

        return "\n".join(lines)

    except Exception as e:
        print("❌ fetch_history error:", repr(e))
        return "❌ Không lấy được lịch sử trade. Thử lại sau."


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
        [{"text": "👁 Monitor Wallet",    "callback_data": "monitor_wallet"}],
        [{"text": "📊 Xem Positions",     "callback_data": "view_positions"}],
        [{"text": "📜 Lịch sử Trade",     "callback_data": "view_history"}],
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
        f"*Daily Summary* ({d})",
        f"Ví đang monitor: {wallet}",
        f"Tổng lệnh trade: {total}",
        "Markets đã traded:",
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
    side = str(t.get("side") or "").upper()
    outcome = fmt_outcome(t.get("outcomeSide"))
    market_title = t.get("rootMarketTitle") or t.get("marketTitle") or "?"
    root_market_id = t.get("rootMarketId") or t.get("marketId") or ""

    # Build market URL
    if root_market_id:
        market_url = f"https://app.opinion.trade/detail?topicId={root_market_id}"
        market_link = f"[{market_title}]({market_url})"
    else:
        market_link = market_title

    try:
        price_str = f"{float(t.get('price') or 0) * 100:.1f} ¢"
    except Exception:
        price_str = "?"

    try:
        usd_str = f"${float(t.get('amount') or 0):.2f}"
    except Exception:
        usd_str = "?"

    lines = [
        "✅ *TRADE EXECUTED*",
        f"",
        f"Market: {market_link}",
        f"",
        f"Target Wallet: `{wallet}`",
        f"• Action: *{side} {outcome}* for {usd_str}",
        f"• Order Price: {price_str}",
    ]
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
            # API trả về result.list
            result = data.get("result", {})
            trades = result.get("list") or []
            return trades
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
            f"*Bắt đầu monitor ví:*\n`{self.wallet}`",
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


def start_monitoring(token, chat_id, api_key, smart_wallet, eoa):
    global monitor_thread
    state = load_state()
    state["last_seen_id"] = None
    state["monitored_wallet"] = smart_wallet
    state["monitored_eoa"] = eoa
    state["chat_id"] = str(chat_id)
    save_state(state)

    if monitor_thread and monitor_thread.is_alive():
        monitor_thread.stop()
        monitor_thread.join(timeout=10)

    # Poll bằng EOA (API index theo EOA)
    monitor_thread = MonitorThread(token, chat_id, api_key, eoa)
    monitor_thread.start()


def do_find_and_monitor(token, chat_id, api_key, eoa):
    """Tìm smart wallet từ EOA rồi bắt đầu monitor hoặc hỏi xác nhận"""
    smart_wallet = find_smart_wallet(eoa)

    if not smart_wallet:
        send_message(token, chat_id,
            "❌ Không tìm thấy smart wallet cho EOA này.\n\nCó thể ví chưa từng dùng Opinion.",
            reply_markup={"inline_keyboard": [[{"text": "🏠 Menu chính", "callback_data": "main_menu"}]]}
        )
        return

    state = load_state()
    current_wallet = state.get("monitored_wallet")

    if current_wallet and current_wallet.lower() != smart_wallet.lower():
        # Đang monitor ví khác → hỏi xác nhận bằng nút (gộp 1 message)
        send_message(
            token, chat_id,
            f"✅ Tìm thấy Smart Wallet!\n\nEOA: `{eoa}`\nSmart Wallet: `{smart_wallet}`\n\n⚠️ Đang monitor ví:\n`{current_wallet}`\n\nBạn có muốn chuyển sang monitor ví mới không?",
            reply_markup={
                "inline_keyboard": [
                    [
                        {"text": "✅ Có, đổi ví", "callback_data": f"confirm_change:{smart_wallet}~{eoa}"},
                        {"text": "❌ Không, giữ nguyên", "callback_data": "cancel_change"},
                    ]
                ]
            },
            parse_mode="Markdown"
        )
    else:
        # Chưa monitor ví nào → monitor luôn
        send_message(token, chat_id,
            f"✅ Tìm thấy Smart Wallet!\n\nEOA: `{eoa}`\nSmart Wallet: `{smart_wallet}`",
            parse_mode="Markdown")
        start_monitoring(token, chat_id, api_key, smart_wallet, eoa)


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
        eoa = state.get("monitored_eoa")
        if eoa:
            msg = fetch_positions(api_key, eoa)
            send_message(token, chat_id, msg, parse_mode="Markdown",
                reply_markup={"inline_keyboard": [[{"text": "🏠 Menu chính", "callback_data": "main_menu"}]]})
        else:
            send_message(token, chat_id, "⚠️ Chưa monitor ví nào. Bấm Monitor Wallet trước nhé!",
                reply_markup={"inline_keyboard": [[{"text": "🏠 Menu chính", "callback_data": "main_menu"}]]})
        return

    if text == "/history":
        state = load_state()
        eoa = state.get("monitored_eoa")
        if eoa:
            msg = fetch_history(api_key, eoa)
            send_message(token, chat_id, msg, parse_mode="Markdown",
                reply_markup={"inline_keyboard": [[{"text": "🏠 Menu chính", "callback_data": "main_menu"}]]})
        else:
            send_message(token, chat_id, "⚠️ Chưa monitor ví nào. Bấm Monitor Wallet trước nhé!",
                reply_markup={"inline_keyboard": [[{"text": "🏠 Menu chính", "callback_data": "main_menu"}]]})
        return

    step = get_chat_step(chat_id)

    if step == "waiting_eoa":
        eoa = text
        clear_chat_step(chat_id)
        send_message(token, chat_id,
            f"🔍 Đang tìm smart wallet cho:\n`{eoa}`\n\nVui lòng chờ...",
            parse_mode="Markdown")
        # Chạy trong thread riêng để không block bot
        t = threading.Thread(target=do_find_and_monitor, args=(token, chat_id, api_key, eoa), daemon=True)
        t.start()
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
        eoa = state.get("monitored_eoa")
        if eoa:
            edit_message(token, chat_id, message_id, "⏳ Đang lấy positions...")
            msg = fetch_positions(api_key, eoa)
            edit_message(token, chat_id, message_id, msg,
                reply_markup={"inline_keyboard": [[{"text": "🏠 Menu chính", "callback_data": "main_menu"}]]})
        else:
            edit_message(token, chat_id, message_id,
                "⚠️ Chưa monitor ví nào. Bấm Monitor Wallet trước nhé!",
                reply_markup={"inline_keyboard": [[{"text": "🏠 Menu chính", "callback_data": "main_menu"}]]})
        return

    if data == "view_history":
        state = load_state()
        eoa = state.get("monitored_eoa")
        if eoa:
            edit_message(token, chat_id, message_id, "⏳ Đang lấy lịch sử trade...")
            msg = fetch_history(api_key, eoa)
            edit_message(token, chat_id, message_id, msg,
                reply_markup={"inline_keyboard": [[{"text": "🏠 Menu chính", "callback_data": "main_menu"}]]})
        else:
            edit_message(token, chat_id, message_id,
                "⚠️ Chưa monitor ví nào. Bấm Monitor Wallet trước nhé!",
                reply_markup={"inline_keyboard": [[{"text": "🏠 Menu chính", "callback_data": "main_menu"}]]})
        return

    if data == "monitor_wallet":
        clear_chat_step(chat_id)
        set_chat_step(chat_id, "waiting_eoa")
        state = load_state()
        current_wallet = state.get("monitored_wallet")

        if current_wallet:
            edit_message(token, chat_id, message_id,
                f"👁 Đang monitor ví:\n`{current_wallet}`\n\nNhập EOA wallet mới muốn monitor:",
                reply_markup={"inline_keyboard": [[{"text": "🏠 Hủy bỏ", "callback_data": "main_menu"}]]})
        else:
            edit_message(token, chat_id, message_id,
                "👁 *Monitor Wallet*\n\nNhập địa chỉ EOA wallet muốn monitor:",
                reply_markup={"inline_keyboard": [[{"text": "🏠 Menu chính", "callback_data": "main_menu"}]]})
        return

    if data.startswith("confirm_change:"):
        payload = data.split("confirm_change:")[1]
        parts = payload.split("~")
        new_smart_wallet = parts[0]
        new_eoa = parts[1] if len(parts) > 1 else None
        clear_chat_step(chat_id)
        edit_message(token, chat_id, message_id,
            f"✅ *Đang chuyển sang monitor ví mới:*\n`{new_smart_wallet}`")
        start_monitoring(token, chat_id, api_key, new_smart_wallet, new_eoa)
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
    saved_eoa = state.get("monitored_eoa")
    if saved_eoa:
        print(f"🔄 Auto-resume monitor ví: {saved_eoa}")
        global monitor_thread
        monitor_thread = MonitorThread(token, TELEGRAM_CHAT_ID, api_key, saved_eoa)
        monitor_thread.start()

    processed_ids = set()
    while True:
        try:
            resp = requests.get(
                TG_BASE.format(token=token, method="getUpdates"),
                params={"offset": offset, "timeout": 30},
                timeout=40
            )
            updates = resp.json().get("result", [])

            for update in updates:
                uid = update["update_id"]
                offset = uid + 1
                if uid in processed_ids:
                    continue
                processed_ids.add(uid)
                if len(processed_ids) > 1000:
                    processed_ids = set(list(processed_ids)[-500:])
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
                        build_daily_summary(monitor_thread.wallet, daily),
                        parse_mode="Markdown")
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