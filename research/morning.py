"""Утреннее окно: банк заморожен с 19:00 до ~10:00, а рынок торгует вечером и с 06:50.
Сигнал по цене топ-10 в 09:00 (утренняя сессия) против вечернего (23:50) — и результат к 10:00."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bot"))
import mp_bot as B
from datetime import date
from collections import defaultdict

START = os.environ.get("START", "2025-01-01")
_, weights, _, _ = B.load_fund()
till = date.today().isoformat()

def hist(t):
    out, start = {}, 0
    while True:
        j = B.iss_json(f"{B.ISS}/history/engines/stock/markets/shares/boards/TQBR/securities/{t}.json"
                       f"?iss.meta=off&iss.only=history&from={START}&till={till}&start={start}"
                       f"&history.columns=TRADEDATE,OPEN,CLOSE,LEGALCLOSEPRICE")
        rows = B.iss_rows(j, "history")
        for r in rows:
            if B.ok(r.get("LEGALCLOSEPRICE")) and B.ok(r.get("CLOSE")):
                out[r["TRADEDATE"]] = r
        if len(rows) < 100:
            return out
        start += len(rows)

def candles(t):
    out, start = {}, 0
    while True:
        j = B.iss_json(f"{B.ISS}/engines/stock/markets/shares/boards/TQBR/securities/{t}/candles.json"
                       f"?iss.meta=off&from={START}&till={till}&interval=60&start={start}")
        rows = B.iss_rows(j, "candles")
        for r in rows:
            out[r["begin"]] = r["close"]
        if len(rows) < 500:
            return out
        start += len(rows)

H = {t: hist(t) for t, _, _ in weights}
C = {t: candles(t) for t, _, _ in weights}
days = sorted(H["SBER"])
hours = defaultdict(int)
for b in C["SBER"]:
    hours[b[11:13]] += 1
print("свечи SBER по часам начала:", dict(sorted(hours.items())))
print("дней:", len(days), days[0], "…", days[-1])

def px(t, day, hh):
    return C[t].get(f"{day} {hh}:00:00")

def move(f):
    tot = ws = 0.0
    for t, _, w in weights:
        x = f(t)
        if x is None or abs(x) > 0.2:
            continue
        tot += w * x; ws += w
    return tot / ws if ws else None

rows = []
for d, nd in zip(days, days[1:]):
    F = lambda t: H[t][d]["LEGALCLOSEPRICE"] if d in H[t] else None
    e = move(lambda t: H[t][d]["CLOSE"] / F(t) - 1 if F(t) else None)
    m09 = move(lambda t: px(t, nd, "08") / F(t) - 1 if F(t) and px(t, nd, "08") else None)
    r10 = move(lambda t: px(t, nd, "09") / F(t) - 1 if F(t) and px(t, nd, "09") else None)
    op = move(lambda t: H[t][nd]["OPEN"] / F(t) - 1 if F(t) and nd in H[t] and B.ok(H[t][nd].get("OPEN")) else None)
    if None not in (e, m09, r10):
        rows.append((nd, e, m09, r10, op))
print("дней с полными данными:", len(rows))

def thr_run(sig_idx, s, thr, k=3):
    buy, sell = [], []
    for r in rows:
        x = r[sig_idx]
        if (1 + x) * (1 - s) / (1 + s) - 1 >= thr:
            buy.append((1 + r[k]) * (1 - s) / (1 + s) - 1)
        elif (1 - s) / ((1 + x) * (1 + s)) - 1 >= thr:
            sell.append((1 - s) / ((1 + r[k]) * (1 + s)) - 1)
    return buy, sell

def f(v, lab):
    if not v: return f"{lab}: 0"
    return f"{lab}: {len(v):2d}, в плюс {sum(1 for z in v if z>0):2d}, средне {sum(v)/len(v)*100:+5.2f}%, сумма {sum(v)*100:+6.1f}%"

print("\nСигнал по вечеру (23:50), результат — цена в 10:00 | сигнал по 09:00 (утро), результат — цена в 10:00")
for s in (0.005, 0.01):
    for thr in (0.005, 0.01, 0.015):
        be, se = thr_run(1, s, thr)
        bm, sm = thr_run(2, s, thr)
        print(f" спред ±{s*100:.1f}% порог {thr*100:.1f}% | вечер: {f(be,'куп')} / {f(se,'прод')} || утро09: {f(bm,'куп')} / {f(sm,'прод')}")

import statistics as st
big = [r for r in rows if abs(r[2]) > 0.01]
print("\nдней |движение к 09:00| >1%:", len(big), "; >2%:", sum(1 for r in rows if abs(r[2]) > 0.02))
print("из них вечер (23:50) уже >1%:", sum(1 for r in big if abs(r[1]) > 0.01))
dm = [r[2] - r[1] for r in rows]
print("движение между 23:50 и 09:00 (утренняя сессия): ст.откл. %.2f%%, |>0.5%%| дней: %d" % (st.pstdev(dm)*100, sum(1 for x in dm if abs(x) > 0.005)))
print("\nпримеры, где утро добавило сигнал (вечер <1%, 09:00 ≥1%):")
for r in rows:
    if abs(r[1]) < 0.01 and abs(r[2]) >= 0.01:
        print(f"  {r[0]} вечер {r[1]*100:+.2f}%  09:00 {r[2]*100:+.2f}%  10:00 {r[3]*100:+.2f}%")
