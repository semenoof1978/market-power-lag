"""Walk-forward тест: даёт ли «технический» прогноз хоть какую-то пользу сверх известного вечера.
Прокси фонда — взвешенное топ-10 (как в evening.py). Целевые величины:
  A) G = ночной гэп: открытие следующего дня к закрытию основной сессии (по данным на 19:00, без вечера);
  B) Y = G − e: то, что случится ПОСЛЕ вечера (по данным на 23:50, с вечерним движением e).
Признаки: доходности 1/3/5/10/20 дней, волатильность 10д, отклонение от MA20, внутридневное движение, доля растущих дней,
прошлые гэп и вечер."""
import os, sys, math
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bot"))
import mp_bot as B
import numpy as np
from datetime import date

START = "2023-12-01"
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
            if B.ok(r.get("LEGALCLOSEPRICE")) and B.ok(r.get("CLOSE")) and B.ok(r.get("OPEN")):
                out[r["TRADEDATE"]] = r
        if len(rows) < 100:
            return out
        start += len(rows)

H = {t: hist(t) for t, _, _ in weights}
days = sorted(H["SBER"])

def move(f):
    tot = ws = 0.0
    for t, _, w in weights:
        x = f(t)
        if x is None or abs(x) > 0.2:
            continue
        tot += w * x; ws += w
    return tot / ws if ws else None

n = len(days)
ret = [None] * n; intr = [None] * n; eve = [None] * n; gap = [None] * n
for i, d in enumerate(days):
    L = lambda t, k=d: H[t][k]["LEGALCLOSEPRICE"] if k in H[t] else None
    if i > 0:
        p = days[i - 1]
        ret[i] = move(lambda t: L(t) / L(t, p) - 1 if L(t) and L(t, p) else None)
    intr[i] = move(lambda t: L(t) / H[t][d]["OPEN"] - 1 if L(t) and d in H[t] else None)
    eve[i] = move(lambda t: H[t][d]["CLOSE"] / L(t) - 1 if L(t) else None)
    if i + 1 < n:
        nd = days[i + 1]
        gap[i] = move(lambda t: H[t][nd]["OPEN"] / L(t) - 1 if L(t) and nd in H[t] else None)

rows = []  # (idx, features, e, G)
for i in range(25, n - 1):
    if None in (ret[i], intr[i], eve[i], gap[i], gap[i - 1], eve[i - 1]):
        continue
    r = [ret[j] if ret[j] is not None else 0.0 for j in range(i - 24, i + 1)]
    cum = lambda k: float(np.prod([1 + x for x in r[-k:]]) - 1)
    lvl = np.cumprod([1 + x for x in r])
    ma20 = lvl[-20:].mean()
    f = [r[-1], cum(3), cum(5), cum(10), cum(20), float(np.std(r[-10:])), float(lvl[-1] / ma20 - 1),
         intr[i], sum(1 for x in r[-14:] if x > 0) / 14 - 0.5, gap[i - 1], eve[i - 1]]
    rows.append((i, f, eve[i], gap[i]))

X = np.array([r[1] for r in rows]); E = np.array([r[2] for r in rows]); G = np.array([r[3] for r in rows])
N = len(rows)
print("дней в выборке:", N, days[rows[0][0]], "…", days[rows[-1][0]])
print("G: ст.откл. %.2f%%, |G|>1%%: %d, |G|>2%%: %d" % (G.std() * 100, int((abs(G) > .01).sum()), int((abs(G) > .02).sum())))

def ridge_walk(Xa, y, lam=10.0, start=250, step=20):
    pred = np.full(len(y), np.nan)
    for s in range(start, len(y), step):
        tr = slice(0, s)
        mu, sd = Xa[tr].mean(0), Xa[tr].std(0) + 1e-9
        Z = (Xa[tr] - mu) / sd
        A = Z.T @ Z + lam * np.eye(Z.shape[1])
        b = np.linalg.solve(A, Z.T @ (y[tr] - y[tr].mean()))
        e_ = min(s + step, len(y))
        pred[s:e_] = ((Xa[s:e_] - mu) / sd) @ b + y[tr].mean()
    return pred

def stats(name, y, p, start=250):
    m = ~np.isnan(p)
    yy, pp = y[m], p[m]
    r = float(np.corrcoef(yy, pp)[0, 1])
    r2 = 1 - ((yy - pp) ** 2).sum() / ((yy - yy.mean()) ** 2).sum()
    t = r * math.sqrt(len(yy) - 2) / math.sqrt(max(1e-9, 1 - r * r))
    hit = float((np.sign(yy) == np.sign(pp)).mean())
    print(f"{name}: n={len(yy)}  корр={r:+.3f}  t={t:+.1f}  R²(OOS)={r2*100:+.1f}%  угадали знак={hit*100:.0f}%  ст.откл. прогноза={pp.std()*100:.2f}%")
    return yy, pp

print("\n— A) ночной гэп G по данным на 19:00 (без вечера) —")
pA = ridge_walk(X, G)
yA, ppA = stats("техн. прогноз", G, pA)
print("— B) то, что после вечера (Y = G − e), признаки + вечер e —")
Y = G - E
XB = np.column_stack([X, E])
pB = ridge_walk(XB, Y)
stats("техн. прогноз", Y, pB)
m = ~np.isnan(pB)
print("   для сравнения: вечер e объясняет G: корр=%+.3f, R²=%.1f%%" % (np.corrcoef(E[m], G[m])[0, 1], (1 - ((G[m] - E[m]) ** 2).sum() / ((G[m] - G[m].mean()) ** 2).sum()) * 100))
fullpred = E + np.where(m, pB, 0)
print("   вечер + техн. поправка: ошибка G ст.откл. %.3f%%  против одного вечера %.3f%%" % (np.std((G - fullpred)[m]) * 100, np.std((G - E)[m]) * 100))

print("\n— Торговля по прогнозу A (покупка/продажа ночью, результат = гэп к открытию) —")
mk = ~np.isnan(pA)
for s in (0.005, 0.01):
    for thr in (0.0, 0.005, 0.01):
        buy = [(1 + G[k]) * (1 - s) / (1 + s) - 1 for k in range(N) if mk[k] and (1 + pA[k]) * (1 - s) / (1 + s) - 1 >= thr]
        sel = [(1 - s) / ((1 + G[k]) * (1 + s)) - 1 for k in range(N) if mk[k] and (1 - s) / ((1 + pA[k]) * (1 + s)) - 1 >= thr]
        f = lambda v, l: f"{l}: {len(v):3d}, средне {np.mean(v)*100 if v else 0:+5.2f}%, сумма {sum(v)*100:+6.1f}%"
        print(f" спред ±{s*100:.1f}% порог {thr*100:.1f}% | {f(buy,'куп')} | {f(sel,'прод')}")
print("\n— То же по вечеру e (для сравнения, те же дни) —")
for s in (0.005, 0.01):
    for thr in (0.005, 0.01, 0.015):
        buy = [(1 + G[k]) * (1 - s) / (1 + s) - 1 for k in range(N) if mk[k] and (1 + E[k]) * (1 - s) / (1 + s) - 1 >= thr]
        sel = [(1 - s) / ((1 + G[k]) * (1 + s)) - 1 for k in range(N) if mk[k] and (1 - s) / ((1 + E[k]) * (1 + s)) - 1 >= thr]
        f = lambda v, l: f"{l}: {len(v):3d}, средне {np.mean(v)*100 if v else 0:+5.2f}%, сумма {sum(v)*100:+6.1f}%"
        print(f" спред ±{s*100:.1f}% порог {thr*100:.1f}% | {f(buy,'куп')} | {f(sel,'прод')}")
