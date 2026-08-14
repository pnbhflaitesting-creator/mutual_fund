# 🎙️ Mutual Fund Voice Bot

A voice-driven assistant that answers questions like:

> *"What are the top 5 mutual funds by 3 year return?"*
> *"Top 5 pharma funds by 3 year return"*
> *"Best technology funds over 5 years"*

You **speak** the question, the bot ranks Indian mutual funds by the requested
return period (optionally filtered to a category like pharma, technology,
small cap …), shows the list, **suggests one fund to consider** with the
reasoning, and **reads the answer back** to you.

## The suggested pick

Beyond the ranked list, the bot highlights **one fund** and explains why. The
pick is rule-based and transparent (not a black box, and not personalised
advice):

- It favours **consistency** — a fund that beats its peers' average across the
  1-, 3-, and 5-year windows, not just the single headline number.
- Ties break on the requested period's return.
- A **category risk note** is attached (e.g. "sector funds are concentrated and
  best kept as a satellite holding"; "small-cap funds suit a 7-year+ horizon"),
  so you understand the trade-off, not just the number.

Every answer ends with a reminder that this is an educational demo, not
investment advice.

---

## How it works

```
Browser (mic)  ──speech-to-text──►  question text
      │                                   │
      │                             GET /api/query?q=…
      ▼                                   ▼
Web Speech API                     Flask backend (app.py)
 (recognition + TTS)                     │
      ▲                             fund_service.py
      │                              ├─ parse the query (category / period / count)
      └──speaks the answer◄──────────┤─ fetch LIVE NAV from api.mfapi.in and
                                      │  compute annualised (CAGR) returns
                                      └─ fall back to a bundled snapshot
                                         (data/funds.json) if the API is blocked
```

- **Voice** uses the browser's built-in **Web Speech API** — no API keys, no
  extra services. Speech recognition works in Chrome / Edge (and most
  Chromium browsers). Text-to-speech works nearly everywhere. If recognition
  isn't available, a text box is provided as a fallback.
- **Data** is fetched **live** from the free [mfapi.in](https://www.mfapi.in/)
  service (AMFI NAV history) and returns are computed as CAGR over 1 / 3 / 5
  years. If the network is unavailable, the app transparently falls back to the
  bundled snapshot in `data/funds.json` — each fund card shows a **live** or
  **snapshot** badge so you always know which you're seeing.

---

## Run it

```bash
cd mutual_fund
python3 -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install -r requirements.txt
python app.py
```

Then open **http://localhost:5000** in Chrome or Edge, tap the 🎤 mic, and ask
your question. (Microphone access requires `localhost` or HTTPS — both are fine.)

> **Note on live data:** the first query in a category fetches NAV history for
> that category's funds and caches it for 6 hours, so the first request is a
> little slower than the rest. In restricted/sandboxed networks where
> `api.mfapi.in` is blocked, the app automatically uses the bundled snapshot.

---

## What you can ask

| Intent | Example phrases |
|---|---|
| Number of results | "top **5**", "top **three**", "best **10**" (default 5) |
| Time period | "**3 year** return", "over **5 years**", "**1 year**" (default 3y) |
| Category | pharma / healthcare, technology / IT, banking / financial, infrastructure, FMCG / consumption, energy, large cap, mid cap, small cap, flexi cap, ELSS / tax saver, index |

Ask with no category to rank across **all** funds.

---

## Project layout

```
mutual_fund/
├── app.py                # Flask app + JSON API
├── fund_service.py       # query parsing, live fetch + fallback, ranking, TTS text
├── data/funds.json       # curated fund universe + snapshot returns
├── templates/index.html  # single-page voice UI
├── static/style.css      # styling
├── static/app.js         # Web Speech API wiring + rendering
├── requirements.txt
└── README.md
```

## API

`GET /api/query?q=<question>&live=<0|1>`

```jsonc
{
  "query": "top 5 pharma funds by 3 year return",
  "parsed": { "category": "pharma", "period": "3y", "limit": 5 },
  "source": "live",              // live | mixed | snapshot
  "results": [
    { "name": "...", "amc": "...", "category": "pharma",
      "category_label": "pharma & healthcare", "scheme_code": 118759,
      "period": "3y", "return_pct": 27.9,
      "returns_all": { "1y": 26.3, "3y": 28.4, "5y": 20.5 }, "live": true }
  ],
  "recommendation": {
    "name": "DSP Healthcare Fund", "return_pct": 28.4, "period": "3y",
    "reason": "DSP Healthcare Fund looks the strongest of these — it tops the list…"
  },
  "spoken": "Here are the top 5 pharma & healthcare mutual funds by 3 year return…"
}
```

---

## Disclaimer

This is an **educational demo**, not investment advice. Snapshot returns in
`data/funds.json` are illustrative; verify current figures with the AMC or a
registered advisor before investing.
