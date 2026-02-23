import os
import time
import json
import requests
from datetime import datetime, date
from dotenv import load_dotenv

OPINION_URL = "https://openapi.opinion.trade/openapi/trade/user/{wallet}"

# ====== Bạn chỉnh 2 dòng này nếu muốn ======
POLL_SECONDS = 5              # quét ví mỗi N giây (bạn muốn 5 thì đổi thành 5)
HEARTBEAT_PRINT_SECONDS = 3600  # 1 tiếng in 1 lần
# ===========================================

DAILY_FILE = "daily_summary.json"


def send_telegram(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=30)
        if resp.status_code != 200:
            print("❌ Telegram API error:", resp.status_code, resp.text)
    except Exception as e:
        print("❌ Telegram send error:", repr(e))


def fetch_trades(api_key: str, wallet: str):
    """
    Gọi endpoint lịch sử trade của user (wallet).
    GET https://openapi.opinion.trade/openapi/trade/user/{walletAddress}
    """
    url = OPINION_URL.format(wallet=wallet)
    headers = {"apikey": api_key}

    # Retry để đỡ lag mạng / server chậm
    last_err = None
    for _ in range(3):
        try:
            resp = requests.get(url, headers=headers, timeout=45)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and "data" in data:
                return data["data"]
            return data
        except Exception as e:
            last_err = e
            time.sleep(2)

    raise last_err


def pick_id(trade: dict) -> str:
    """
    Dùng để chống trùng: ưu tiên txHash, fallback tradeNo, fallback createdAt.
    """
    for k in ["txHash", "tradeNo", "createdAt", "id"]:
        v = trade.get(k)
        if v:
            return str(v)
    return str(trade)


def fmt_outcome(outcome_side) -> str:
    # 1 = YES, 2 = NO
    if str(outcome_side) == "1":
        return "YES"
    if str(outcome_side) == "2":
        return "NO"
    return str(outcome_side)


def format_trade_message(wallet: str, t: dict) -> str:
    side = t.get("side", "")
    outcome = fmt_outcome(t.get("outcomeSide"))
    price = t.get("price")
    amount = t.get("amount")
    usd_amount = t.get("usdAmount")
    shares = t.get("shares")
    fee = t.get("fee")
    tx = t.get("txHash", "")
    created = t.get("createdAt", "")

    lines = [
        "✅ EXECUTED (Opinion)",
        f"Wallet: {wallet}",
        f"Side: {side} | Outcome: {outcome}",
        f"Price: {price}",
        f"Amount: {amount} | USD: {usd_amount}",
        f"Shares: {shares}",
        f"Fee: {fee}",
    ]
    if tx:
        lines.append(f"Tx: {tx}")
    if created:
        lines.append(f"Time: {created}")

    return "\n".join(lines)


# ---------------- Daily summary storage ----------------

def load_daily():
    today_str = str(date.today())
    if not os.path.exists(DAILY_FILE):
        return {"date": today_str, "total": 0, "markets": []}
    try:
        with open(DAILY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("date") != today_str:
            # sang ngày mới thì reset
            return {"date": today_str, "total": 0, "markets": []}
        if "total" not in data or "markets" not in data:
            return {"date": today_str, "total": 0, "markets": []}
        return data
    except Exception:
        return {"date": today_str, "total": 0, "markets": []}


def save_daily(daily):
    with open(DAILY_FILE, "w", encoding="utf-8") as f:
        json.dump(daily, f, ensure_ascii=False, indent=2)


def extract_market_name(t: dict) -> str:
    """
    Mình không chắc API trả field nào, nên mình thử vài key phổ biến.
    Nếu không có thì fallback 'unknown'.
    """
    for k in ["marketName", "marketTitle", "title", "question"]:
        v = t.get(k)
        if v:
            return str(v)

    # Nếu không có tên, ít nhất lấy marketId/rootMarketId
    market_id = t.get("marketId")
    root_market_id = t.get("rootMarketId")

    if market_id:
        return f"marketId:{market_id}"
    if root_market_id:
        return f"rootMarketId:{root_market_id}"

    return "unknown"


def add_trade_to_daily(daily, trade: dict):
    today_str = str(date.today())
    if daily.get("date") != today_str:
        daily = {"date": today_str, "total": 0, "markets": []}

    daily["total"] = int(daily.get("total", 0)) + 1

    m = extract_market_name(trade)
    if m not in daily["markets"]:
        daily["markets"].append(m)

    save_daily(daily)
    return daily


def build_daily_summary(wallet: str, daily) -> str:
    d = daily.get("date", str(date.today()))
    total = daily.get("total", 0)
    markets = daily.get("markets", [])

    lines = [
        f"📊 Daily Summary ({d})",
        f"Wallet: {wallet}",
        f"Total executed trades: {total}",
        "Markets traded today:",
    ]

    if not markets:
        lines.append("- (none)")
    else:
        for m in markets:
            lines.append(f"- {m}")

    return "\n".join(lines)


# ---------------- Main loop ----------------

def main():
    load_dotenv()

    tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
    tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
    api_key = os.getenv("OPINION_API_KEY")
    wallet = os.getenv("SMART_WALLET")

    if not all([tg_token, tg_chat_id, api_key, wallet]):
        print("❌ Thiếu TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID / OPINION_API_KEY / SMART_WALLET trong .env")
        return

    print("🚀 Bot started. Polling trades...")
    last_seen_id = None
    daily = load_daily()

    last_heartbeat_print = 0
    last_summary_sent_date = None

    while True:
        try:
            # In trạng thái mỗi 1 tiếng
            now_ts = time.time()
            if now_ts - last_heartbeat_print >= HEARTBEAT_PRINT_SECONDS:
                now_str = datetime.now().strftime("%H:%M:%S")
                print(f"[{now_str}] Still running...")
                last_heartbeat_print = now_ts

            trades = fetch_trades(api_key, wallet)

            if isinstance(trades, list) and len(trades) > 0:
                if last_seen_id is None:
                    # Lần đầu chạy: set mốc, không spam lịch sử
                    last_seen_id = pick_id(trades[0])
                else:
                    new_trades = []
                    for tr in trades:
                        if pick_id(tr) == last_seen_id:
                            break
                        new_trades.append(tr)

                    if new_trades:
                        # gửi theo thứ tự cũ -> mới
                        for tr in reversed(new_trades):
                            send_telegram(tg_token, tg_chat_id, format_trade_message(wallet, tr))
                            daily = add_trade_to_daily(daily, tr)

                        last_seen_id = pick_id(trades[0])

            # Daily summary lúc 23:59 (giờ máy)
            now = datetime.now()
            today_str = str(date.today())
            if now.hour == 23 and now.minute == 59:
                if last_summary_sent_date != today_str:
                    daily = load_daily()
                    send_telegram(tg_token, tg_chat_id, build_daily_summary(wallet, daily))

                    # Reset file cho ngày tiếp theo
                    save_daily({"date": str(date.today()), "total": 0, "markets": []})
                    last_summary_sent_date = today_str

        except Exception as e:
            # Không spam Telegram lỗi, chỉ in ra CMD
            # (nếu bạn muốn gửi lỗi Telegram khi lỗi liên tục, mình cũng thêm được)
            print("⚠️ Lỗi khi poll:", repr(e))

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()