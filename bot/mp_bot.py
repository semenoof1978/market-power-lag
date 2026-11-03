#!/usr/bin/env python3
"""
Бот «Лаг Маркет Пауэр».

Раз в час:
  1. берёт стоимость пая и топ-10 фонда с сайта УК «Контрада Капитал»;
  2. берёт цены акций и индекс МосБиржи с iss.moex.com;
  3. считает расчётную стоимость пая по текущим ценам;
  4. сравнивает с котировкой (стоимость пая ± спред маркет-мейкера) и пишет в Telegram:
     «Покупать» / «Продавать», вечернюю сводку и оповещения о потере связи.

Только стандартная библиотека Python.
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")
UK_URL = "https://contrada-capital.ru/products/opif/marketpower"
ISS = "https://iss.moex.com/iss"

TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
STATE_FILE = os.environ.get("STATE_FILE", "state/state.json")
MODE = (os.environ.get("MODE") or "auto").strip()          # auto | summary


def env_float(name, default):
    try:
        return float(str(os.environ.get(name) or default).replace(",", "."))
    except ValueError:
        return default


THRESHOLD = env_float("THRESHOLD", 3) / 100     # порог сигнала после спреда
SPREAD = env_float("SPREAD", 0.5) / 100         # маркет-мейкер: +0,5% покупка, −0,5% продажа
FUND_FEE = env_float("FUND_FEE", 2.95) / 100    # годовые расходы фонда (УК 2,5% + депозитарий до 0,45%)
QUOTE_LAG = int(env_float("QUOTE_LAG_DAYS", 1)) # котировка = стоимость пая за N-й торговый день назад
SUMMARY_HOUR = int(env_float("SUMMARY_HOUR", 19))
PING_MOVE = env_float("PING_MOVE", 1.5) / 100   # пуш «пришли свежие цены», если расчётная цена ушла на столько
PING_STEP = env_float("PING_STEP", 0.5) / 100    # повторный пуш — только если цена ушла ещё на столько дальше
PING_EVERY_MIN = 10                              # проверяем при каждом запуске (cron — каждые 10 минут, т.е. не реже 15)
REPEAT_HOURS = 3        # повтор действующего сигнала не чаще
FAILS_TO_ALERT = 3      # подряд неудачных проверок (по 10 минут) до оповещения о связи
REMIND_EVERY = 18       # напоминание «связи всё ещё нет» — раз в 3 часа

DEF_WEIGHTS = [("LKOH", "Лукойл", 9.71), ("SVCB", "Совкомбанк", 8.49), ("SBER", "Сбербанк", 8.43),
               ("DOMRF", "ДОМ.РФ", 8.30), ("T", "Т-Технологии", 7.95), ("TATNP", "Татнефть-п", 6.66),
               ("NVTK", "Новатэк", 5.85), ("TRNFP", "Транснефть-п", 4.36), ("SNGSP", "Сургутнефтегаз-п", 3.88),
               ("NLMK", "НЛМК", 3.60)]
DEF_WEIGHTS_DATE = "30 сентября 2026"
NAME_MAP = [("ГАЗПРОМ НЕФТЬ", "SIBN", "Газпром нефть"), ("МТС-БАНК", "MBNK", "МТС-Банк"),
            ("БАНК САНКТ-ПЕТЕРБУРГ", "BSPB", "Банк СПб"), ("НОРИЛЬСКИЙ НИКЕЛЬ", "GMKN", "Норникель"),
            ("МОСКОВСКАЯ БИРЖА", "MOEX", "Мосбиржа"), ("СУРГУТНЕФТЕГАЗ", "SNGSP", "Сургутнефтегаз-п"),
            ("Т-ТЕХНОЛОГИИ", "T", "Т-Технологии"), ("ТРАНСНЕФТЬ", "TRNFP", "Транснефть-п"),
            ("СОВКОМФЛОТ", "FLOT", "Совкомфлот"), ("СОВКОМБАНК", "SVCB", "Совкомбанк"),
            ("СЕВЕРСТАЛЬ", "CHMF", "Северсталь"), ("ГРУППА АСТРА", "ASTR", "Астра"),
            ("ХЭДХАНТЕР", "HEAD", "HeadHunter"), ("ТАТНЕФТЬ", "TATNP", "Татнефть-п"),
            ("СБЕРБАНК", "SBER", "Сбербанк"), ("ИНТЕР РАО", "IRAO", "Интер РАО"), ("РОСНЕФТЬ", "ROSN", "Роснефть"),
            ("СЕЛИГДАР", "SELG", "Селигдар"), ("РУСГИДРО", "HYDR", "РусГидро"), ("АЭРОФЛОТ", "AFLT", "Аэрофлот"),
            ("ПОЗИТИВ", "POSI", "Positive"), ("ФОСАГРО", "PHOR", "ФосАгро"), ("НОВАТЭК", "NVTK", "Новатэк"),
            ("ГАЗПРОМ", "GAZP", "Газпром"), ("ЛУКОЙЛ", "LKOH", "Лукойл"), ("ДОМ.РФ", "DOMRF", "ДОМ.РФ"),
            ("МАГНИТ", "MGNT", "Магнит"), ("ЯНДЕКС", "YDEX", "Яндекс"), ("АЛРОСА", "ALRS", "АЛРОСА"),
            ("РУСАЛ", "RUAL", "Русал"), ("ИКС 5", "X5", "X5"), ("ЛЕНТА", "LENT", "Лента"),
            ("ПОЛЮС", "PLZL", "Полюс"), ("ОЗОН", "OZON", "Озон"), ("НЛМК", "NLMK", "НЛМК"),
            ("ММК", "MAGN", "ММК"), ("АФК", "AFKS", "АФК Система"), ("ЮГК", "UGLD", "ЮГК"),
            ("ВТБ", "VTBR", "ВТБ"), ("МТС", "MTSS", "МТС"), ("X5", "X5", "X5")]


# ---------------------------------------------------------------- helpers
class SourceError(Exception):
    """Источник данных недоступен или вернул не то."""
    def __init__(self, source, detail):
        super().__init__(f"{source}: {detail}")
        self.source = source
        self.detail = detail


def http_get(url, timeout=25, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                "Accept-Language": "ru-RU,ru;q=0.9"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (i + 1))
    raise last


def num(s):
    if s is None:
        return float("nan")
    try:
        return float(re.sub(r"[\s  ]", "", str(s)).replace(",", "."))
    except ValueError:
        return float("nan")


def ok(x):
    return x is not None and x == x and x > 0


def rub(x):
    if not ok(x):
        return "—"
    return f"{x:,.2f}".replace(",", " ").replace(".", ",") + " ₽"


MAIN_CLOSE = 19 * 60          # конец основной сессии (с 14.09.2026; раньше 18:50)


def bank_live(now):
    """Совкомбанк обновляет цену пая, пока идёт утренняя/основная сессия (будни 06:50–19:00, выходные 09:50–19:00).
    После закрытия основной сессии цена замораживается до утра — в это окно и работает лаг."""
    m = now.hour * 60 + now.minute
    start = 6 * 60 + 50 if now.weekday() < 5 else 9 * 60 + 50
    return start <= m < MAIN_CLOSE


def in_session(now):
    """Идёт вечерняя сессия (цены ещё меняются, сигнал предварительный): будни 19:00–23:50 + 15 мин задержки данных."""
    m = now.hour * 60 + now.minute
    return now.weekday() < 5 and MAIN_CLOSE <= m < 24 * 60 + 5


def last_main_close(now):
    """Момент последнего закрытия основной сессии, после которого цена банка заморожена."""
    d = now.replace(hour=MAIN_CLOSE // 60, minute=MAIN_CLOSE % 60, second=0, microsecond=0)
    return d if now >= d else d - timedelta(days=1)


def next_live_start(dt):
    """Когда банк снова начнёт обновлять цену после момента dt."""
    d = dt
    for _ in range(4):
        start = d.replace(hour=6 if d.weekday() < 5 else 9, minute=50, second=0, microsecond=0)
        if start > dt:
            return start
        d = (d + timedelta(days=1)).replace(hour=0, minute=0)
    return dt + timedelta(days=1)


def money(x):
    """Сумма со знаком, 0 — тоже число (rub() для нуля ставит прочерк)."""
    return ("+" if x > 0 else "−" if x < 0 else "") + f"{abs(x):,.2f}".replace(",", " ").replace(".", ",") + " ₽"


def thr_txt():
    return f"порог {THRESHOLD * 100:g}%".replace(".", ",")


def fmt_pct(x):
    return f"{x * 100:.2f}".replace(".", ",") + "%"


def pct(x):
    if x is None or x != x:
        return "—"
    s = f"{x * 100:+.2f}".replace(".", ",")
    return s + "%"


def ru_date(iso):
    return f"{iso[8:10]}.{iso[5:7]}.{iso[0:4]}"


def iss_rows(j, name):
    t = j.get(name) or {}
    cols = t.get("columns") or []
    return [dict(zip(cols, r)) for r in t.get("data") or []]


# ---------------------------------------------------------------- fund site
def page_text(html):
    t = re.sub(r"<[^>]+>", " ", html)
    t = re.sub(r"&nbsp;|&#160;|\\u00a0| | |\\u202f", " ", t)
    return t.replace("&quot;", '"').replace("&#34;", '"').replace("&laquo;", '"').replace("&raquo;", '"').replace("&amp;", "&")


def parse_navs(text):
    out, seen = [], set()
    for m in re.finditer(r"(\d{2})\.(\d{2})\.(20\d{2})", text):
        iso = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
        if iso in seen:
            continue
        tail = text[m.end():m.end() + 260]
        nums = []
        for k in re.finditer(r"[+-]?\d[\d ]*,\d{2}", tail):
            if re.search(r"\d{2}\.\d{2}\.20\d{2}", tail[:k.start()]):
                break
            nums.append(num(k.group(0)))
            if len(nums) == 4:
                break
        if len(nums) >= 4 and nums[0] > 1e6 and 20 < nums[2] < 2000:
            seen.add(iso)
            out.append((iso, nums[2]))
    out.sort(reverse=True)
    return out


def parse_weights(text):
    up = text.upper()
    a = up.find("ТОП-10 ЭМИТЕНТОВ")
    if a < 0:
        return None
    b = up.find("ОТРАСЛЕВАЯ", a)
    seg = up[a:b if b > 0 else a + 6000]
    alts = "|".join(re.escape(x[0]) for x in NAME_MAP)
    res, seen = [], set()
    for m in re.finditer("(" + alts + r")[^%]{0,120}?(\d{1,2}[.,]\d{1,2})\s*%", seg):
        e = next(x for x in NAME_MAP if x[0] == m.group(1))
        if e[1] in seen:
            continue
        seen.add(e[1])
        res.append((e[1], e[2], num(m.group(2))))
    total = sum(w for _, _, w in res)
    if len(res) < 5 or not 35 <= total <= 100:
        return None
    d = re.search(r"АКТУАЛЬНЫ\s+ПО\s+СОСТОЯНИЮ\s+НА\s+(\d{1,2}\s+[А-ЯЁ]+\s+\d{4}|\d{2}\.\d{2}\.\d{4})", up[a:])
    return res, (re.sub(r"\s+", " ", d.group(1)).lower() if d else "дата не указана")


def load_fund():
    try:
        text = page_text(http_get(UK_URL, timeout=45, tries=4))
    except Exception as e:  # noqa: BLE001
        raise SourceError("сайт УК", f"нет связи ({type(e).__name__})")
    navs = parse_navs(text)
    if not navs:
        raise SourceError("сайт УК", "не нашлась таблица стоимости пая (могли поменять страницу)")
    w = parse_weights(text)
    if w:
        return navs, w[0], w[1], "сайт УК"
    return navs, DEF_WEIGHTS, DEF_WEIGHTS_DATE, "сохранённый состав"


# ---------------------------------------------------------------- MOEX
def iss_json(url):
    try:
        return json.loads(http_get(url))
    except Exception as e:  # noqa: BLE001
        raise SourceError("Мосбиржа", f"нет связи ({type(e).__name__})")


LEGAL = {}      # закрытия основной сессии по дням: {тикер: {дата: цена}}
LCLOSE = {}     # закрытие основной сессии текущего торгового дня из marketdata


def load_history(tickers, frm, till):
    hist = {}
    for t in tickers:
        j = iss_json(f"{ISS}/history/engines/stock/markets/shares/boards/TQBR/securities/{t}.json"
                     f"?iss.meta=off&iss.only=history&from={frm}&till={till}&history.columns=TRADEDATE,CLOSE,LEGALCLOSEPRICE")
        rows = iss_rows(j, "history")
        hist[t] = {r["TRADEDATE"]: (r["CLOSE"] if ok(r.get("CLOSE")) else r.get("LEGALCLOSEPRICE")) for r in rows}
        LEGAL[t] = {r["TRADEDATE"]: r["LEGALCLOSEPRICE"] for r in rows if ok(r.get("LEGALCLOSEPRICE"))}
    j = iss_json(f"{ISS}/history/engines/stock/markets/index/securities/IMOEX.json"
                 f"?iss.meta=off&iss.only=history&from={frm}&till={till}&history.columns=TRADEDATE,CLOSE")
    idx = {r["TRADEDATE"]: r["CLOSE"] for r in iss_rows(j, "history") if ok(r.get("CLOSE"))}
    if not idx:
        raise SourceError("Мосбиржа", "пустая история индекса")
    try:
        j = iss_json(f"{ISS}/history/engines/stock/markets/index/securities/MCFTRR.json"
                     f"?iss.meta=off&iss.only=history&from={frm}&till={till}&history.columns=TRADEDATE,CLOSE")
        tr = {r["TRADEDATE"]: r["CLOSE"] for r in iss_rows(j, "history") if ok(r.get("CLOSE"))}
    except SourceError:
        tr = {}
    return hist, idx, tr


def load_market(tickers):
    j = iss_json(f"{ISS}/engines/stock/markets/shares/boards/TQBR/securities.json"
                 f"?iss.meta=off&iss.only=marketdata,securities&securities={','.join(tickers)}")
    LCLOSE.clear()
    sec = {r["SECID"]: r for r in iss_rows(j, "securities")}
    cur, upd = {}, None
    for r in iss_rows(j, "marketdata"):
        s = sec.get(r["SECID"], {})
        v = next((x for x in (r.get("LAST"), r.get("LCURRENTPRICE"), r.get("MARKETPRICE"), s.get("PREVPRICE")) if ok(x)), None)
        cur[r["SECID"]] = v
        if ok(r.get("LCLOSEPRICE")):
            LCLOSE[r["SECID"]] = r["LCLOSEPRICE"]
        if r.get("UPDATETIME") and (upd is None or r["UPDATETIME"] > upd):
            upd = r["UPDATETIME"]
    j = iss_json(f"{ISS}/engines/stock/markets/index/securities.json?iss.meta=off&iss.only=marketdata&securities=IMOEX,MCFTRR")
    vals = {r.get("SECID"): next((x for x in (r.get("CURRENTVALUE"), r.get("LASTVALUE")) if ok(x)), None)
            for r in iss_rows(j, "marketdata")}
    idx = vals.get("IMOEX")
    if not ok(idx):
        raise SourceError("Мосбиржа", "нет текущего значения индекса")
    return cur, idx, upd, vals.get("MCFTRR")


# ---------------------------------------------------------------- dividends (smart-lab)
SL_PAGE = {"TATNP": "TATN", "SNGSP": "SNGS", "SBERP": "SBER", "MTLRP": "MTLR", "RTKMP": "RTKM", "BANEP": "BANE"}
DIV_ROW = re.compile(r"<td>\s*([A-Z0-9]{1,8})\s*</td>\s*<td>\s*(\d{2}\.\d{2}\.\d{4})\s*</td>\s*"
                     r"<td>\s*(\d{2}\.\d{2}\.\d{4})\s*</td>\s*<td>[^<]*</td>\s*<td>\s*<strong[^>]*>\s*([\d\s.,]+?)\s*</strong>", re.S)


def ru2iso(s_):
    return f"{s_[6:10]}-{s_[3:5]}-{s_[0:2]}"


def load_dividends(tickers, state, now):
    """Дивиденды бумаг фонда со smart-lab: {тикер: [(последний день с дивидендом T-1, сумма)]}.
    Обновляются раз в 12 часов; при сбое — прошлые данные."""
    cache = state.get("div_cache") or {}
    if cache.get("at") and (now - datetime.fromisoformat(cache["at"])).total_seconds() < 12 * 3600 \
            and set(tickers) <= set(cache.get("checked", [])):
        return {t: [tuple(x) for x in v] for t, v in cache.get("data", {}).items()}
    data, checked = {}, []
    for t in tickers:
        pages = [SL_PAGE.get(t, t)] + (["TRNF"] if t == "TRNFP" else [])
        for page in pages:
            try:
                html = http_get(f"https://smart-lab.ru/q/{page}/dividend/", timeout=20, tries=2)
            except Exception:  # noqa: BLE001
                continue
            rows = [(ru2iso(m.group(2)), num(m.group(4))) for m in DIV_ROW.finditer(html) if m.group(1) == t]
            rows = [(d, v) for d, v in rows if ok(v)]
            if rows:
                data[t] = sorted(set(rows))
                break
        checked.append(t)
    if data or not cache.get("data"):
        state["div_cache"] = {"at": now.isoformat(), "data": data, "checked": checked}
    else:
        data = {t: [tuple(x) for x in v] for t, v in cache["data"].items()}
    print("dividends loaded for", len(data), "tickers")
    return data


CLOSE_CACHE = {}   # {"day": D, "px": {тикер: цена закрытия основной сессии}} — из 10-минутных свечей


def share_at_close(t, day):
    """Цена бумаги на закрытии основной сессии дня day по 10-минутным свечам (когда LCLOSEPRICE ещё нет)."""
    if CLOSE_CACHE.get("day") != day:
        CLOSE_CACHE.clear()
        CLOSE_CACHE.update(day=day, px={})
    if t in CLOSE_CACHE["px"]:
        return CLOSE_CACHE["px"][t]
    try:
        j = iss_json(f"{ISS}/engines/stock/markets/shares/boards/TQBR/securities/{t}/candles.json"
                     f"?iss.meta=off&interval=10&from={day}&till={day}")
        rows = [r for r in iss_rows(j, "candles") if r.get("begin", "") < f"{day} {MAIN_CLOSE // 60:02d}:{MAIN_CLOSE % 60:02d}"]
        v = rows[-1]["close"] if rows else None
    except SourceError:
        v = None
    if ok(v):
        CLOSE_CACHE["px"][t] = v
    return v


def idx_at_close(day):
    """IMOEX на закрытии основной сессии дня day (по 10-минутным свечам)."""
    try:
        j = iss_json(f"{ISS}/engines/stock/markets/index/securities/IMOEX/candles.json"
                     f"?iss.meta=off&interval=10&from={day}&till={day}")
        rows = [r for r in iss_rows(j, "candles") if r.get("begin", "") < f"{day} {MAIN_CLOSE // 60:02d}:{MAIN_CLOSE % 60:02d}"]
        return rows[-1]["close"] if rows else None
    except SourceError:
        return None


FUND_ISIN = "RU000A107G30"


def load_fund_quote(state):
    """Котировки маркет-мейкера по паю на Мосбирже: {"bid", "offer", "time", "board"} или None."""
    try:
        loc = state.get("fund_board")
        if loc and loc.get("missing_at"):
            if (datetime.now(MSK) - datetime.fromisoformat(loc["missing_at"])).total_seconds() < 86400:
                return None
            loc = None
        if not loc:
            j = iss_json(f"{ISS}/securities.json?iss.meta=off&iss.only=securities&q={FUND_ISIN}")
            rows = [r for r in iss_rows(j, "securities") if (r.get("isin") or r.get("secid")) == FUND_ISIN] \
                or iss_rows(j, "securities")
            if not rows:
                print("fund quote: security not found on MOEX")
                state["fund_board"] = {"missing_at": datetime.now(MSK).isoformat()}
                return None
            secid = rows[0]["secid"]
            j = iss_json(f"{ISS}/securities/{secid}.json?iss.meta=off&iss.only=boards")
            boards = [r for r in iss_rows(j, "boards") if r.get("is_traded") == 1]
            if not boards:
                print("fund quote: no traded boards")
                state["fund_board"] = {"missing_at": datetime.now(MSK).isoformat()}
                return None
            bd = sorted(boards, key=lambda r: -(r.get("is_primary") or 0))[0]
            loc = {"secid": secid, "engine": bd["engine"], "market": bd["market"], "board": bd["boardid"]}
            state["fund_board"] = loc
        j = iss_json(f"{ISS}/engines/{loc['engine']}/markets/{loc['market']}/boards/{loc['board']}"
                     f"/securities/{loc['secid']}.json?iss.meta=off&iss.only=marketdata")
        r = (iss_rows(j, "marketdata") or [{}])[0]
        bid, offer = r.get("BID"), r.get("OFFER")
        print("fund quote:", loc["board"], loc["secid"], "bid", bid, "offer", offer, "last", r.get("LAST"))
        if ok(bid) and ok(offer) and offer >= bid and offer / bid < 1.06:
            return {"bid": bid, "offer": offer, "time": (r.get("UPDATETIME") or r.get("TIME") or "")[:5],
                    "board": loc["board"]}
    except Exception as e:  # noqa: BLE001
        print("fund quote unavailable:", e)
    return None


# ---------------------------------------------------------------- model
def fund_move(weights, ret_of, r_idx):
    """Изменение стоимости пая: Σ вес × доходность бумаги + остаток × индекс."""
    w_sum, move, missing = 0.0, 0.0, []
    for t, n, w in weights:
        w /= 100
        w_sum += w
        r = ret_of(t)
        if r is None:
            missing.append(n)
            r = r_idx
        move += w * r
    move += max(0.0, 1 - w_sum) * r_idx
    return move, missing


def calculate(now, quote, fund, exq=None, spread=SPREAD, divs=None):
    """quote — цены от пользователя: сохранённые {"buy","sell","nav","date","spread"}
    или только что присланные {"new": True, "buy", "sell", "prev"}; None — оценивать самому.
    fund — (navs, weights, w_date, w_src) с сайта УК или из запаса."""
    navs, weights, w_date, w_src = fund
    tickers = [t for t, _, _ in weights]
    d0, nav0 = navs[0]
    today = now.date().isoformat()
    frm = (date.fromisoformat(d0) - timedelta(days=12)).isoformat()
    hist, idx_hist, tr_hist = load_history(tickers, frm, today)
    cur, idx_now, upd, tr_now = load_market(tickers)
    if d0 not in idx_hist:
        raise SourceError("Мосбиржа", f"нет закрытия индекса за {ru_date(d0)}")
    trading = sorted(d for d in idx_hist if d < today)
    nav_map = dict(navs)

    def rest_ret(a, idx_b, tr_b):
        """Остальная часть портфеля: MCFTRR (полная доходность, учитывает дивиденды), иначе IMOEX.
        По бэктесту 2024–2026 так точнее."""
        if ok(tr_b) and ok(tr_hist.get(a)):
            return tr_b / tr_hist[a] - 1
        return idx_b / idx_hist[a] - 1

    divs = divs or {}

    def move_between(a, price_b, idx_b, tr_b=None, b_date=None):
        """Изменение пая от закрытия дня a до цен price_b(t) и индексов на момент b.
        Дивиденд, отрезанный между a и b, возвращаем в доходность бумаги: стоимость пая из-за него не падает."""
        b_date = b_date or today

        def r(t):
            pa, pb = hist.get(t, {}).get(a), price_b(t)
            if not (ok(pa) and ok(pb)):
                return None
            div = sum(v for t1, v in divs.get(t, []) if a <= t1 < b_date)
            x = (pb + div) / pa - 1
            return None if abs(x) > 0.20 else x      # сплит/консолидация (как у T и TRNFP) — берём как индекс
        return fund_move(weights, r, rest_ret(a, idx_b, tr_b))

    def fee(a, b):
        return 1 - FUND_FEE * (date.fromisoformat(b) - date.fromisoformat(a)).days / 365

    # ================= логика лага (подтверждена сделками 2025 года и ценами банка 02.11.2026) =================
    # Днём Совкомбанк обновляет цену пая вслед за рынком — лага нет.
    # После закрытия основной сессии цена замораживается до утра, а вечерняя сессия продолжает двигать акции.
    # Сигнал = движение акций фонда на вечерней сессии относительно закрытия основной.
    live = bank_live(now)
    close_at = last_main_close(now)
    D = close_at.date().isoformat()

    def same_window(at_iso):
        if not at_iso:
            return False
        at = datetime.fromisoformat(at_iso)
        if live:
            return bank_live(at) and (now - at).total_seconds() < 3600
        return at >= close_at

    # --- живая расчётная стоимость пая: от последней стоимости на сайте УК по текущим ценам
    move_now, missing = move_between(d0, lambda t: cur.get(t), idx_now, None)
    calc_live = nav0 * (1 + move_now) * fee(d0, today)

    # --- цены, присланные пользователем
    quote_status = None
    if quote and quote.get("new"):
        b, sl, prev = quote.get("buy"), quote.get("sell"), quote.get("prev") or {}
        if prev and same_window(prev.get("at")):          # вторая цена из прошлого сообщения того же окна
            b, sl = b or prev.get("buy"), sl or prev.get("sell")
        spr = (b - sl) / (b + sl) if b and sl else spread
        mid = (b + sl) / 2 if b and sl else (b / (1 + spr) if b else sl / (1 - spr))
        quote = {"buy": b, "sell": sl, "mid": round(mid, 4), "spread": spr, "at": now.isoformat()}
        quote_status = "new"
    elif quote and not same_window(quote.get("at")):
        quote, quote_status = None, "expired"
    s_eff = quote["spread"] if quote else spread

    eve = None
    if live:
        phase, calc = "live", calc_live
        ref = quote["mid"] if quote else calc_live
        q_kind = "user" if quote else "live"
    else:
        phase = "evening" if in_session(now) else "night"

        def main_close(t):
            v = LCLOSE.get(t) if D == today or not LEGAL.get(t, {}).get(D) else None
            v = v if ok(v) else LEGAL.get(t, {}).get(D)
            return v if ok(v) else share_at_close(t, D)

        tot, ws = 0.0, 0.0
        for t, _, w in weights:
            pa, pb = main_close(t), cur.get(t)
            if ok(pa) and ok(pb) and abs(pb / pa - 1) < 0.2:
                tot += w * (pb / pa - 1)
                ws += w
        eve = tot / ws if ws >= 40 else None             # нужна хотя бы половина топ-10 по весу

        if quote:
            ref, q_kind = quote["mid"], "user"
        else:
            # цена банка ≈ стоимость пая по ценам закрытия основной сессии
            ic = idx_at_close(D)
            if D > d0 and ic and all(ok(main_close(t)) for t, _, _ in weights[:5]):
                mv, _ = move_between(d0, main_close, ic, None, D)
                ref = nav0 * (1 + mv) * fee(d0, D)
            else:
                ref = calc_live / (1 + (eve or 0))
            q_kind = "est"
        calc = ref * (1 + (eve or 0))

    if quote:
        ask = quote.get("buy") or ref * (1 + s_eff)
        bid = quote.get("sell") or ref * (1 - s_eff)
    else:
        ask, bid = ref * (1 + s_eff), ref * (1 - s_eff)
    q_date, q_nav, q_src = D, ref, q_kind
    base_d, base_nav, base_src = (D, ref, q_kind) if not live else (d0, nav0, "site")
    expected = D

    check = None
    if len(navs) >= 2:
        (dd, nd), (pp, npp) = navs[0], navs[1]
        if dd in idx_hist and pp in idx_hist:
            mv, _ = move_between(pp, lambda t: hist.get(t, {}).get(dd), idx_hist[dd], tr_hist.get(dd), dd)
            check = {"date": dd, "model": (1 + mv) * fee(pp, dd) - 1, "fact": nd / npp - 1}

    edge_buy = calc * (1 - s_eff) / ask - 1      # купить сейчас, продать после обновления цены банка
    edge_sell = bid / (calc * (1 + s_eff)) - 1   # продать сейчас, откупить после обновления
    signal = "none" if live else ("buy" if edge_buy >= THRESHOLD else "sell" if edge_sell >= THRESHOLD else "none")
    return {
        "signal": signal, "calc": calc, "move": move_now, "missing": missing,
        "base_d": base_d, "base_nav": base_nav, "base_src": base_src,
        "d0": d0, "nav0": nav0, "q_date": q_date, "q_nav": q_nav, "q_kind": q_kind,
        "ask": ask, "bid": bid, "edge_buy": edge_buy, "edge_sell": edge_sell,
        "upd": upd, "w_src": w_src, "w_date": w_date,
        "quote": quote, "quote_status": quote_status, "spread": s_eff,
        "q_view": quote, "exq": exq, "check": check, "expected": expected,
        "phase": phase, "eve": eve, "close_at": close_at.isoformat(),
    }


# ---------------------------------------------------------------- messages
def msg_body(c, now):
    base = {"user": f"от твоих цен за {ru_date(c['base_d'])}",
            "exchange": f"от цен пая на Мосбирже (за {ru_date(c['base_d'])})"}.get(c["base_src"], f"к {ru_date(c['base_d'])}")
    exq = c.get("exq")
    if c["q_kind"] == "exchange":
        q = (f"купить по <b>{rub(c['ask'])}</b>, продать по <b>{rub(c['bid'])}</b> (Мосбиржа, {exq['time'] or 'сейчас'})\n"
             f"   стоимость пая в ценах {rub(c['q_nav'])} за {ru_date(c['q_date'])}")
    elif c["q_kind"] == "user":
        qq = c["q_view"]
        b = f"<b>{rub(c['ask'])}</b>" if qq.get("buy") else f"≈ {rub(c['ask'])}"
        sl = f"<b>{rub(c['bid'])}</b>" if qq.get("sell") else f"≈ {rub(c['bid'])}"
        q = (f"купить по {b}, продать по {sl} (прислал ты)\n"
             f"   стоимость пая в ценах {rub(c['q_nav'])} за {ru_date(c['q_date'])}")
    elif c["q_kind"] == "est":
        q = (f"купить ≈ {rub(c['ask'])}, продать ≈ {rub(c['bid'])}\n"
             f"   оценка бота: стоимости пая за {ru_date(c['q_date'])} на сайте УК ещё нет")
    else:
        q = (f"купить ≈ {rub(c['ask'])}, продать ≈ {rub(c['bid'])}\n"
             f"   стоимость пая за {ru_date(c['q_date'])} ± спред")
    lines = [
        f"Расчётная стоимость пая: <b>{rub(c['calc'])}</b>",
        f"   по ценам Мосбиржи на {(c['upd'] or now.strftime('%H:%M'))[:5]} МСК, {pct(c['move'])} {base}",
        f"Фактическая стоимость пая: <b>{rub(c['nav0'])}</b> (сайт УК, {ru_date(c['d0'])})",
        f"Цены пая: {q}",
        f"Выгода с учётом спреда ±{fmt_pct(c['spread'])}: покупка {pct(c['edge_buy'])}, выход {pct(c['edge_sell'])}",
    ]
    if c["q_kind"] == "user" and exq:
        lines.append(f"На Мосбирже сейчас: купить {rub(exq['offer'])}, продать {rub(exq['bid'])}")
    elif c["q_kind"] != "exchange":
        lines.append("Котировок пая на Мосбирже сейчас нет")
    if c["missing"]:
        lines.append("Нет цен по: " + ", ".join(c["missing"]) + " — взял их как индекс.")
    return "\n".join(lines)


def msg_signal(c, now):
    head = {"buy": "🟩🟩🟩 <b>ПОКУПАТЬ</b> 🟩🟩🟩", "sell": "🟥🟥🟥 <b>ПРОДАВАТЬ</b> 🟥🟥🟥"}[c["signal"]]
    head += f" · {thr_txt()}"
    why = ("Акции фонда выросли на вечерней сессии, а цена пая в банке заморожена с 19:00." if c["signal"] == "buy"
           else "Акции фонда упали на вечерней сессии, а цена пая в банке заморожена с 19:00.")
    return (f"{head}\n{why}\n\n{msg_body(c, now)}\n\n"
            f"Утром банк обновит цену. Проверь цену в приложении перед сделкой.")


def msg_summary(c, now):
    state = {"buy": "🟩 ПОКУПАТЬ", "sell": "🟥 ПРОДАВАТЬ", "none": "⬜ ДЕРЖАТЬ"}[c["signal"]]
    tip = "" if c["q_kind"] in ("user", "exchange") else "\n\nДля точного расчёта пришли цены из приложения: «купить по 154,83» и «продать по 153,29»"
    return (f"📋 Сводка {now.strftime('%d.%m %H:%M')} · {state} · {thr_txt()}\n\n"
            f"{msg_body(c, now)}\n\nСостав фонда: {c['w_src']}, на {c['w_date']}.{c.get('fund_note', '')}{tip}")


# ---------------------------------------------------------------- signal card (picture)
FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")


def signal_card(c, now, notes=(), word=None, sub=None, color=None):
    """PNG: крупное слово на зелёном или красном фоне. None, если Pillow нет."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None
    sig = color or c["signal"]
    buy = sig == "buy"
    bg, fg, soft = {"buy": ((30, 140, 80), (255, 255, 255), (214, 240, 224)),
                    "sell": ((196, 52, 44), (255, 255, 255), (250, 222, 219)),
                    "none": ((72, 84, 104), (255, 255, 255), (214, 222, 235))}[sig]
    W, H = 1080, 700 + 50 * len(notes)
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)

    def font(name, size):
        for path in (os.path.join(FONT_DIR, name), f"/usr/share/fonts/truetype/dejavu/{name}"):
            if os.path.exists(path):
                return ImageFont.truetype(path, size)
        return ImageFont.load_default()

    big, mid, small = font("DejaVuSans-Bold.ttf", 150), font("DejaVuSans-Bold.ttf", 52), font("DejaVuSans.ttf", 38)
    word = word or {"buy": "ПОКУПАТЬ", "sell": "ПРОДАВАТЬ", "none": "ДЕРЖАТЬ"}[sig]
    while d.textlength(word, font=big) > W - 100 and big.size > 60:
        big = font("DejaVuSans-Bold.ttf", big.size - 10)
    d.text((W / 2, 150), word, font=big, fill=fg, anchor="mm")
    tag = {"live": "БАНК ОБНОВЛЯЕТ ЦЕНУ · основная сессия",
           "evening": "ПРЕДВАРИТЕЛЬНО · идёт вечерняя сессия"}.get(c.get("phase"), "ПОДТВЕРЖДЕНО · вечерняя сессия закрыта")
    d.text((W / 2, 46), tag, font=font("DejaVuSans-Bold.ttf", 30), fill=soft, anchor="mm")
    if sub:
        d.text((W / 2, 275), sub, font=font("DejaVuSans-Bold.ttf", 44), fill=fg, anchor="mm")
    elif sig == "none":
        sub = f"покупка {pct(c['edge_buy'])} · выход {pct(c['edge_sell'])}"
        d.text((W / 2, 275), sub, font=font("DejaVuSans-Bold.ttf", 44), fill=fg, anchor="mm")
    else:
        edge = c["edge_buy"] if buy else c["edge_sell"]
        d.text((W / 2, 275), f"выгода {pct(edge)}", font=mid, fill=fg, anchor="mm")
    d.line((80, 345, W - 80, 345), fill=soft, width=3)
    src = {"user": " (твои)", "live": " (≈ рынок)"}.get(c["q_kind"], " (оценка)")
    rows = [("Расчётная стоимость пая", rub(c["calc"])),
            ("Купить по" + src, rub(c["ask"])),
            ("Продать по" + src, rub(c["bid"])),
            (f"Фактическая (сайт УК, {ru_date(c['d0'])[:5]})" if c.get("d0") else "Фактическая (сайт УК)", rub(c.get("nav0")))]
    y = 390
    for label, val in rows:
        d.text((80, y), label, font=small, fill=soft, anchor="lm")
        d.text((W - 80, y), val, font=font("DejaVuSans-Bold.ttf", 42), fill=fg, anchor="rm")
        y += 66
    for note in notes:
        d.text((W / 2, y + 10), note, font=font("DejaVuSans.ttf", 32), fill=fg, anchor="mm")
        y += 50
    d.text((W / 2, H - 38), f"Маркет Пауэр · {thr_txt()} · {now.strftime('%d.%m %H:%M')} МСК", font=font("DejaVuSans.ttf", 30), fill=soft, anchor="mm")
    import io
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


# ---------------------------------------------------------------- telegram
def tg(method, **params):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/{method}", data=data)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def tg_photo(chat_id, png, caption):
    boundary = "----mplag" + str(int(time.time() * 1000))
    parts = []
    fields = [("chat_id", str(chat_id))] + ([("caption", caption), ("parse_mode", "HTML")] if caption else [])
    for k, v in fields:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="signal.png"\r\n'
                 f'Content-Type: image/png\r\n\r\n'.encode() + png + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendPhoto", data=b"".join(parts),
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def send(state, text):
    tg("sendMessage", chat_id=state["chat_id"], text=text, parse_mode="HTML", disable_web_page_preview="true")
    print("message sent")


def send_signal(state, c, now, text=None, notes=None, **card):
    text = text or msg_signal(c, now)
    notes = list(notes or [])                    # всё — на карточке, подписи нет
    if c.get("missing"):
        notes.append("нет цен: " + ", ".join(c["missing"]) + " — взял как индекс")
    if c.get("fund_note"):
        notes.append("сайт УК не ответил, стоимость пая из прошлой проверки")
    png = signal_card(c, now, notes, **card)
    if png:
        try:
            tg_photo(state["chat_id"], png, "")
            print("card sent")
            return
        except Exception as e:  # noqa: BLE001
            print("sendPhoto failed, fallback to text:", e)
    send(state, text)


PRICE_RE = r"(\d{2,4}(?:[.,]\d{1,2})?)"

HELP_TEXT = ("📖 Команды (ответ — при ближайшей проверке, до 10 минут):\n"
             "• «статус» или «обнови» — карточка с текущим расчётом\n"
             "• «купить по 154,83 продать по 151,78» — цены пая из приложения, расчёт станет точным\n"
             "• «сброс» — снова оценивать цены самому\n"
             "• «купил 100 по 154,83» / «продал 100 по 151,78» — записать сделку; без цены — по текущей\n"
             "• «сделки» — открытые круги и итог\n"
             "• «помощь» — этот список\n\n"
             "🔔 Каждые 15 минут бот сверяет расчётную цену пая с последним значением (после твоих цен или прошлого "
             "напоминания). Если она ушла на 1,5% и больше — пришлёт напоминание прислать свежие цены.\n\n"
             "По-английски тоже можно: status, buy at 154.83 sell at 151.78, reset, bought 100 at 154.83, "
             "sold 100 at 151.78, trades, help.\n\n"
             "Как работает: днём (до 19:00) банк обновляет цену пая вслед за рынком — сигналов нет. "
             "После 19:00 цена в банке заморожена до утра, а вечерняя сессия двигает акции. "
             "🟩 ПОКУПАТЬ / 🟥 ПРОДАВАТЬ — вечернее движение даёт выгоду выше порога; ⬜ ДЕРЖАТЬ — ничего не делать. "
             "ПРЕДВАРИТЕЛЬНО — вечерняя сессия идёт; ПОДТВЕРЖДЕНО — закрыта (после 23:50). "
             "Цены «купить по … продать по …» присылай после 19:00 — тогда расчёт точный.")


def incoming(state):
    """chat_id из первого сообщения, «статус», «купить по 154,83», «продать по 153,29», «сброс»."""
    res = {"asked": False, "buy": None, "sell": None, "quote_off": False, "trades": [], "help": False, "list": False}
    try:
        upd = tg("getUpdates", offset=state.get("tg_offset", 0), timeout=0)
    except Exception as e:  # noqa: BLE001
        print("getUpdates failed:", e)
        return res
    for u in upd.get("result", []):
        state["tg_offset"] = u["update_id"] + 1
        m = u.get("message") or {}
        chat = (m.get("chat") or {}).get("id")
        if not chat:
            continue
        if not state.get("chat_id"):
            state["chat_id"] = chat
            state["welcome"] = True
        if chat != state.get("chat_id"):
            continue
        text = (m.get("text") or "").lower().strip()
        if re.search(r"статус|status|сейчас|обнов|/start|update|refresh|\bnow\b", text):
            res["asked"] = True
        if re.search(r"помощь|help|команды|commands|/help", text):
            res["help"] = True
        if re.search(r"^/?(сделки|trades)\b", text):
            res["list"] = True
        for m3 in re.finditer(r"(купил|докупил|продал|bought|sold)\s+(\d{1,6})(?:\s*(?:шт\.?|па[её]в|пая|пай|units?|pcs))?"
                              r"(?:\s*(?:по|за|at|@|for)\s*" + PRICE_RE + ")?", text):
            px_ = num(m3.group(3)) if m3.group(3) else None
            res["trades"].append(("sell" if m3.group(1) in ("продал", "sold") else "buy", int(m3.group(2)),
                                  px_ if px_ and 20 < px_ < 2000 else None))
        if re.search(r"^(сброс|цены авто|котировка авто|авто|reset|auto)\b", text):
            res.update(quote_off=True, buy=None, sell=None)
            continue
        for key, pat in (("buy", r"(?:купить|покупка|продают|\bbuy)\s*(?:по|за|at|@)?\s*:?\s*" + PRICE_RE),
                         ("sell", r"(?:продать|продажа|покупают|\bsell)\s*(?:по|за|at|@)?\s*:?\s*" + PRICE_RE),
                         ("buy", r"котировк\w*\s*:?\s*" + PRICE_RE)):
            for m2 in re.finditer(pat, text):
                v = num(m2.group(1))
                if 20 < v < 2000:
                    res[key], res["quote_off"] = v, False
    return res


# ---------------------------------------------------------------- state
def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def save_state(s):
    os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- main
def main():
    now = datetime.now(MSK)
    if not TOKEN:
        print("Нет секрета TELEGRAM_TOKEN — добавь его в Settings → Secrets → Actions. Пропускаю запуск.")
        return 0
    state = load_state()
    if CHAT_ID:
        state["chat_id"] = int(CHAT_ID) if CHAT_ID.lstrip("-").isdigit() else CHAT_ID
    CLOSE_CACHE.clear()
    CLOSE_CACHE.update(state.get("close_cache") or {})
    inc = incoming(state)
    asked = inc["asked"]
    if not state.get("chat_id"):
        print("Боту ещё никто не написал. Открой бота в Telegram и нажми Start.")
        save_state(state)
        return 0
    if state.pop("welcome", False):
        send(state, "👋 Бот «Лаг Маркет Пауэр» подключён.\n"
                    f"Проверяю каждые 10 минут с 7 до 1 МСК, сигнал при выгоде от {str(THRESHOLD * 100).replace('.', ',')}% после спреда, "
                    f"сводка около {SUMMARY_HOUR}:00.\n"
                    "Команды (ответ — при ближайшей проверке, до 10 минут):\n"
                    "• «статус» — текущий расчёт\n"
                    "• «купить по 154,83» — цена покупки пая в приложении\n"
                    "• «продать по 153,29» — цена продажи пая в приложении\n"
                    "• «сброс» — снова оценивать цены самому\n"
                    "• «помощь» — все команды")
    elif not state.get("v2_notice"):
        send(state, "🆕 Обновление бота:\n"
                    "• «котировка 154,83» — пришли цену «Продают по» из приложения, буду считать от неё\n"
                    "• «котировка авто» — снова оценивать котировку самому\n"
                    "• сигналы теперь приходят картинкой: зелёной «ПОКУПАТЬ» или красной «ПРОДАВАТЬ»\n"
                    "Ответ на команды — при ближайшей ежечасной проверке.")
    elif not state.get("v3_notice"):
        send(state, "🆕 Вместо «котировка» теперь две команды:\n"
                    "• «купить по 154,83» — цена покупки пая в приложении\n"
                    "• «продать по 153,29» — цена продажи\n"
                    "Можно одной строкой или по очереди. С обеими ценами бот знает реальный спред.\n"
                    "• «сброс» — снова оценивать цены самому")
    elif not state.get("v4_notice"):
        send(state, "🆕 Новое в боте:\n"
                    "• учёт сделок: «купил 100 по 154,83», «продал 100 по 151,78», «сделки»\n"
                    "• бот напомнит закрыть круг, когда Совкомбанк обновит цену\n"
                    "• сам проверяет точность модели и учитывает дивиденды\n"
                    "• утром в будни напомнит прислать цены\n"
                    "• «помощь» — все команды")
    elif not state.get("v5_notice"):
        send(state, "🆕 Бот переделан под реальную схему:\n"
                    "• днём Совкомбанк обновляет цену пая вслед за рынком — сигналов нет;\n"
                    "• после 19:00 цена в банке заморожена до утра, а вечерняя сессия двигает акции — "
                    "сигнал ПОКУПАТЬ/ПРОДАВАТЬ, если вечернее движение даёт выгоду выше порога;\n"
                    "• сводка — в 23:00, напоминание закрыть круг — утром, когда банк обновит цену;\n"
                    "• цены «купить по … продать по …» присылай после 19:00.\n"
                    "Именно так работали твои сделки 09.04, 06.08 и 16.10.2025.")
    state["v2_notice"] = state["v3_notice"] = state["v4_notice"] = state["v5_notice"] = True
    if inc["help"]:
        send(state, HELP_TEXT)
    if inc["quote_off"]:
        state.pop("quote", None)
        send(state, "Ок, цены снова оцениваю сам.")
    stored = state.get("quote")
    if stored and not stored.get("new") and "at" not in stored:   # старые форматы — больше не действуют
        stored = None
    if stored and stored.get("new") and not (inc["buy"] or inc["sell"]):
        inc["buy"], inc["sell"] = stored.get("buy"), stored.get("sell")   # не обработанные из-за сбоя
        stored = None
    got_prices = bool(inc["buy"] or inc["sell"])
    quote = ({"new": True, "buy": inc["buy"], "sell": inc["sell"], "prev": stored} if got_prices else stored)

    # ---- расчёт и оповещения о связи
    fund_note = ""
    try:
        try:
            fund = load_fund()
            state["fund_cache"] = {"at": now.isoformat(), "fund": fund}
        except SourceError as e:
            fc = state.get("fund_cache")
            age = (now - datetime.fromisoformat(fc["at"])).total_seconds() / 3600 if fc else 1e9
            if age > 24:
                raise
            print("UK site failed, using cache:", e)
            fund = [fc["fund"][0], [tuple(x) for x in fc["fund"][1]], fc["fund"][2], fc["fund"][3]]
            fund[0] = [tuple(x) for x in fund[0]]
            fund_note = (f"\nСайт УК сейчас не ответил, стоимость пая из проверки "
                         f"{datetime.fromisoformat(fc['at']).strftime('%d.%m %H:%M')}.")
        exq = load_fund_quote(state)
        try:
            divs = load_dividends([t for t, _, _ in fund[1]], state, now)
        except Exception as e:  # noqa: BLE001
            print("dividends failed:", e)
            divs = {}
        c = calculate(now, quote, tuple(fund), exq, state.get("last_spread") or SPREAD, divs)
        c["fund_note"] = fund_note
    except SourceError as e:
        fails = state.get("fails", 0) + 1
        state["fails"] = fails
        state["last_error"] = str(e)
        print("ERROR:", e)
        if fails == FAILS_TO_ALERT or MODE == "summary" or asked or got_prices:
            send(state, f"⚠️ <b>Нет связи</b>: {e.source} — {e.detail}.\n"
                        f"Неудачных проверок подряд: {fails}. Сигналы не приходят, пока связь не вернётся. "
                        f"Сообщу, когда восстановится.")
            state["alerted"] = True
        elif fails > FAILS_TO_ALERT and fails % REMIND_EVERY == 0:
            send(state, f"⚠️ Связи всё ещё нет: {e.source} — {e.detail}. Проверок без данных подряд: {fails}.")
        if got_prices:
            state["quote"] = {"new": True, "buy": inc["buy"], "sell": inc["sell"]}
        save_state(state)
        return 0
    except Exception as e:  # noqa: BLE001
        state["fails"] = state.get("fails", 0) + 1
        print("UNEXPECTED:", repr(e))
        if state["fails"] == FAILS_TO_ALERT:
            send(state, f"⚠️ Бот не смог посчитать стоимость пая: {type(e).__name__}. Нужна проверка кода.")
        save_state(state)
        raise

    if state.get("alerted"):
        send(state, f"✅ Связь восстановлена после {state.get('fails', 0)} неудачных проверок.")
    state.update(fails=0, alerted=False, last_error=None)

    # ---- котировка пользователя
    if c["quote_status"] == "new":
        state["quote"] = c["quote"]
        if c["quote"].get("buy") and c["quote"].get("sell"):
            state["last_spread"] = round(c["quote"]["spread"], 5)   # запоминаем реальный спред Совкомбанка
        qq = c["quote"]
        parts = [f"купить по {rub(qq['buy'])}" if qq.get("buy") else None,
                 f"продать по {rub(qq['sell'])}" if qq.get("sell") else None]
        more = "" if qq.get("buy") and qq.get("sell") else (
            ". Пришли и «" + ("продать" if qq.get("buy") else "купить") + " по …»")
        send(state, f"✅ Цены приняты: {', '.join(p for p in parts if p)}{more}")
        asked = True
    elif c["quote_status"] == "expired" or not c["quote"]:
        state.pop("quote", None)
    elif c["quote"]:
        state["quote"] = c["quote"]
    print("check ok, signal:", c["signal"])
    if os.environ.get("DEBUG"): print({k: c.get(k) for k in ("phase","eve","calc","ask","bid","edge_buy","edge_sell","q_kind","spread")})
    today = now.date().isoformat()

    # ---- пуш: расчётная цена пая ушла от последнего значения → попросить свежие цены банка
    ref = state.get("ping_ref") or {}
    if c["quote_status"] == "new" or not ok(ref.get("calc")):
        state["ping_ref"] = {"calc": round(c["calc"], 4), "at": now.isoformat()}   # точка отсчёта — свежие цены
        state["ping_alert"] = None                                                  # флаг сброшен твоими ценами
    else:
        last_chk = state.get("ping_check_at")
        if not last_chk or (now - datetime.fromisoformat(last_chk)).total_seconds() >= (PING_EVERY_MIN - 1) * 60:
            state["ping_check_at"] = now.isoformat()
            mv = c["calc"] / ref["calc"] - 1
            al = state.get("ping_alert")          # {"dir": ±1, "mv": движение на момент последнего пуша}
            d = 1 if mv > 0 else -1
            if not al:
                fire = abs(mv) >= PING_MOVE
            else:                                 # пуш уже был: повтор только если ушло ещё на PING_STEP дальше
                fire = d == al["dir"] and abs(mv) >= abs(al["mv"]) + PING_STEP
                if d != al["dir"] and abs(mv) >= PING_MOVE:
                    fire = True                   # ушло в другую сторону за порог — это новое событие
            if fire:
                since = datetime.fromisoformat(ref["at"]).strftime("%d.%m %H:%M")
                again = "ещё дальше: " if al and d == al["dir"] else ""
                send(state, f"🔔 <b>Расчетная цена пая ушла на {fmt_pct(abs(mv))} от последнего значения! "
                            f"Пришли свежие цены из Совкомбанка</b>\n"
                            f"Было {rub(ref['calc'])} ({since}), сейчас {rub(c['calc'])} ({again}{pct(mv)}).\n"
                            f"Ответь: «купить по … продать по …»")
                state["ping_alert"] = {"dir": d, "mv": round(mv, 5)}

    # ---- самопроверка модели по новой стоимости пая
    ck = c.get("check")
    if ck and state.get("check_date") != ck["date"]:
        err = ck["model"] - ck["fact"]
        errs = (state.get("model_errs") or [])[-19:] + [round(err, 5)]
        state.update(model_errs=errs, check_date=ck["date"])
        recent = errs[-5:]
        mae5 = sum(abs(x) for x in recent) / len(recent)
        if abs(err) > 0.01:
            send(state, f"⚠️ Модель ошиблась на {fmt_pct(abs(err))} за {ru_date(ck['date'])}: прогноз {pct(ck['model'])}, "
                        f"факт {pct(ck['fact'])}. Если повторится, значит УК поменяла состав фонда — сигналам доверяй осторожнее.")
        elif len(recent) >= 5 and mae5 > 0.006:
            send(state, f"⚠️ Точность модели снизилась: средняя ошибка за 5 дней {fmt_pct(mae5)}. Сигналам доверяй осторожнее.")
    errs = state.get("model_errs") or []
    if os.environ.get("DEBUG"): print("model check:", ck, "errs:", errs)
    acc_note = (f"точность модели: ±{fmt_pct(sum(abs(x) for x in errs[-5:]) / len(errs[-5:]))} за {len(errs[-5:])} дн."
                if errs else None)

    # ---- сделки и круги
    rounds = state.setdefault("rounds", [])
    for side, qty, price in inc["trades"]:
        if qty <= 0:
            continue
        price = price or (c["ask"] if side == "buy" else c["bid"])
        closing = "short" if side == "buy" else "long"
        remaining, profit, closed_cost = qty, 0.0, 0.0
        for r in [r for r in rounds if r["side"] == closing]:
            if remaining <= 0:
                break
            m = min(remaining, r["qty"])
            gain = m * (r["price"] - price) if closing == "short" else m * (price - r["price"])
            profit += gain
            closed_cost += m * r["price"]
            r["qty"] -= m
            remaining -= m
        rounds[:] = [r for r in rounds if r["qty"] > 0]
        lines = []
        if closed_cost:
            state["realized"] = round(state.get("realized", 0.0) + profit, 2)
            state.setdefault("closed", []).append({"at": now.isoformat(), "profit": round(profit, 2),
                                                    "pct": round(profit / closed_cost, 5)})
            state["closed"] = state["closed"][-50:]
            lines.append(f"✅ Круг закрыт: {money(profit)} ({pct(profit / closed_cost)}). "
                         f"Итого по всем кругам: {money(state['realized'])}.")
        if remaining > 0:
            rounds.append({"side": "long" if side == "buy" else "short", "qty": remaining, "price": price,
                           "date": c["expected"], "at": now.isoformat(), "reminded": False})
            act = "купил" if side == "buy" else "продал"
            nxt = "продать" if side == "buy" else "откупить"
            lines.append(f"📝 Записал: {act} {remaining} шт. по {rub(price)}. Напомню {nxt}, когда Совкомбанк обновит цену.")
        send(state, "\n".join(lines))
    if inc["list"]:
        if rounds:
            body = "\n".join(f"• {'купил' if r['side'] == 'long' else 'продал'} {r['qty']} шт. по {rub(r['price'])} "
                             f"({datetime.fromisoformat(r['at']).strftime('%d.%m %H:%M')})" for r in rounds)
        else:
            body = "открытых кругов нет"
        send(state, f"📒 Открытые круги:\n{body}\n\nЗакрыто кругов: {len(state.get('closed', []))}, "
                    f"итого: {money(state.get('realized', 0))}")

    # ---- напоминание закрыть круг: банк снова обновляет цену (наступила сессия после сделки)
    if c["phase"] == "live":
        for r in rounds:
            if r.get("reminded") or now < next_live_start(datetime.fromisoformat(r["at"])) + timedelta(minutes=20):
                continue
            if r["side"] == "long":
                gain = r["qty"] * (c["bid"] - r["price"])
                word, color, act = "ПРОДАТЬ", "sell", f"продать {r['qty']} шт."
            else:
                gain = r["qty"] * (r["price"] - c["ask"])
                word, color, act = "ОТКУПИТЬ", "buy", f"откупить {r['qty']} шт."
            gtxt = money(gain)
            send_signal(state, c, now, f"Закрыть круг: {act}", notes=[f"закрыть круг: {act}, ожидаемо {gtxt}",
                                                                      "проверь, что цена в приложении обновилась"],
                        word=word, color=color, sub=f"закрыть круг · {gtxt}")
            r["reminded"] = True

    # ---- напоминание прислать цены: вечером, если рынок заметно сдвинулся после 19:00
    if c["phase"] == "evening" and c.get("eve") is not None and abs(c["eve"]) >= 0.01 \
            and state.get("remind_date") != today and c["q_kind"] != "user":
        send(state, f"🌙 Акции фонда на вечерней сессии {pct(c['eve'])}. Пришли цены пая из приложения "
                    "(«купить по … продать по …») — посчитаю выгоду точно.")
        state["remind_date"] = today

    # ---- сигнал
    last, last_at = state.get("signal", "none"), state.get("signal_at")
    hours = (now - datetime.fromisoformat(last_at)).total_seconds() / 3600 if last_at else 1e9
    signal_sent = False
    # антидребезг: во время торгов новый сигнал — только если держится 2 проверки подряд
    pend = state.get("pending") or {}
    pend = {"signal": c["signal"], "n": pend.get("n", 0) + 1 if pend.get("signal") == c["signal"] else 1}
    state["pending"] = pend
    steady = (not in_session(now)) or pend["n"] >= 2 or asked
    if c["signal"] != "none" and steady and (c["signal"] != last or hours >= REPEAT_HOURS or asked):
        send_signal(state, c, now)
        state["signal_at"] = now.isoformat()
        signal_sent = True
    elif c["signal"] == "none" and last != "none":
        send_signal(state, c, now, f"⬜ <b>ДЕРЖАТЬ</b>: сигнал «{'Покупать' if last == 'buy' else 'Продавать'}» больше не действует · {thr_txt()}.\n\n{msg_body(c, now)}")
    if c["signal"] == "none" or steady:
        state["signal"] = c["signal"]

    # ---- сводка
    due = now.hour >= SUMMARY_HOUR and state.get("summary_date") != today
    if (MODE == "summary" or asked or due) and not (signal_sent and not due):
        send_signal(state, c, now, msg_summary(c, now), notes=[acc_note] if acc_note else None)
    if due:
        state["summary_date"] = today

    state["close_cache"] = dict(CLOSE_CACHE)
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
