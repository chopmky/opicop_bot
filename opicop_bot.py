import os
import time
import requests
from dotenv import load_dotenv

OPINION_URL = "https://openapi.opinion.trade/openapi/trade/user/{wallet}"

def send_telegram(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=20)
    if resp.status_code != 200:
        print("❌ Telegram API error:", resp.status_code, resp.text)

def fmt_outcome(outcome_side) -> str:
    # 1 = YES, 2 = NO (theo docs)
    if str(outcome_side) == "1":
        return "YES"
    if str(outcome_side) == "2":
        return "NO"
    return str(outcome_side)

def fetch_trades(api_key: str, wallet: str):
    """
    Gọi endpoint lịch sử trade của user (wallet).
    Docs: GET /openapi/trade/user/{walletAddress}
    """
    url = OPINION_URL.format(wallet=wallet)
    headers = {"apikey": api_key}
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    # API thường trả { "code": ..., "msg": ..., "data": [...] } hoặc trực tiếp list
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    return data

def pick_id(trade: dict) -> str:
    """
    Dùng để chống trùng: ưu tiên txHash, fallback tradeNo, fallback createdAt.
    """
    for k in ["txHash", "tradeNo", "createdAt", "id"]:
        v = trade.get(k)
        if v:
            return str(v)
    # fallback cuối cùng
    return str(trade)

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

def main():
    load_dotenv()

    tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
    tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
    api_key = os.getenv("OPINION_API_KEY")
    wallet = os.getenv("SMART_WALLET")

    if not all([tg_token, tg_chat_id, api_key, wallet]):
        print("❌ Thiếu TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID / OPINION_API_KEY / SMART_WALLET trong .env")
        return

    print("🚀 Bot started. Poll mỗi 5 giây để bắt executed trades...")
    last_seen_id = None

    while True:
        try:
            trades = fetch_trades(api_key, wallet)

            # giả sử list trades trả về mới nhất trước; nếu ngược thì ta vẫn xử lý được bằng sorting nhẹ
            if isinstance(trades, list) and len(trades) > 0:
                # lọc trades "mới" so với last_seen_id
                if last_seen_id is None:
                    # lần đầu chạy: không spam lịch sử, chỉ set mốc
                    last_seen_id = pick_id(trades[0])
                    print("ℹ️ Set mốc last_seen (không gửi lịch sử):", last_seen_id)
                else:
                    new_trades = []
                    for tr in trades:
                        if pick_id(tr) == last_seen_id:
                            break
                        new_trades.append(tr)

                    if new_trades:
                        # gửi theo thứ tự cũ -> mới để đọc dễ
                        for tr in reversed(new_trades):
                            msg = format_trade_message(wallet, tr)
                            send_telegram(tg_token, tg_chat_id, msg)
                            print("✅ Sent trade:", pick_id(tr))

                        last_seen_id = pick_id(trades[0])

        except Exception as e:
            print("⚠️ Lỗi khi poll:", repr(e))

        time.sleep(3)

if __name__ == "__main__":
    main()