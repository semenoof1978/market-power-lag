"""Сверка: база 30.10 (дневная история) против часовых свечей, и текущие цены."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bot"))
import mp_bot as B
from datetime import datetime
now = datetime.now(B.MSK)
day = now.date().isoformat()
navs, weights, _, _ = B.load_fund()
d0 = navs[0][0]
tick = [t for t, _, _ in weights]
hist, idx, tr = B.load_history(tick, "2026-10-25", day)
cur, idx_now, upd, tr_now = B.load_market(tick)
print("upd", upd, "IMOEX hist", idx.get(d0), "now", idx_now, "MCFTRR hist", tr.get(d0), "now", tr_now)
for t in tick:
    j = B.iss_json(f"{B.ISS}/engines/stock/markets/shares/boards/TQBR/securities/{t}/candles.json?iss.meta=off&interval=60&from={d0}&till={day}")
    rows = B.iss_rows(j, "candles")
    c30 = [r for r in rows if r["begin"][:10] == d0]
    main = [r["close"] for r in c30 if r["begin"][11:13] <= "18"]
    raw = B.iss_json(f"{B.ISS}/history/engines/stock/markets/shares/boards/TQBR/securities/{t}.json?iss.meta=off&iss.only=history&from={d0}&till={day}&history.columns=TRADEDATE,CLOSE,LEGALCLOSEPRICE,WAPRICE")
    hr = B.iss_rows(raw, "history")
    print(t, "hist", [(r["TRADEDATE"], r["CLOSE"], r["LEGALCLOSEPRICE"], r["WAPRICE"]) for r in hr],
          "| candle 30.10 main", main[-1] if main else None, "last", c30[-1]["close"] if c30 else None, "| now", cur.get(t))
