import os
import time
import json
import threading
import requests
from datetime import datetime, date
from dotenv import load_dotenv

# ============================================================
# CONFIG
# ============================================================
OPINION_TRADE_URL = "https://openapi.opinion.trade/openapi/trade/user/{wallet}"
OPINION_POSITIONS_URL = "https://openapi.opinion.trade/openapi/positions/user/{wallet}"
POLL_SECONDS = 5
HEARTBEAT_SECONDS = 3600
DAILY_FILE = "daily_summary.json"
STATE_FILE = "state.json"
TELEGRAM_CHAT_ID = "508551859"
TG_BASE = "https://api.telegram.org/bot{token}/{method}"


# ============================================================
# FETCH POSITIONS
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
            return "Ví không có position nào đang mở."

        lines = [f"Positions ({len(positions)} vị thế)\n"]
        for i, p in enumerate(positions, 1):
            root_market = p.get("rootMarketTitle") or p.get("marketTitle") or f"Market {p.get('marketId', '?')}"
            sub_market = p.get("marketTitle") or ""
            outcome = "YES" if p.get("outcomeSide") == 1 else "NO"

            try:
                shares_str = f"{float(p.get('sharesOwned') or 0):.4f}"
            except Exception:
                shares_str = "?"

            try:
                value_str = f"${float(p.get('currentValueInQuoteToken') or 0):.4f}"
            except Exception:
                value_str = "?"

            try:
                avg_cost_str = f"{float(p.get('avgEntryPrice') or 0):.4f}c"
            except Exception:
                avg_cost_str = "?"

            try:
                pnl_float = float(p.get("unrealizedPnl") or 0)
                pnl_pct_float = float(p.get("unrealizedPnlPercent") or 0) * 100
                pnl_str = f"+${pnl_float:.4f}" if pnl_float >= 0 else f"-${abs(pnl_float):.4f}"
                pnl_pct_str = f"+{pnl_pct_float:.1f}%" if pnl_pct_float >= 0 else f"{pnl_pct_float:.1f}%"
            except Exception:
                pnl_str = "?"
                pnl_pct_str = "?"

            lines.append(f"{i}. *{root_market}*")
            if sub_market and sub_market != root_market:
                lines.append(f"   {sub_market}")
            lines.append(f"   {outcome} | Shares: {shares_str} | Value: {value_str}")
            lines.append(f"   Avg Cost: {avg_cost_str} | PnL: {pnl_str} ({pnl_pct_str})\n")

        return "\n".join(lines)

    except Exception as e:
        print("fetch_positions error:", repr(e))
        return "Không lấy được positions. Thử lại sau."


# ============================================================
# FETCH HISTORY
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

        if not isinstance(trades, list) or not trades:
            return "Ví chưa có trade nào."

        trades = trades[:10]

        lines = ["*10 Trade gần nhất*\n"]
        for i, t in enumerate(trades, 1):
            side = str(t.get("side", "")).upper()
            outcome = "YES" if str(t.get("outcomeSide", "")) == "1" else "NO"
            root_market = t.get("rootMarketTitle") or t.get("marketTitle") or "?"
            root_market_id = t.get("rootMarketId") or t.get("marketId") or ""
            market_id = t.get("marketId") or ""
            sub_market_title = t.get("marketTitle") or ""
            is_multi = root_market_id and market_id and str(root_market_id) != str(market_id)

            try:
                price_str = f"{float(t.get('price') or 0) * 100:.1f}c"
            except Exception:
                price_str = "?"

            try:
                usd_str = f"${float(t.get('amount') or 0):.2f}"
            except Exception:
                usd_str = "?"

            try:
                ts = int(t.get("createdAt") or 0)
                time_str = datetime.fromtimestamp(ts).strftime("%d/%m %H:%M")
            except Exception:
                time_str = "?"

            if is_multi and sub_market_title and sub_market_title != root_market:
                action_str = f"*{side} {outcome} ({sub_market_title})* for {usd_str} at {price_str}"
            else:
                action_str = f"*{side} {outcome}* for {usd_str} at {price_str}"

            lines.append(
                f"{i}. {action_str}\n"
                f"   {root_market[:50]}\n"
                f"   {time_str}\n"
            )

        return "\n".join(lines)

    except Exception as e:
        print("fetch_history error:", repr(e))
        return "Không lấy được lịch sử trade. Thử lại sau."


# ============================================================
# TELEGRAM HELPERS
# ============================================================

def tg(token, method, **kwargs):
    url = TG_BASE.format(token=token, method=method)
    try:
        resp = requests.post(url, json=kwargs, timeout=30)
        return resp.json()
    except Exception as e:
        print("Telegram error:", repr(e))
        return {}


def send_message(token, chat_id, text, reply_markup=None, parse_mode=None):
    kwargs = {"chat_id": chat_id, "text": text}
    if reply_markup:
        kwargs["reply_markup"] = reply_markup
    if parse_mode:
        kwargs["parse_mode"] = parse_mode
    return tg(token, "sendMessage", **kwargs)


def answer_callback(token, callback_query_id):
    tg(token, "answerCallbackQuery", callback_query_id=callback_query_id)


def edit_message(token, chat_id, message_id, text, reply_markup=None, parse_mode=None):
    kwargs = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if reply_markup:
        kwargs["reply_markup"] = reply_markup
    if parse_mode:
        kwargs["parse_mode"] = parse_mode
    tg(token, "editMessageText", **kwargs)


# ============================================================
# MENU
# ============================================================

def get_main_menu_markup(has_wallet=False):
    if has_wallet:
        return {
            "inline_keyboard": [
                [{"text": "Đổi ví đang monitor", "callback_data": "change_wallet"}],
                [{"text": "View Positions",       "callback_data": "view_positions"}],
                [{"text": "Trade History",         "callback_data": "view_history"}],
                [{"text": "Copy Trade",            "callback_data": "copy_trade"}],
            ]
        }
    else:
        return {
            "inline_keyboard": [
                [{"text": "Monitor Wallet", "callback_data": "monitor_wallet"}],
                [{"text": "View Positions", "callback_data": "view_positions"}],
                [{"text": "Trade History",  "callback_data": "view_history"}],
                [{"text": "Copy Trade",     "callback_data": "copy_trade"}],
            ]
        }


def send_main_menu(token, chat_id, user_name=None):
    state = load_state()
    eoa = state.get("monitored_eoa")
    has_wallet = bool(eoa)

    name = user_name or "bạn"
    if eoa:
        text = f"Welcome {name}, chọn tính năng bạn muốn dùng:\n\nĐang monitor: `{eoa}`"
    else:
        text = f"Welcome {name}, chọn tính năng bạn muốn dùng:"

    send_message(token, chat_id, text,
        reply_markup=get_main_menu_markup(has_wallet),
        parse_mode="Markdown")


def edit_main_menu(token, chat_id, message_id, user_name=None):
    state = load_state()
    eoa = state.get("monitored_eoa")
    has_wallet = bool(eoa)

    name = user_name or "bạn"
    if eoa:
        text = f"Welcome {name}, chọn tính năng bạn muốn dùng:\n\nĐang monitor: `{eoa}`"
    else:
        text = f"Welcome {name}, chọn tính năng bạn muốn dùng:"

    edit_message(token, chat_id, message_id, text,
        reply_markup=get_main_menu_markup(has_wallet),
        parse_mode="Markdown")


def get_user_name(message):
    user = message.get("from") or message.get("chat") or {}
    first = user.get("first_name") or ""
    last = user.get("last_name") or ""
    return (first + " " + last).strip() or "bạn"


# ============================================================
# STATE
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
    market = trade.get("rootMarketTitle") or trade.get("marketTitle") or "unknown"
    if market not in daily["markets"]:
        daily["markets"].append(market)
    save_daily(daily)
    return daily


def build_daily_summary(wallet, daily):
    d = daily.get("date", str(date.today()))
    total = daily.get("total", 0)
    markets = daily.get("markets", [])
    lines = [
        f"*Daily Summary* ({d})",
        f"Ví đang monitor: `{wallet}`",
        f"Tổng lệnh trade: {total}",
        "Markets đã traded:",
    ]
    for m in (markets or ["(không có)"]):
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

    root_market_title = t.get("rootMarketTitle") or t.get("marketTitle") or "?"
    root_market_id = t.get("rootMarketId") or t.get("marketId") or ""
    market_id = t.get("marketId") or ""
    sub_market_title = t.get("marketTitle") or ""

    # Multi-market: rootMarketId khác marketId
    is_multi = root_market_id and market_id and str(root_market_id) != str(market_id)

    if root_market_id:
        suffix = "&type=multi" if is_multi else ""
        market_url = f"https://app.opinion.trade/detail?topicId={root_market_id}{suffix}"
        market_link = f"[{root_market_title}]({market_url})"
    else:
        market_link = root_market_title

    # Action: thêm outcome cụ thể nếu là multi
    if is_multi and sub_market_title and sub_market_title != root_market_title:
        action_str = f"*{side} {outcome} ({sub_market_title})*"
    else:
        action_str = f"*{side} {outcome}*"

    try:
        price_str = f"{float(t.get('price') or 0) * 100:.1f} c"
    except Exception:
        price_str = "?"

    try:
        usd_str = f"${float(t.get('amount') or 0):.2f}"
    except Exception:
        usd_str = "?"

    lines = [
        "✅ *TRADE EXECUTED*",
        "",
        f"Market: {market_link}",
        "",
        f"Target Wallet: `{wallet}`",
        f"• Action: {action_str} for {usd_str}",
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
            result = data.get("result", {})
            return result.get("list") or []
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
        print(f"Monitor started: {self.wallet}")
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
                print("Poll error:", repr(e))
                consecutive_errors += 1
                if consecutive_errors == 10:
                    send_message(self.token, self.chat_id,
                        f"Bot lỗi liên tục 10 lần!\nLỗi cuối: {repr(e)}")

            self.stop_event.wait(POLL_SECONDS)

        print(f"Monitor stopped: {self.wallet}")


# ============================================================
# BOT STATE
# ============================================================

CHAT_STATE = {}
monitor_thread: MonitorThread | None = None


def get_chat_step(chat_id):
    return CHAT_STATE.get(str(chat_id), {}).get("step")


def set_chat_step(chat_id, step):
    CHAT_STATE[str(chat_id)] = {"step": step}


def clear_chat_step(chat_id):
    CHAT_STATE.pop(str(chat_id), None)


def start_monitoring(token, chat_id, api_key, eoa):
    global monitor_thread
    state = load_state()
    state["last_seen_id"] = None
    state["monitored_eoa"] = eoa
    state["chat_id"] = str(chat_id)
    save_state(state)

    if monitor_thread and monitor_thread.is_alive():
        monitor_thread.stop()
        monitor_thread.join(timeout=10)

    monitor_thread = MonitorThread(token, chat_id, api_key, eoa)
    monitor_thread.start()

    send_message(token, chat_id,
        f"Bắt đầu monitor ví:\n`{eoa}`",
        parse_mode="Markdown")


# ============================================================
# HANDLE MESSAGES
# ============================================================

def handle_message(token, api_key, message):
    chat_id = message["chat"]["id"]
    text = message.get("text", "").strip()
    user_name = get_user_name(message)

    if text in ("/start", "/menu"):
        clear_chat_step(chat_id)
        send_main_menu(token, chat_id, user_name)
        return

    if text == "/positions":
        state = load_state()
        eoa = state.get("monitored_eoa")
        if eoa:
            msg = fetch_positions(api_key, eoa)
            send_message(token, chat_id, msg, parse_mode="Markdown",
                reply_markup={"inline_keyboard": [[{"text": "Menu chính", "callback_data": "main_menu"}]]})
        else:
            send_message(token, chat_id, "Chưa monitor ví nào.",
                reply_markup={"inline_keyboard": [[{"text": "Menu chính", "callback_data": "main_menu"}]]})
        return

    if text == "/history":
        state = load_state()
        eoa = state.get("monitored_eoa")
        if eoa:
            msg = fetch_history(api_key, eoa)
            send_message(token, chat_id, msg, parse_mode="Markdown",
                reply_markup={"inline_keyboard": [[{"text": "Menu chính", "callback_data": "main_menu"}]]})
        else:
            send_message(token, chat_id, "Chưa monitor ví nào.",
                reply_markup={"inline_keyboard": [[{"text": "Menu chính", "callback_data": "main_menu"}]]})
        return

    step = get_chat_step(chat_id)

    if step == "waiting_eoa":
        eoa = text
        clear_chat_step(chat_id)
        send_message(token, chat_id,
            f"Đang kiểm tra ví:\n`{eoa}`\n\nVui lòng chờ...",
            parse_mode="Markdown")

        state = load_state()
        current_eoa = state.get("monitored_eoa")

        if current_eoa and current_eoa.lower() != eoa.lower():
            send_message(token, chat_id,
                f"Đang monitor ví:\n`{current_eoa}`\n\nBạn có muốn chuyển sang monitor ví mới không?\n`{eoa}`",
                reply_markup={
                    "inline_keyboard": [[
                        {"text": "Có, đổi ví", "callback_data": f"confirm_change:{eoa}"},
                        {"text": "Không, giữ nguyên", "callback_data": "cancel_change"},
                    ]]
                },
                parse_mode="Markdown"
            )
        else:
            start_monitoring(token, chat_id, api_key, eoa)
        return

    send_message(token, chat_id, "Dùng /start để mở menu nhé!")


# ============================================================
# HANDLE CALLBACKS
# ============================================================

def handle_callback(token, api_key, callback_query):
    chat_id = callback_query["message"]["chat"]["id"]
    message_id = callback_query["message"]["message_id"]
    data = callback_query.get("data", "")
    cq_id = callback_query["id"]
    user_name = get_user_name(callback_query.get("message", {}).get("chat", {}))

    answer_callback(token, cq_id)

    if data == "main_menu":
        clear_chat_step(chat_id)
        edit_main_menu(token, chat_id, message_id, user_name)
        return

    if data == "copy_trade":
        edit_message(token, chat_id, message_id,
            "Tính năng Copy Trade đang được phát triển.\n\nVui lòng quay lại sau!",
            reply_markup={"inline_keyboard": [[{"text": "Menu chính", "callback_data": "main_menu"}]]})
        return

    if data in ("monitor_wallet", "change_wallet"):
        set_chat_step(chat_id, "waiting_eoa")
        state = load_state()
        current_eoa = state.get("monitored_eoa")
        if current_eoa:
            edit_message(token, chat_id, message_id,
                f"Đang monitor ví:\n`{current_eoa}`\n\nNhập EOA wallet mới muốn monitor:",
                reply_markup={"inline_keyboard": [[{"text": "Huỷ bỏ", "callback_data": "main_menu"}]]},
                parse_mode="Markdown")
        else:
            edit_message(token, chat_id, message_id,
                "Nhập địa chỉ EOA wallet muốn monitor:",
                reply_markup={"inline_keyboard": [[{"text": "Hủy bỏ", "callback_data": "main_menu"}]]})
        return

    if data == "view_positions":
        state = load_state()
        eoa = state.get("monitored_eoa")
        if eoa:
            edit_message(token, chat_id, message_id, "Đang lấy positions...")
            msg = fetch_positions(api_key, eoa)
            edit_message(token, chat_id, message_id, msg,
                reply_markup={"inline_keyboard": [[{"text": "Menu chính", "callback_data": "main_menu"}]]},
                parse_mode="Markdown")
        else:
            edit_message(token, chat_id, message_id,
                "Chưa monitor ví nào.",
                reply_markup={"inline_keyboard": [[{"text": "Menu chính", "callback_data": "main_menu"}]]})
        return

    if data == "view_history":
        state = load_state()
        eoa = state.get("monitored_eoa")
        if eoa:
            edit_message(token, chat_id, message_id, "Đang lấy lịch sử trade...")
            msg = fetch_history(api_key, eoa)
            edit_message(token, chat_id, message_id, msg,
                reply_markup={"inline_keyboard": [[{"text": "Menu chính", "callback_data": "main_menu"}]]},
                parse_mode="Markdown")
        else:
            edit_message(token, chat_id, message_id,
                "Chưa monitor ví nào.",
                reply_markup={"inline_keyboard": [[{"text": "Menu chính", "callback_data": "main_menu"}]]})
        return

    if data.startswith("confirm_change:"):
        new_eoa = data.split("confirm_change:")[1]
        clear_chat_step(chat_id)
        edit_message(token, chat_id, message_id,
            f"Đang chuyển sang monitor ví mới:\n`{new_eoa}`",
            parse_mode="Markdown")
        start_monitoring(token, chat_id, api_key, new_eoa)
        return

    if data == "cancel_change":
        clear_chat_step(chat_id)
        edit_message(token, chat_id, message_id,
            "Giữ nguyên ví đang monitor.",
            reply_markup={"inline_keyboard": [[{"text": "Menu chính", "callback_data": "main_menu"}]]})
        return


# ============================================================
# TELEGRAM UPDATE LOOP
# ============================================================

def run_bot(token, api_key):
    print("Bot started, polling Telegram updates...")
    offset = 0
    last_summary_date = None

    state = load_state()
    saved_eoa = state.get("monitored_eoa")
    if saved_eoa:
        print(f"Auto-resume monitor: {saved_eoa}")
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

            # Daily summary 23:58
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
                    print(f"Daily summary sent for {today_str}")

        except Exception as e:
            print("Update loop error:", repr(e))
            time.sleep(5)


# ============================================================
# MAIN
# ============================================================

def main():
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    api_key = os.getenv("OPINION_API_KEY")

    missing = [n for n, v in [("TELEGRAM_BOT_TOKEN", token), ("OPINION_API_KEY", api_key)] if not v]
    if missing:
        for m in missing:
            print(f"Thiếu biến môi trường: {m}")
        return

    print("Config loaded. Starting bot...")
    run_bot(token, api_key)


if __name__ == "__main__":
    main()