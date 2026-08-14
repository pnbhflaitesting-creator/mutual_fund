"""Core logic for the mutual-fund voice bot.

Responsibilities:
  * Load the curated fund universe (data/funds.json).
  * Parse a natural-language / spoken query into structured intent
    (category, period, how many funds).
  * Fetch live NAV data from api.mfapi.in and compute annualised returns,
    falling back to the bundled snapshot when the network is unavailable.
  * Rank funds and produce both structured results and a spoken summary.
"""

import json
import os
import re
import time
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "funds.json")
MFAPI_URL = "https://api.mfapi.in/mf/{code}"

# How long a live NAV fetch may take, and how long to trust a cached result.
FETCH_TIMEOUT_SECONDS = 6
CACHE_TTL_SECONDS = 6 * 60 * 60  # 6 hours

# ---------------------------------------------------------------------------
# Category vocabulary: maps the many ways a person might *say* a category to
# the canonical key used inside funds.json.
# ---------------------------------------------------------------------------
CATEGORY_SYNONYMS = {
    "pharma": ["pharma", "pharmaceutical", "pharmaceuticals", "healthcare",
               "health care", "health", "medical", "medicine", "diagnostic"],
    "technology": ["technology", "tech", "it ", "i.t", "digital", "software",
                   "information technology"],
    "banking": ["banking", "bank", "financial", "finance", "bfsi",
                "financial services"],
    "infrastructure": ["infrastructure", "infra", "construction", "capex"],
    "fmcg": ["fmcg", "consumption", "consumer", "consumer goods", "staples"],
    "energy": ["energy", "power", "oil", "gas", "resources", "natural resources"],
    "largecap": ["large cap", "largecap", "large-cap", "bluechip", "blue chip",
                 "blue-chip"],
    "midcap": ["mid cap", "midcap", "mid-cap"],
    "smallcap": ["small cap", "smallcap", "small-cap"],
    "flexicap": ["flexi cap", "flexicap", "flexi-cap", "multi cap", "multicap",
                 "multi-cap"],
    "elss": ["elss", "tax saver", "tax saving", "tax-saving", "80c",
             "tax saving fund"],
    "index": ["index", "nifty", "sensex", "passive"],
}

# Human-friendly labels for spoken output.
CATEGORY_LABELS = {
    "pharma": "pharma & healthcare",
    "technology": "technology",
    "banking": "banking & financial services",
    "infrastructure": "infrastructure",
    "fmcg": "FMCG & consumption",
    "energy": "energy & resources",
    "largecap": "large cap",
    "midcap": "mid cap",
    "smallcap": "small cap",
    "flexicap": "flexi cap",
    "elss": "ELSS tax saver",
    "index": "index",
}

# Number words for parsing "top five", "top ten", etc.
_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

_PERIOD_KEYS = {"1y": "1 year", "3y": "3 year", "5y": "5 year"}

# Plain-language risk / suitability notes per category, used in the
# recommendation. These are educational, not personalised advice.
CATEGORY_RISK_NOTES = {
    "pharma": "It's a sector fund, so it's concentrated in one industry and can "
              "swing more than a diversified fund — usually best as a small "
              "satellite holding, not your core.",
    "technology": "It's a sector fund concentrated in tech, which can be volatile "
                  "— better as a satellite holding alongside a diversified core.",
    "banking": "It's a sector fund focused on financials — cyclical and best kept "
               "to a smaller part of a diversified portfolio.",
    "infrastructure": "It's a thematic fund tied to the capex cycle — high growth "
                      "potential but cyclical, so size it as a satellite holding.",
    "fmcg": "It's a consumption-theme fund — generally steadier than most sector "
            "funds, but still concentrated in one theme.",
    "energy": "It's a resources/energy theme fund — commodity-linked and cyclical, "
              "so keep it a small part of the portfolio.",
    "largecap": "Large-cap funds are relatively stable and can serve as a core "
                "holding for most investors.",
    "midcap": "Mid-cap funds carry higher risk and volatility than large caps — "
              "suited to a long horizon of five years or more.",
    "smallcap": "Small-cap funds are high risk and can be very volatile — only "
                "for a long horizon (seven years or more) and a higher risk "
                "appetite.",
    "flexicap": "Flexi-cap funds spread across large, mid and small caps, so they "
                "work well as a diversified core holding.",
    "elss": "This is a tax-saving fund with a three-year lock-in and Section 80C "
            "benefit — plan for the lock-in before investing.",
    "index": "Index funds passively track a benchmark at low cost — a simple, "
             "low-maintenance core holding.",
}


class FundService:
    def __init__(self, data_path=DATA_PATH):
        with open(data_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        self.meta = payload.get("_meta", {})
        self.funds = payload["funds"]
        # scheme_code -> (timestamp, {period: return})
        self._cache = {}
        self._cache_lock = threading.Lock()

    # ------------------------------------------------------------------ parse
    def parse_query(self, text):
        """Turn a spoken/typed query into {category, period, limit}."""
        t = " " + (text or "").lower().strip() + " "

        # Category (first match wins; check multi-word synonyms too).
        category = None
        for canonical, synonyms in CATEGORY_SYNONYMS.items():
            if any(s in t for s in synonyms):
                category = canonical
                break

        # Period: default 3 years (matches the primary use case).
        period = "3y"
        if re.search(r"\b(1|one)\s*(year|yr)", t):
            period = "1y"
        elif re.search(r"\b(5|five)\s*(year|yr)", t):
            period = "5y"
        elif re.search(r"\b(3|three)\s*(year|yr)", t):
            period = "3y"

        # How many: "top 5", "top five", default 5.
        limit = 5
        m = re.search(r"top\s+(\d+)", t)
        if m:
            limit = int(m.group(1))
        else:
            m = re.search(r"top\s+(" + "|".join(_NUMBER_WORDS) + r")\b", t)
            if m:
                limit = _NUMBER_WORDS[m.group(1)]
        limit = max(1, min(limit, 15))

        return {"category": category, "period": period, "limit": limit}

    # ------------------------------------------------------------------- live
    def _fetch_live_returns(self, scheme_code):
        """Fetch NAV history from mfapi.in and compute 1y/3y/5y CAGR.

        Returns a dict {"1y": %, "3y": %, "5y": %, "nav": latest} or None on
        any failure (network blocked, bad data, etc.), so the caller can fall
        back to the bundled snapshot.
        """
        now = time.time()
        with self._cache_lock:
            cached = self._cache.get(scheme_code)
            if cached and (now - cached[0]) < CACHE_TTL_SECONDS:
                return cached[1]

        try:
            url = MFAPI_URL.format(code=scheme_code)
            req = urllib.request.Request(url, headers={"User-Agent": "mf-voicebot/1.0"})
            with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SECONDS) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

        data = raw.get("data") or []
        if len(data) < 2:
            return None

        # mfapi returns newest-first: [{"date": "dd-mm-yyyy", "nav": "123.45"}, ...]
        def parse_point(point):
            return (datetime.strptime(point["date"], "%d-%m-%Y"), float(point["nav"]))

        try:
            points = [parse_point(p) for p in data if p.get("nav") not in (None, "", "0")]
        except (ValueError, KeyError):
            return None
        if len(points) < 2:
            return None

        points.sort(key=lambda x: x[0])  # oldest -> newest
        latest_date, latest_nav = points[-1]

        result = {"nav": round(latest_nav, 4)}
        for key, years in (("1y", 1), ("3y", 3), ("5y", 5)):
            target = latest_date.replace(year=latest_date.year - years) \
                if latest_date.month != 2 or latest_date.day != 29 \
                else latest_date.replace(year=latest_date.year - years, day=28)
            # Nearest NAV on/before the target date.
            past = None
            for d, nav in points:
                if d <= target:
                    past = (d, nav)
                else:
                    break
            if past and past[1] > 0:
                cagr = (latest_nav / past[1]) ** (1.0 / years) - 1.0
                result[key] = round(cagr * 100, 2)
            else:
                result[key] = None

        with self._cache_lock:
            self._cache[scheme_code] = (now, result)
        return result

    # ------------------------------------------------------------------- rank
    def get_top_funds(self, category=None, period="3y", limit=5, live=True):
        """Rank funds by return over `period`, optionally filtered by category."""
        universe = [f for f in self.funds
                    if category is None or f["category"] == category]

        source = "snapshot"
        live_returns = {}
        if live and universe:
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = {pool.submit(self._fetch_live_returns, f["scheme_code"]): f
                           for f in universe}
                for fut in as_completed(futures):
                    res = fut.result()
                    if res:
                        live_returns[futures[fut]["scheme_code"]] = res
            if live_returns:
                source = "live" if len(live_returns) == len(universe) else "mixed"

        results = []
        for f in universe:
            live_res = live_returns.get(f["scheme_code"])
            ret = None
            is_live = False
            if live_res and live_res.get(period) is not None:
                ret = live_res[period]
                is_live = True
            else:
                ret = f["returns"].get(period)
            if ret is None:
                continue
            # All-period returns (live where available, else snapshot), used by
            # the recommendation engine to judge consistency over time.
            returns_all = {}
            for pk in ("1y", "3y", "5y"):
                if live_res and live_res.get(pk) is not None:
                    returns_all[pk] = live_res[pk]
                else:
                    returns_all[pk] = f["returns"].get(pk)
            results.append({
                "name": f["name"],
                "amc": f["amc"],
                "category": f["category"],
                "category_label": CATEGORY_LABELS.get(f["category"], f["category"]),
                "scheme_code": f["scheme_code"],
                "period": period,
                "return_pct": ret,
                "returns_all": returns_all,
                "live": is_live,
            })

        results.sort(key=lambda x: x["return_pct"], reverse=True)
        return {"source": source, "results": results[:limit]}

    # ------------------------------------------------------- recommendation
    def build_recommendation(self, parsed, ranked):
        """Pick one fund to highlight and explain the reasoning.

        The pick favours *consistency*: a fund that beats its peers' average
        across the 1/3/5-year windows, not just the single headline period.
        Ties break on the requested period's return. A category risk note is
        attached so the user understands the trade-off. This is rule-based and
        transparent — an educational suggestion, not personalised advice.
        """
        results = ranked["results"]
        if not results:
            return None

        period = parsed["period"]

        # Peer average per period across the shown set (ignoring missing values).
        peer_avg = {}
        for pk in ("1y", "3y", "5y"):
            vals = [r["returns_all"].get(pk) for r in results
                    if r["returns_all"].get(pk) is not None]
            peer_avg[pk] = sum(vals) / len(vals) if vals else None

        def score(r):
            ra = r["returns_all"]
            # How many windows this fund beats the peer average in.
            consistency = sum(
                1 for pk in ("1y", "3y", "5y")
                if ra.get(pk) is not None and peer_avg[pk] is not None
                and ra[pk] >= peer_avg[pk]
            )
            return (consistency, r["return_pct"])

        pick = max(results, key=score)
        ra = pick["returns_all"]
        beats = [pk for pk in ("1y", "3y", "5y")
                 if ra.get(pk) is not None and peer_avg[pk] is not None
                 and ra[pk] >= peer_avg[pk]]

        # Rationale text.
        period_label = _PERIOD_KEYS.get(period, period)
        rank_pos = results.index(pick) + 1
        if rank_pos == 1:
            lead = (f"{pick['name']} looks the strongest of these — it tops the list "
                    f"with {pick['return_pct']}% over {period_label}")
        else:
            lead = (f"{pick['name']} stands out — {pick['return_pct']}% over "
                    f"{period_label}")

        if len(beats) >= 2:
            others = [p for p in beats if p != period]
            extra = ", ".join(f"{ra[p]}% over {_PERIOD_KEYS[p]}" for p in others)
            consistency_txt = (f", and it stays ahead of the pack over other "
                               f"periods too ({extra})") if extra else ""
        else:
            consistency_txt = (", though its lead is mainly over this one period, "
                               "so check longer-term consistency")
        note = CATEGORY_RISK_NOTES.get(pick["category"], "")

        reason = f"{lead}{consistency_txt}. {note}".strip()
        return {
            "name": pick["name"],
            "amc": pick["amc"],
            "scheme_code": pick["scheme_code"],
            "return_pct": pick["return_pct"],
            "period": period,
            "reason": reason,
        }

    # ---------------------------------------------------------------- speech
    def build_spoken_summary(self, parsed, ranked, recommendation=None):
        results = ranked["results"]
        period_label = _PERIOD_KEYS.get(parsed["period"], parsed["period"])
        cat = parsed["category"]
        cat_label = CATEGORY_LABELS.get(cat) if cat else None

        if not results:
            if cat_label:
                return (f"Sorry, I couldn't find any {cat_label} funds. "
                        "Try another category like technology, banking, or small cap.")
            return "Sorry, I couldn't find any matching funds. Please try again."

        n = len(results)
        scope = f"{cat_label} " if cat_label else ""
        freshness = "live" if ranked["source"] in ("live", "mixed") else "latest available"
        lead = (f"Here are the top {n} {scope}mutual funds by {period_label} return, "
                f"using {freshness} data. ")
        parts = []
        for i, r in enumerate(results, 1):
            parts.append(f"Number {i}, {r['name']}, with {r['return_pct']} percent.")
        body = lead + " ".join(parts)

        if recommendation:
            body += (f" My pick out of these would be {recommendation['name']}. "
                     f"{recommendation['reason']} "
                     "Remember, this is for learning, not investment advice.")
        return body
