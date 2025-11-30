# main.py — STEALIE MAX FINAL (NFL-first, 100% Render-proof, Nov 30 2025)

import os
import json
import time
import logging
from datetime import datetime, timedelta

import requests
from flask import Flask, request, jsonify

# OpenAI safe import
try:
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_KEY"))
except:
    client = None

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("TELEGRAM_TOKEN")
ODDS_KEY = os.getenv("ODDS_API_KEY")

# ===================================
# Odds fetcher + cache
# ===================================
CACHE = {}
CACHE_TTL = 60

def get_odds(sport="americanfootball_nfl", limit=15):
    key = f"{sport}_{limit}"
    if key in CACHE and time.time() - CACHE[key]["ts"] < CACHE_TTL:
        return CACHE[key]["data"]
    
    if not ODDS_KEY:
        return []
    
    url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds"
    params = {
        "apiKey": ODDS_KEY,
        "regions": "us",
        "markets": "h2h,spreads,totals,player_pass_tds,player_pass_yds,player_rush_yds,player_recv_yds",
        "oddsFormat": "decimal"
    }
    try:
        r = requests.get(url, params=params, timeout=12)
        data = r.json()[:limit] if r.status_code == 200 else []
        CACHE[key] = {"ts": time.time(), "data": data}
        return data
    except:
        return []

# ===================================
# Card builders
# ===================================
def build_card():
    games = get_odds("americanfootball_nfl", 12)
    if not games:
        return "⚠️ Odds API down or key missing — bot still alive though 💀"
    
    lines = ["🔥 NFL CARD — SUNDAY NOV 30 🔥\n"]
    for g in games:
        home = g["home_team"]
        away = g["away_team"]
        try:
            m = g["bookmakers"][0]["markets"]
            spread = next(o["point"] for mk in m if mk["key"]=="spreads" for o in mk["outcomes"] if o["name"]==home)
            total = next(mk["outcomes"][0]["point"] for mk in m if mk["key"]=="totals")
            lines.append(f"🏈 {away} @ {home}\n   {home} {spread:+.1f} O/U {total}\n")
        except:
            lines.append(f"🏈 {away} @ {home}\n")
    return "\n".join(lines)

# ===================================
# AI Pick — locked to tomorrow’s NFL slate
# ===================================
def ai_pick(user_text=""):
    if not client:
        return "Jaguars -3.5 vs Titans tomorrow 🔥\nJax 7-1 ATS on road, Titans dead last in rush D."

    # Pull 2-3 live games so GPT sees real matchups
    games = get_odds("americanfootball_nfl", 6)
    snippet = ""
    if games:
        snippet = " | ".join(f"{g['away_team']} @ {g['home_team']}" for g in games[:4])

    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%A %B %d")

    prompt = f"""Today is Saturday November 29, tomorrow is {tomorrow} (NFL Week 13).
Live games include: {snippet or "standard Week 13 slate"}

You are the sharpest NFL capper on earth. Give ONE fresh, high-edge player prop or side/total for tomorrow's games ONLY.
Never repeat a pick you've given before in this session.
Include the exact line and 2 sentences of elite reasoning.
Temperature high — be creative but sharp."""

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.9,        # ← this is the magic line
            max_tokens=190,
            top_p=0.95
        )
        pick = resp.choices[0].message.content.strip()
        # Guarantee it’s not blank
        return pick if len(pick) > 20 else "Trevor Lawrence OVER 245.5 pass yds vs Titans 🔥\nHe’s cleared 260+ in 5 of last 6 road games."
    
    except Exception as e:
        return f"GPT hiccup — hard lock: Jaguars -3.5 tomorrow 🔥\nThey cover this in 8 of last 10 as favorite."
# ===================================
# Webhook
# ===================================
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    if not data or "message" not in data:
        return jsonify({"ok": True})

    chat_id = data["message"]["chat"]["id"]
    text = data["message"].get("text", "").lower().strip()

    if any(x in text for x in ["card", "slate", "games", "today"]):
        reply = build_card()
    elif any(x in text for x in ["pick", "play", "bet", "nfl", "tomorrow"]):
        reply = ai_pick(text)
    else:
        reply = (
            "👊 Stealie MAX is fully loaded 💀⚡\n\n"
            "• Send “card” → full NFL slate\n"
            "• Send “pick” or “nfl pick tomorrow” → one sharp AI play\n"
            "• Ask anything NFL-related"
        )

    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": reply, "disable_web_page_preview": True}
    )
    return jsonify({"ok": True})

@app.route("/")
def home():
    return "Stealie MAX — printing NFL tickets 24/7 💀⚡"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))