# Opinion.trade - Master Context File

## Platform Overview
- Opinion.trade: prediction market trên BSC (BNB Smart Chain)
- User flow: EOA wallet → smart wallet (Safe proxy) → trade
- Web app: https://app.opinion.trade

---

## API

### Base URL
```
https://openapi.opinion.trade/openapi
```

### Authentication
```
Header: apikey: <OPINION_API_KEY>
```

### Response Structure
Tất cả endpoints trả về:
```json
{
  "errno": 0,
  "errmsg": "",
  "result": { ... }
}
```

### Endpoints đã dùng

**Trade History**
```
GET /trade/user/{wallet}
→ result.list (array of trades)
→ Dùng EOA address (không phải smart wallet)
```

**Positions**
```
GET /positions/user/{wallet}
→ result.list (array of positions)
→ Dùng EOA address (không phải smart wallet)
```

---

## Data Structure

### Trade Object
```
txHash, marketId, marketTitle, rootMarketId, rootMarketTitle
side: "Buy" | "Sell" | "Merge"
outcome: "YES" | "NO"
outcomeSide: 1 (YES) | 2 (NO)
price: decimal (0.444 = 44.4c)
shares: số lượng shares
amount: USD thực tế (dùng cái này!)
usdAmount: wei (chia 10^18 = amount, KHÔNG dùng)
fee, profit, status, chainId
createdAt: Unix timestamp (không phải ISO string)
```

### Position Object
```
marketId, marketTitle, rootMarketId, rootMarketTitle
outcomeSide: 1 (YES) | 2 (NO)
sharesOwned, sharesFrozen
currentValueInQuoteToken: giá trị hiện tại (USD)
avgEntryPrice: giá mua trung bình (cent)
unrealizedPnl, unrealizedPnlPercent
claimStatus, quoteToken
```

---

## Quirks & Gotchas

### EOA vs Smart Wallet
- **Trade history & Positions**: dùng EOA → có data
- **Smart wallet**: API trả về empty, KHÔNG dùng
- Smart wallet = Safe proxy contract, deploy bởi GnosisSafeProxyFactory
- Không cần tìm smart wallet nữa — EOA là đủ cho mọi thứ

### Multi vs Single Market
- Single market: `rootMarketId == marketId`
- Multi market: `rootMarketId != marketId`
  - `rootMarketTitle`: tên market chính (ví dụ "Backpack FDV above...")
  - `marketTitle`: tên outcome cụ thể (ví dụ "$1B", "March 31, 2026")
  - URL single: `https://app.opinion.trade/detail?topicId={rootMarketId}`
  - URL multi: `https://app.opinion.trade/detail?topicId={rootMarketId}&type=multi`

### Số tiền
- `amount` = USD thực tế, dùng trực tiếp
- `usdAmount` = dạng wei, chia 10^18 mới ra USD — nhưng dùng `amount` cho đơn giản

### Thời gian
- `createdAt` là Unix timestamp (integer), không phải ISO string
- Convert: `datetime.fromtimestamp(ts)`

### Price
- API trả về dạng decimal: `0.444`
- Hiển thị dạng cent: nhân 100 → `44.4c`

---

## Market URL Examples
```
Single: https://app.opinion.trade/detail?topicId=226
Multi:  https://app.opinion.trade/detail?topicId=95&type=multi
```

---

## Tool 1: OpicOp - Personal Monitor Bot

### Mô tả
Telegram bot monitor ví whale trên Opinion.trade, thông báo khi có trade mới.

### Stack
- Python, Telegram Bot API (polling), Opinion.trade API
- File: `bot.py`
- Config: `.env` (TELEGRAM_BOT_TOKEN, OPINION_API_KEY)

### State Files
- `state.json`: `monitored_eoa`, `last_seen_id`, `chat_id`
- `daily_summary.json`: trade count theo ngày

### Features
- Monitor EOA: poll mỗi 5 giây, detect trade mới
- Trade alert: format đẹp với hyperlink market
- View Positions: current open positions
- Trade History: 10 trade gần nhất
- Auto-resume: khi restart bot tự monitor lại ví cũ
- Daily summary: gửi lúc 23:58 mỗi ngày

### Trade Alert Format
```
✅ TRADE EXECUTED

Market: [Market Title](url)

Target Wallet: 0x...
• Action: BUY YES ($1B) for $5.00   ← multi market có tên outcome
• Order Price: 44.4 c
```

### Architecture
- `MonitorThread`: daemon thread poll trade mới theo EOA
- `CHAT_STATE`: dict lưu conversation state (waiting_eoa, ...)
- `processed_ids`: set dedup Telegram updates
- Menu dynamic: "Monitor Wallet" khi chưa có ví, "Đổi ví đang monitor" khi đã có

### Lessons Learned
- Poll bằng EOA mới detect được trade mới (smart wallet → empty)
- `fetch_trades` đọc `result.list` không phải `data`
- `do_find_and_monitor` phải chạy trong thread riêng để không block bot
- Telegram callback_data không dùng ký tự `|`, dùng `~` thay thế
- 2 message liên tiếp nhanh có thể bị Telegram drop → gộp thành 1 message

---

## Notes cho Tool mới
- Rate limit Opinion API: chưa rõ, cần test nếu nhiều user
- WebSocket: Opinion chỉ support user channel của chính mình, không subscribe ví người khác
- Không có endpoint `/claim` — detect claim bằng cách so sánh position snapshots
- Không có historical data API — phải query on-chain qua Moralis nếu cần
