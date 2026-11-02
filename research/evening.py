"""
Бэктест «вечернего лага»: Совкомбанк обновляет цену пая в течение основной сессии и
замораживает её после её закрытия (19:00 / раньше 18:50) до утра.
Покупка ночью по цене закрытия основной сессии (+спред), продажа на следующий день,
когда банк обновит цену (берём открытие следующего дня или закрытие его основной сессии, −спред).
Данные — дневная история Мосбиржи: LEGALCLOSEPRICE = закрытие основной сессии, CLOSE = закрытие вечерней.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bot"))
import mp_bot as B
from datetime import date

START = "2024-01-15"
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
print("дней:", len(days), days[0], "…", days[-1])
W = sum(w for _, _, w in weights)


def move(f):
    """Изменение портфеля топ-10 (веса нормированы на 100%); бумаги со сплитом/без данных пропускаем."""
    tot, ws = 0.0, 0.0
    for t, _, w in weights:
        x = f(t)
        if x is None or abs(x) > 0.2:
            continue
        tot += w * x
        ws += w
    return tot / ws if ws else None


rows = []
for d, nd in zip(days, days[1:]):
    eve = move(lambda t: H[t][d]["CLOSE"] / H[t][d]["LEGALCLOSEPRICE"] - 1 if d in H[t] else None)
    to_open = move(lambda t: H[t][nd]["OPEN"] / H[t][d]["LEGALCLOSEPRICE"] - 1
                   if d in H[t] and nd in H[t] and B.ok(H[t][nd].get("OPEN")) else None)
    to_close = move(lambda t: H[t][nd]["LEGALCLOSEPRICE"] / H[t][d]["LEGALCLOSEPRICE"] - 1 if d in H[t] and nd in H[t] else None)
    if None not in (eve, to_open, to_close):
        rows.append((d, eve, to_open, to_close))

for y in ("2024", "2025", "2026"):
    ys = [r for r in rows if r[0].startswith(y)]
    if ys:
        print(f"{y}: дней {len(ys)}, вечер |движение|>1%: {sum(1 for r in ys if abs(r[1]) > 0.01)}, >2%: {sum(1 for r in ys if abs(r[1]) > 0.02)}")
print("\nКрупнейшие вечерние движения (вечерняя сессия к закрытию основной):")
for d, e, o, c in sorted(rows, key=lambda r: -abs(r[1]))[:15]:
    print(f"  {d}  вечер {e*100:+5.2f}%  к открытию след. дня {o*100:+5.2f}%  к закрытию след. дня {c*100:+5.2f}%")

print("\n=== Стратегия: ночью по замороженной цене банка (= закрытие основной сессии) ± спред ===")
for s in (0.005, 0.01):
    for thr in (0.005, 0.01, 0.015, 0.02):
        for label, k in (("выход на открытии след. дня", 2), ("выход на закрытии осн. сессии след. дня", 3)):
            buy, sell = [], []
            for r in rows:
                e, x = r[1], r[k]
                if (1 + e) * (1 - s) / (1 + s) - 1 >= thr:
                    buy.append((1 + x) * (1 - s) / (1 + s) - 1)
                elif (1 - s) / ((1 + e) * (1 + s)) - 1 >= thr:
                    sell.append((1 - s) / ((1 + x) * (1 + s)) - 1)
            def fmt(v, lab):
                if not v:
                    return f"{lab}: 0"
                return (f"{lab}: {len(v):2d}, в плюс {sum(1 for z in v if z > 0):2d}, "
                        f"средне {sum(v)/len(v)*100:+5.2f}%, сумма {sum(v)*100:+6.1f}%")
            print(f"  спред ±{s*100:.1f}% порог {thr*100:.1f}% | {label:40s} | {fmt(buy, 'покупка')} | {fmt(sell, 'выход')}")
