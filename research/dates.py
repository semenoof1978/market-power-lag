"""Вечерний гэп в конкретные даты: eve = вечерняя сессия к закрытию основной, потом — к открытию/закрытию следующего дня."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bot"))
import mp_bot as B
from datetime import date

DATES = os.environ.get("DATES", "").split(",")
START = "2025-03-01"
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

H = {t: hist(t) for t, _, _ in weights}
days = sorted(H["SBER"])

def move(f):
    tot, ws = 0.0, 0.0
    for t, _, w in weights:
        x = f(t)
        if x is None or abs(x) > 0.2:
            continue
        tot += w * x; ws += w
    return tot / ws if ws else None

def row(d, nd):
    eve = move(lambda t: H[t][d]["CLOSE"] / H[t][d]["LEGALCLOSEPRICE"] - 1 if d in H[t] else None)
    op = move(lambda t: H[t][nd]["OPEN"] / H[t][d]["LEGALCLOSEPRICE"] - 1 if d in H[t] and nd in H[t] and B.ok(H[t][nd].get("OPEN")) else None) if nd else None
    cl = move(lambda t: H[t][nd]["LEGALCLOSEPRICE"] / H[t][d]["LEGALCLOSEPRICE"] - 1 if d in H[t] and nd in H[t] else None) if nd else None
    return eve, op, cl

f = lambda v: "  n/a " if v is None else f"{v*100:+6.2f}%"
print("дата(осн. сессия)  вечер после неё | к открытию след.дня | к закрытию след.дня | след.день")
for D in DATES:
    D = D.strip()
    if D not in days:
        print(D, "— нет торгов/данных"); continue
    i = days.index(D); nd = days[i + 1] if i + 1 < len(days) else None
    e, o, c = row(D, nd)
    print(f"{D}   {f(e)}   {f(o)}   {f(c)}   {nd}")
