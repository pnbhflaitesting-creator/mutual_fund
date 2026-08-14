"""Flask backend for the Mutual Fund Voice Bot.

Serves the single-page voice UI and a small JSON API that the browser calls
after transcribing the user's speech.
"""

from flask import Flask, jsonify, render_template, request

from fund_service import FundService, CATEGORY_LABELS

app = Flask(__name__)
service = FundService()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/query")
def query():
    """Answer a natural-language question about top funds.

    Query params:
      q     - the raw (spoken) question, e.g. "top 5 pharma funds by 3 year return"
      live  - "0" to force the bundled snapshot (skip the live API)
    """
    text = request.args.get("q", "")
    use_live = request.args.get("live", "1") != "0"

    parsed = service.parse_query(text)
    ranked = service.get_top_funds(
        category=parsed["category"],
        period=parsed["period"],
        limit=parsed["limit"],
        live=use_live,
    )
    spoken = service.build_spoken_summary(parsed, ranked)

    return jsonify({
        "query": text,
        "parsed": parsed,
        "source": ranked["source"],
        "results": ranked["results"],
        "spoken": spoken,
    })


@app.route("/api/categories")
def categories():
    """List the categories the bot understands (for UI hints)."""
    return jsonify({"categories": CATEGORY_LABELS})


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "funds": len(service.funds)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
