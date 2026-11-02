import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bot"))
import mp_bot as B
from datetime import datetime
now = datetime.now(B.MSK)
fund = B.load_fund()
print("NAV last 4:", fund[0][:4])
divs = B.load_dividends([t for t, _, _ in fund[1]], {}, now)
for q in (None, {"new": True, "buy": 150.66, "sell": 149.17}):
    c = B.calculate(now, q, tuple(fund), None, 0.00995, divs)
    keep = ["signal", "calc", "move", "base_d", "base_nav", "base_src", "d0", "nav0", "q_date", "q_nav", "q_kind",
            "ask", "bid", "edge_buy", "edge_sell", "upd", "check", "expected", "missing"]
    print(json.dumps({k: c.get(k) for k in keep}, ensure_ascii=False, default=str))
