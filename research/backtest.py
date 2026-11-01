#!/usr/bin/env python3
"""
Бэктест бота «Лаг Маркет Пауэр» на истории фонда.

Для каждой пары соседних опубликованных стоимостей пая (p → d):
  факт   = NAV_d / NAV_p − 1
  модель = изменение по ценам закрытия Мосбиржи за тот же интервал
Котировка банка вечером дня d = NAV_p ± спред (цена ещё вчерашняя).
Сигнал «покупать», если NAV_p·(1+модель)·(1−s) / (NAV_p·(1+s)) − 1 ≥ порог; реальный итог считается по факту NAV_d.
Зеркально «продавать».

Запускается на GitHub Actions (там доступны Мосбиржа и сайт УК), результат — в лог.
"""
import json
import math
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bot"))
import mp_bot as B  # noqa: E402

FEE = 0.0295  # расходы фонда в год
START = "2024-01-15"


def paged(url_base, table):
    rows, start = [], 0
    while True:
        j = B.iss_json(f"{url_base}&start={start}")
        part = B.iss_rows(j, table)
        rows += part
        if len(part) < 100:
            return rows
        start += len(part)


def history(kind, secid, frm, till):
    if kind == "index":
        base = (f"{B.ISS}/history/engines/stock/markets/index/securities/{secid}.json"
                f"?iss.meta=off&iss.only=history&from={frm}&till={till}&history.columns=TRADEDATE,CLOSE")
    else:
        base = (f"{B.ISS}/history/engines/stock/markets/shares/boards/TQBR/securities/{secid}.json"
                f"?iss.meta=off&iss.only=history&from={frm}&till={till}&history.columns=TRADEDATE,CLOSE,LEGALCLOSEPRICE")
    out = {}
    for r in paged(base, "history"):
        v = r.get("CLOSE") if B.ok(r.get("CLOSE")) else r.get("LEGALCLOSEPRICE")
        if B.ok(v):
            out[r["TRADEDATE"]] = v
    return out


def dividends(secid):
    try:
        raw = B.http_get(f"{B.ISS}/securities/{secid}/dividends.json?iss.meta=off")
        if secid in ("SBER", "LKOH"):
            print(f"  RAW dividends {secid}: {raw[:600]}")
        j = json.loads(raw)
        return [(r["registryclosedate"], r["value"]) for r in B.iss_rows(j, "dividends")
                if r.get("registryclosedate") and B.ok(r.get("value"))]
    except Exception as e:  # noqa: BLE001
        print("no dividends for", secid, e)
        return []


def last_on_or_before(series, d):
    """Цена закрытия на дату d или последняя до неё (бумага могла не торговаться)."""
    keys = [k for k in series if k <= d]
    return series[max(keys)] if keys else None


def stats(errs):
    n = len(errs)
    if not n:
        return "нет данных"
    mae = sum(abs(e) for e in errs) / n
    rmse = math.sqrt(sum(e * e for e in errs) / n)
    big = sum(1 for e in errs if abs(e) > 0.01) / n
    return f"дней {n:4d} | средняя ошибка {mae*100:5.2f}% | RMSE {rmse*100:5.2f}% | ошибка >1%: {big*100:4.1f}% дней"


def main():
    text = B.page_text(B.http_get(B.UK_URL, timeout=45, tries=4))
    navs = sorted((d, n) for d, n in B.parse_navs(text) if d >= START)
    w = B.parse_weights(text)
    weights = w[0] if w else B.DEF_WEIGHTS
    print(f"NAV: {len(navs)} значений, {navs[0][0]} … {navs[-1][0]}")
    print("Веса (текущие, с сайта УК):", ", ".join(f"{t} {x:.2f}" for t, _, x in weights))

    till = date.today().isoformat()
    frm = (date.fromisoformat(START) - timedelta(days=10)).isoformat()
    px = {t: history("share", t, frm, till) for t, _, _ in weights}
    for t in px:
        print(f"  {t}: {len(px[t])} дней истории")
    idx = {k: history("index", k, frm, till) for k in ("IMOEX", "MCFTRR")}
    for k in idx:
        print(f"  {k}: {len(idx[k])} дней истории")
    divs = {t: dividends(t) for t in px}
    trade_days = sorted(idx["IMOEX"])

    def exdate(reg):
        """С T+1 первый день без дивиденда = день закрытия реестра (или следующий торговый)."""
        nxt = [d for d in trade_days if d >= reg]
        return nxt[0] if nxt else reg

    ex = {t: [(exdate(r), v) for r, v in divs[t]] for t in divs}
    print("Дивиденды в периоде:", sum(1 for t in ex for d, _ in ex[t] if d >= START))

    def r_share(t, p, d, with_div, clip=False):
        a, b = last_on_or_before(px[t], p), last_on_or_before(px[t], d)
        if not (B.ok(a) and B.ok(b)) or not px[t] or min(px[t]) > p:
            return None
        div = sum(v for e, v in ex[t] if p < e <= d) if with_div else 0
        r = (b + div) / a - 1
        if clip and abs(r) > 0.20:      # сплит/консолидация или ошибка данных — берём как индекс
            return None
        return r

    def r_idx(k, p, d):
        a, b = last_on_or_before(idx[k], p), last_on_or_before(idx[k], d)
        return b / a - 1 if B.ok(a) and B.ok(b) else None

    models = {
        "A топ-10 + IMOEX (как сейчас)": lambda p, d: B.fund_move(weights, lambda t: r_share(t, p, d, False), r_idx("IMOEX", p, d))[0],
        "B топ-10 + IMOEX + дивиденды": lambda p, d: B.fund_move(weights, lambda t: r_share(t, p, d, True), r_idx("IMOEX", p, d))[0],
        "C топ-10 + MCFTRR + дивиденды": lambda p, d: B.fund_move(weights, lambda t: r_share(t, p, d, True), r_idx("MCFTRR", p, d))[0],
        "F топ-10 + MCFTRR + дивиденды + фильтр сплитов": lambda p, d: B.fund_move(weights, lambda t: r_share(t, p, d, True, True), r_idx("MCFTRR", p, d))[0],
        "D только IMOEX": lambda p, d: r_idx("IMOEX", p, d),
        "E только MCFTRR": lambda p, d: r_idx("MCFTRR", p, d),
    }

    pairs = []
    for (p, np_), (d, nd) in zip(navs, navs[1:]):
        if p not in idx["IMOEX"] or d not in idx["IMOEX"]:
            continue
        days = (date.fromisoformat(d) - date.fromisoformat(p)).days
        fact = nd / np_ - 1
        if abs(fact) > 0.15:
            continue
        row = {"p": p, "d": d, "np": np_, "nd": nd, "fact": fact, "days": days}
        for name, f in models.items():
            m = f(p, d)
            row[name] = None if m is None else (1 + m) * (1 - FEE * days / 365) - 1
        pairs.append(row)
    print(f"\nПар дней для проверки: {len(pairs)} ({pairs[0]['p']} … {pairs[-1]['d']})")

    # ---------- точность моделей по годам
    print("\n=== ТОЧНОСТЬ МОДЕЛЕЙ (модель − факт) ===")
    for name in models:
        print(f"\n{name}")
        for label, sel in (("всё время", lambda r: True), ("2024", lambda r: r["d"] < "2025"),
                           ("2025", lambda r: "2025" <= r["d"] < "2026"), ("2026", lambda r: r["d"] >= "2026"),
                           ("последние 60 дней", lambda r: r in pairs[-60:])):
            errs = [r[name] - r["fact"] for r in pairs if sel(r) and r[name] is not None]
            print(f"  {label:18s} {stats(errs)}")

    # ---------- стратегия
    print("\n=== СТРАТЕГИЯ: вечером дня d по котировке NAV_p ± спред ===")
    for name in models:
        print(f"\n{name}")
        for s in (0.005, 0.01):
            for thr in (0.01, 0.015, 0.02, 0.03):
                res = {"buy": [], "sell": []}
                for r in pairs:
                    m = r[name]
                    if m is None:
                        continue
                    est = r["np"] * (1 + m)
                    ask, bid = r["np"] * (1 + s), r["np"] * (1 - s)
                    if est * (1 - s) / ask - 1 >= thr:
                        res["buy"].append(r["nd"] * (1 - s) / ask - 1)
                    elif bid / (est * (1 + s)) - 1 >= thr:
                        res["sell"].append(bid / (r["nd"] * (1 + s)) - 1)
                parts = []
                for k, lab in (("buy", "покупка"), ("sell", "выход")):
                    v = res[k]
                    if v:
                        win = sum(1 for x in v if x > 0)
                        parts.append(f"{lab}: {len(v):2d} сигн., в плюс {win:2d}, средне {sum(v)/len(v)*100:+5.2f}%, сумма {sum(v)*100:+6.1f}%")
                    else:
                        parts.append(f"{lab}: 0 сигн.")
                print(f"  спред ±{s*100:.1f}% порог {thr*100:.1f}% | " + " | ".join(parts))

    # ---------- идеальная модель: сколько вообще было возможностей
    print("\n=== ПОТОЛОК: если бы модель знала факт точно ===")
    for s in (0.005, 0.01):
        for thr in (0.01, 0.02, 0.03):
            b = [r["fact"] for r in pairs if (1 + r["fact"]) * (1 - s) / (1 + s) - 1 >= thr]
            sl = [r["fact"] for r in pairs if (1 - s) / ((1 + r["fact"]) * (1 + s)) - 1 >= thr]
            print(f"  спред ±{s*100:.1f}% порог {thr*100:.0f}%: дней для покупки {len(b)}, для выхода {len(sl)}")

    # ---------- крупнейшие ошибки текущей модели
    name = "A топ-10 + IMOEX (как сейчас)"
    worst = sorted((r for r in pairs if r[name] is not None), key=lambda r: -abs(r[name] - r["fact"]))[:15]
    print(f"\n=== 15 крупнейших ошибок модели A ===")
    for r in worst:
        evs = [f"{t} {v}" for t in ex for e, v in ex[t] if r["p"] < e <= r["d"]]
        per = ", ".join(f"{t} {x*100:+.1f}%" for t in px for x in [r_share(t, r["p"], r["d"], False)] if x is not None and abs(x) > 0.03)
        print(f"  {r['p']}→{r['d']} факт {r['fact']*100:+6.2f}% модель A {r[name]*100:+6.2f}% "
              f"B {r['B топ-10 + IMOEX + дивиденды']*100:+6.2f}% E {(r['E только MCFTRR'] or 0)*100:+6.2f}% "
              f"{'дивиденды: ' + ', '.join(evs) if evs else ''} | бумаги >3%: {per}")


if __name__ == "__main__":
    main()
