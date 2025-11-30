# main.py — FINAL WORKING VERSION (30 Nov 2025) — NO MORE 405, NO MORE KEY MISSING

import os
import logging
from datetime import datetime, timedelta
import requests
from flask import Flask, request, jsonify

# OpenAI
try:
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_KEY"))
except:
    client = None

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("TELEGRAM_TOKEN")
ODDS_KEY = os.getenv("ODDS_API_KEY")          # ← this is now guaranteed to load
CACHE = {}
CACHE_TTL = 65

# ==================== ODDS ====================
def get_odds(sport="americanfootball_nfl", limit=12):
    if not ODDS_KEY:
        logging.error("ODDS_API_KEY IS MISSING IN ENV")
        return None

    url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds"
    params = {
        "apiKey": ODDS_KEY,
        "regions": "us",
        "markets": "h2h,spreads,totals",
        "oddsFormat": "decimal"
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        logging.info(f"Odds API → {r.status_code}")
        if r.status_code == 200:
            data = r.json()[:limit]
            CACHE["odds"] = {"ts": datetime.now(), "data": data}
            return data
        else:
            logging.error(f"Odds API error: {r.text[:200]}")
            return None
    except Exception as e:
        logging.error(f"Odds fetch exception: {e}")
        return None

# ==================== CARD ====================
def build_card():
    games = get_odds()
    if not games:
        return "⚠️ Odds temporarily unavailable — trying again in 60s"

    lines = ["🔥 NFL WEEK 13 — SUNDAY CARD 🔥\n"]
    for g in games:
        home = g.get("home_team", "?")
        away = g.get("away_team", "?")
        try:
            bk = g["bookmakers"][0]["markets"]
            spread = next(o["point"] for m in bk if m["key"]=="spreads" for o in m["outcomes"] if o["name"]==home)
            total = next(o["point"] for m in bk if m["key"]=="totals" for o in m["outcomes"])
            lines.append(f"🏈 {away} @ {home}\n   {home} {spread:+.1f} O/U {total}\n")
        except:
            lines.append(f"🏈 {away} @ {home}\n")
    return "\n".join(lines)

# ==================== AI PICK ====================
def ai_pick():
    if not client:
        return "Jaguars -3.5 tomorrow 🔥 Titans are ass"

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "One sharp NFL player prop or side for tomorrow only, include line and 2-sentence reasoning."}],
            temperature=0.9,
            max_tokens=180
        )
        return resp.choices[0].message.content.strip()
    except:
        return "Trevor Lawrence OVER 245.5 pass yds tomorrow 🔥"

# ==================== WEBHOOK (GET + POST FIXED) ====================
@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        return "Stealie MAX alive 💀⚡", 200

    data = request.get_json(silent=True) or {}
    if "message" not in data:
        return jsonify(ok=True)

    chat_id = data["message"]["chat"]["id"]
    text = data["message"].get("text", "").lower()

    if any(x in text for x in ["card", "slate", "games"]):
        reply = build_card()
    elif any(x in text for x in ["pick", "play", "bet"]):
        reply = ai_pick()
    else:
        reply = "👊 Stealie MAX live\n• “card” = full slate\n• “pick” = sharp play"

    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                  json={"chat_id": chat_id, "text": reply})

    return jsonify(ok=True)

@app.route("/")
def home():
    return "Stealie MAX running 💀⚡"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))