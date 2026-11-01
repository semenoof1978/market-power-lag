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
REPEAT_HOURS = 3        # повтор действующего сигнала не чаще
FAILS_TO_ALERT = 2      # подряд неудачных запусков до оповещения о связи

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
    return f"{x:,.2f}".replace(",", " ").replace(".", ",") + " ₽"


def pct(x):
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
        text = page_text(http_get(UK_URL))
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


def load_history(tickers, frm, till):
    hist = {}
    for t in tickers:
        j = iss_json(f"{ISS}/history/engines/stock/markets/shares/boards/TQBR/securities/{t}.json"
                     f"?iss.meta=off&iss.only=history&from={frm}&till={till}&history.columns=TRADEDATE,CLOSE,LEGALCLOSEPRICE")
        hist[t] = {r["TRADEDATE"]: (r["CLOSE"] if ok(r.get("CLOSE")) else r.get("LEGALCLOSEPRICE"))
                   for r in iss_rows(j, "history")}
    j = iss_json(f"{ISS}/history/engines/stock/markets/index/securities/IMOEX.json"
                 f"?iss.meta=off&iss.only=history&from={frm}&till={till}&history.columns=TRADEDATE,CLOSE")
    idx = {r["TRADEDATE"]: r["CLOSE"] for r in iss_rows(j, "history") if ok(r.get("CLOSE"))}
    if not idx:
        raise SourceError("Мосбиржа", "пустая история индекса")
    return hist, idx


def load_market(tickers):
    j = iss_json(f"{ISS}/engines/stock/markets/shares/boards/TQBR/securities.json"
                 f"?iss.meta=off&iss.only=marketdata,securities&securities={','.join(tickers)}")
    sec = {r["SECID"]: r for r in iss_rows(j, "securities")}
    cur, upd = {}, None
    for r in iss_rows(j, "marketdata"):
        s = sec.get(r["SECID"], {})
        v = next((x for x in (r.get("LAST"), r.get("LCURRENTPRICE"), r.get("MARKETPRICE"), s.get("PREVPRICE")) if ok(x)), None)
        cur[r["SECID"]] = v
        if r.get("UPDATETIME") and (upd is None or r["UPDATETIME"] > upd):
            upd = r["UPDATETIME"]
    j = iss_json(f"{ISS}/engines/stock/markets/index/securities.json?iss.meta=off&iss.only=marketdata&securities=IMOEX")
    r = (iss_rows(j, "marketdata") or [{}])[0]
    idx = next((x for x in (r.get("CURRENTVALUE"), r.get("LASTVALUE")) if ok(x)), None)
    if not ok(idx):
        raise SourceError("Мосбиржа", "нет текущего значения индекса")
    return cur, idx, upd


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


def calculate(now, quote):
    """quote — котировка от пользователя: {"price", "nav", "date"} или {"price"} (только что прислана) или None."""
    navs, weights, w_date, w_src = load_fund()
    tickers = [t for t, _, _ in weights]
    d0, nav0 = navs[0]
    today = now.date().isoformat()
    frm = (date.fromisoformat(d0) - timedelta(days=12)).isoformat()
    hist, idx_hist = load_history(tickers, frm, today)
    cur, idx_now, upd = load_market(tickers)
    if d0 not in idx_hist:
        raise SourceError("Мосбиржа", f"нет закрытия индекса за {ru_date(d0)}")
    trading = sorted(d for d in idx_hist if d < today)
    nav_map = dict(navs)

    def move_between(a, price_b, idx_b):
        """Изменение пая от закрытия дня a до цен price_b(t) и индекса idx_b."""
        def r(t):
            pa, pb = hist.get(t, {}).get(a), price_b(t)
            return pb / pa - 1 if ok(pa) and ok(pb) else None
        return fund_move(weights, r, idx_b / idx_hist[a] - 1)

    def fee(a, b):
        return 1 - FUND_FEE * (date.fromisoformat(b) - date.fromisoformat(a)).days / 365

    # --- котировка пользователя: определить, за какую дату в ней стоимость пая
    quote_status = None
    if quote and "date" not in quote:
        q_nav = quote["price"] / (1 + SPREAD)
        match = next((d for d, n in navs if abs(n - q_nav) / n < 0.0012), None)
        if match:
            q_d = match
        else:
            later = [d for d in trading if d > d0]
            q_d = later[0] if later else (trading[-QUOTE_LAG] if len(trading) >= QUOTE_LAG else d0)
        quote = {"price": quote["price"], "nav": round(q_nav, 4), "date": q_d}
        quote_status = "new"
    expected = trading[-QUOTE_LAG] if len(trading) >= QUOTE_LAG else d0
    if quote and quote_status != "new" and quote["date"] < expected:
        quote, quote_status = None, "expired"   # прошёл новый торговый день — котировка обновилась

    # --- база расчёта: котировка пользователя, если есть, иначе сайт УК
    if quote and quote["date"] in idx_hist:
        base_d, base_nav, base_src = quote["date"], quote["nav"], "user"
    else:
        base_d, base_nav, base_src = d0, nav0, "site"
    move_now, missing = move_between(base_d, lambda t: cur.get(t), idx_now)
    calc = base_nav * (1 + move_now) * fee(base_d, today)

    # --- котировка Совкомбанка
    if quote:
        q_date, q_nav, q_kind, ask = quote["date"], quote["nav"], "user", quote["price"]
    else:
        q_date = expected
        if q_date in nav_map:
            q_nav, q_kind = nav_map[q_date], "site"
        elif q_date > d0 and q_date in idx_hist:
            mv, _ = move_between(d0, lambda t: hist.get(t, {}).get(q_date), idx_hist[q_date])
            q_nav, q_kind = nav0 * (1 + mv) * fee(d0, q_date), "est"
        else:
            q_date, q_nav, q_kind = d0, nav0, "site"
        ask = q_nav * (1 + SPREAD)
    bid = q_nav * (1 - SPREAD)

    edge_buy = calc * (1 - SPREAD) / ask - 1      # купить сейчас, продать после обновления
    edge_sell = bid / (calc * (1 + SPREAD)) - 1   # продать сейчас, откупить после обновления
    signal = "buy" if edge_buy >= THRESHOLD else "sell" if edge_sell >= THRESHOLD else "none"
    return {
        "signal": signal, "calc": calc, "move": move_now, "missing": missing,
        "base_d": base_d, "base_nav": base_nav, "base_src": base_src,
        "d0": d0, "nav0": nav0, "q_date": q_date, "q_nav": q_nav, "q_kind": q_kind,
        "ask": ask, "bid": bid, "edge_buy": edge_buy, "edge_sell": edge_sell,
        "upd": upd, "w_src": w_src, "w_date": w_date,
        "quote": quote, "quote_status": quote_status,
    }


# ---------------------------------------------------------------- messages
def msg_body(c, now):
    base = (f"от твоей котировки за {ru_date(c['base_d'])}" if c["base_src"] == "user"
            else f"к {ru_date(c['base_d'])}")
    if c["q_kind"] == "user":
        q = f"<b>{rub(c['ask'])}</b> (прислал ты) → стоимость пая в ней {rub(c['q_nav'])} за {ru_date(c['q_date'])}"
    elif c["q_kind"] == "est":
        q = f"≈ {rub(c['ask'])} (оценка бота, стоимость пая за {ru_date(c['q_date'])} на сайте УК ещё нет)"
    else:
        q = f"≈ {rub(c['ask'])} (стоимость пая за {ru_date(c['q_date'])} + спред)"
    lines = [
        f"Расчётная стоимость пая: <b>{rub(c['calc'])}</b>",
        f"   по ценам Мосбиржи на {(c['upd'] or now.strftime('%H:%M'))[:5]} МСК, {pct(c['move'])} {base}",
        f"Фактическая стоимость пая: <b>{rub(c['nav0'])}</b> (сайт УК, {ru_date(c['d0'])})",
        f"Котировка «Продают по»: {q}",
        f"   продать сейчас ≈ {rub(c['bid'])}",
        f"Выгода с учётом спреда {str(SPREAD * 100).replace('.', ',')}%: покупка {pct(c['edge_buy'])}, выход {pct(c['edge_sell'])}",
    ]
    if c["missing"]:
        lines.append("Нет цен по: " + ", ".join(c["missing"]) + " — взял их как индекс.")
    return "\n".join(lines)


def msg_signal(c, now):
    head = {"buy": "🟩🟩🟩 <b>ПОКУПАТЬ</b> 🟩🟩🟩", "sell": "🟥🟥🟥 <b>ПРОДАВАТЬ</b> 🟥🟥🟥"}[c["signal"]]
    why = ("Расчётная стоимость пая выше котировки — пай отстаёт от рынка." if c["signal"] == "buy"
           else "Расчётная стоимость пая ниже котировки — пай ещё не учёл падение.")
    return (f"{head}\n{why}\n\n{msg_body(c, now)}\n\n"
            f"Сигнал верен, если цены удержатся до закрытия дня. Проверь котировку в приложении перед сделкой.")


def msg_summary(c, now):
    state = {"buy": "сигнал «Покупать»", "sell": "сигнал «Продавать»", "none": "сигнала нет"}[c["signal"]]
    tip = "" if c["q_kind"] == "user" else "\n\nДля точного расчёта пришли котировку из приложения, например: котировка 154,83"
    return (f"📋 Сводка {now.strftime('%d.%m %H:%M')} · {state} (порог {str(THRESHOLD * 100).replace('.', ',')}%)\n\n"
            f"{msg_body(c, now)}\n\nСостав фонда: {c['w_src']}, на {c['w_date']}.{tip}")


# ---------------------------------------------------------------- signal card (picture)
FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")


def signal_card(c, now):
    """PNG: крупное слово на зелёном или красном фоне. None, если Pillow нет."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None
    buy = c["signal"] == "buy"
    bg, fg, soft = ((30, 140, 80), (255, 255, 255), (214, 240, 224)) if buy else ((196, 52, 44), (255, 255, 255), (250, 222, 219))
    W, H = 1080, 640
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)

    def font(name, size):
        for path in (os.path.join(FONT_DIR, name), f"/usr/share/fonts/truetype/dejavu/{name}"):
            if os.path.exists(path):
                return ImageFont.truetype(path, size)
        return ImageFont.load_default()

    big, mid, small = font("DejaVuSans-Bold.ttf", 150), font("DejaVuSans-Bold.ttf", 52), font("DejaVuSans.ttf", 38)
    word = "ПОКУПАТЬ" if buy else "ПРОДАВАТЬ"
    while d.textlength(word, font=big) > W - 100 and big.size > 60:
        big = font("DejaVuSans-Bold.ttf", big.size - 10)
    d.text((W / 2, 150), word, font=big, fill=fg, anchor="mm")
    edge = c["edge_buy"] if buy else c["edge_sell"]
    d.text((W / 2, 275), f"выгода {pct(edge)}", font=mid, fill=fg, anchor="mm")
    d.line((80, 345, W - 80, 345), fill=soft, width=3)
    rows = [("Расчётная стоимость пая", rub(c["calc"])),
            (("Котировка «Продают по»", rub(c["ask"])) if buy else ("Продать сейчас ≈", rub(c["bid"]))),
            (f"Фактическая (сайт УК, {ru_date(c['d0'])[:5]})", rub(c["nav0"]))]
    y = 395
    for label, val in rows:
        d.text((80, y), label, font=small, fill=soft, anchor="lm")
        d.text((W - 80, y), val, font=font("DejaVuSans-Bold.ttf", 42), fill=fg, anchor="rm")
        y += 66
    d.text((W / 2, H - 38), f"Маркет Пауэр · {now.strftime('%d.%m %H:%M')} МСК", font=font("DejaVuSans.ttf", 30), fill=soft, anchor="mm")
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
    for k, v in (("chat_id", str(chat_id)), ("caption", caption), ("parse_mode", "HTML")):
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
    print("SENT:", text.split("\n")[0])


def send_signal(state, c, now):
    text = msg_signal(c, now)
    png = signal_card(c, now)
    if png:
        try:
            tg_photo(state["chat_id"], png, text[:1024])
            print("SENT PHOTO:", c["signal"])
            return
        except Exception as e:  # noqa: BLE001
            print("sendPhoto failed, fallback to text:", e)
    send(state, text)


QUOTE_RE = re.compile(r"(\d{2,4}(?:[.,]\d{1,2})?)")


def incoming(state):
    """chat_id из первого сообщения, «статус», «котировка 154,83», «котировка авто»."""
    res = {"asked": False, "quote": None, "quote_off": False}
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
        if re.search(r"статус|status|сейчас", text):
            res["asked"] = True
        if "котиров" in text or re.fullmatch(r"\d{2,4}(?:[.,]\d{1,2})?\s*(₽|р|руб\.?)?", text):
            if re.search(r"авто|сброс|удал", text):
                res["quote_off"], res["quote"] = True, None
                continue
            m2 = QUOTE_RE.search(text)
            v = num(m2.group(1)) if m2 else float("nan")
            if 20 < v < 2000:
                res["quote"], res["quote_off"] = v, False
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
    inc = incoming(state)
    asked = inc["asked"]
    if not state.get("chat_id"):
        print("Боту ещё никто не написал. Открой бота в Telegram и нажми Start.")
        save_state(state)
        return 0
    if state.pop("welcome", False):
        send(state, "👋 Бот «Лаг Маркет Пауэр» подключён.\n"
                    f"Проверяю каждый час с 7 до 0 МСК, сигнал при выгоде от {str(THRESHOLD * 100).replace('.', ',')}% после спреда, "
                    f"сводка около {SUMMARY_HOUR}:00.\n"
                    "Команды (ответ — при ближайшей проверке):\n"
                    "• «статус» — текущий расчёт\n"
                    "• «котировка 154,83» — цена «Продают по» из приложения, считаю от неё\n"
                    "• «котировка авто» — снова оценивать котировку самому")
    elif not state.get("v2_notice"):
        send(state, "🆕 Обновление бота:\n"
                    "• «котировка 154,83» — пришли цену «Продают по» из приложения, буду считать от неё\n"
                    "• «котировка авто» — снова оценивать котировку самому\n"
                    "• сигналы теперь приходят картинкой: зелёной «ПОКУПАТЬ» или красной «ПРОДАВАТЬ»\n"
                    "Ответ на команды — при ближайшей ежечасной проверке.")
    state["v2_notice"] = True
    if inc["quote_off"]:
        state.pop("quote", None)
        send(state, "Ок, котировку снова оцениваю сам.")
    quote = {"price": inc["quote"]} if inc["quote"] else state.get("quote")

    # ---- расчёт и оповещения о связи
    try:
        c = calculate(now, quote)
    except SourceError as e:
        fails = state.get("fails", 0) + 1
        state["fails"] = fails
        state["last_error"] = str(e)
        print("ERROR:", e)
        if fails == FAILS_TO_ALERT or MODE == "summary" or asked or inc["quote"]:
            send(state, f"⚠️ <b>Нет связи</b>: {e.source} — {e.detail}.\n"
                        f"Не получается уже {fails} проверки подряд. Сигналы не приходят, пока связь не вернётся. "
                        f"Сообщу, когда восстановится.")
            state["alerted"] = True
        elif fails > FAILS_TO_ALERT and fails % 6 == 0:
            send(state, f"⚠️ Связи всё ещё нет: {e.source} — {e.detail}. Проверок без данных подряд: {fails}.")
        if inc["quote"]:
            state["quote"] = {"price": inc["quote"]}
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
        send(state, f"✅ Принял котировку {rub(c['quote']['price'])}. Стоимость пая в ней {rub(c['quote']['nav'])} "
                    f"за {ru_date(c['quote']['date'])}. Считаю от неё, пока Совкомбанк не обновит цену.")
        asked = True
    elif c["quote_status"] == "expired":
        state.pop("quote", None)
        send(state, "ℹ️ Твоя котировка устарела: прошёл новый торговый день. Снова оцениваю её сам. "
                    "Пришли новую, например: котировка 154,83")
    elif c["quote"]:
        state["quote"] = c["quote"]
    print(json.dumps({k: v for k, v in c.items() if k != "missing"}, ensure_ascii=False, default=str))

    # ---- сигнал
    last, last_at = state.get("signal", "none"), state.get("signal_at")
    hours = (now - datetime.fromisoformat(last_at)).total_seconds() / 3600 if last_at else 1e9
    signal_sent = False
    if c["signal"] != "none" and (c["signal"] != last or hours >= REPEAT_HOURS or asked):
        send_signal(state, c, now)
        state["signal_at"] = now.isoformat()
        signal_sent = True
    elif c["signal"] == "none" and last != "none":
        send(state, f"⚪ Сигнал «{'Покупать' if last == 'buy' else 'Продавать'}» больше не действует.\n\n{msg_body(c, now)}")
    state["signal"] = c["signal"]

    # ---- сводка
    today = now.date().isoformat()
    due = now.hour >= SUMMARY_HOUR and state.get("summary_date") != today
    if (MODE == "summary" or asked or due) and not (signal_sent and not due):
        send(state, msg_summary(c, now))
    if due:
        state["summary_date"] = today

    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
