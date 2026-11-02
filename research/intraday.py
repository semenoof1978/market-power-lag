"""Расчётная стоимость пая по часам за сегодня (свечи 1 час Мосбиржи) — сравнить с ценами Совкомбанка."""
import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bot"))
import mp_bot as B
from datetime import datetime
now = datetime.now(B.MSK)
day = now.date().isoformat()
navs, weights, _, _ = B.load_fund()
d0, nav0 = navs[0]
print("NAV", d0, nav0)
def candles(path, secid):
    j = B.iss_json(f"{B.ISS}/engines/stock/markets/{path}/securities/{secid}/candles.json?iss.meta=off&interval=60&from={d0}&till={day}")
    return [(r["begin"], r["close"]) for r in B.iss_rows(j, "candles")]
def close_on(rows, d):
    xs = [c for b, c in rows if b[:10] == d]
    return xs[-1] if xs else None
series = {t: candles("shares/boards/TQBR", t) for t, _, _ in weights}
mc = candles("index", "MCFTRR"); im = candles("index", "IMOEX")
idx_rows = mc if mc else im
base = {t: close_on(series[t], d0) for t in series}
ibase = close_on(idx_rows, d0)
hours = sorted({b for b, _ in idx_rows if b[:10] > d0})
print("hour            index%   fund%   calc NAV")
for h in hours:
    def last(rows):
        xs = [c for b, c in rows if b <= h]
        return xs[-1] if xs else None
    ri = last(idx_rows) / ibase - 1
    mv, _ = B.fund_move(weights, lambda t: (last(series[t]) / base[t] - 1) if base[t] and last(series[t]) else None, ri)
    print(f"{h}  {ri*100:+6.2f}  {mv*100:+6.2f}  {nav0*(1+mv):7.2f}")
