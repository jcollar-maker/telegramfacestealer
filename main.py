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
        return "Jaguars -3.5 vs Titans (1 PM tomorrow) 🔥\nJax rolling 7-1 ATS on road, Titans last in EPA vs rush."

    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%A %B %d")
    prompt = f"""You are the sharpest NFL capper alive.
Give ONE single NFL player prop or side/total for games on {tomorrow} ONLY (Week 13).
Include the exact line and 2 sentences of reasoning.
Example format:
Travis Etienne OVER 72.5 rush yards (-110)
Titans can't stop the run (32nd in success rate) and Jags feed him 20+ touches when favored.

Do not mention college, playoffs, or any other date."""

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6,
            max_tokens=180
        )
        return resp.choices[0].message.content.strip()
    except:
        return "Jaguars -3.5 vs Titans tomorrow 🔥\nJax 7-1 ATS on road post-bye, Titans dead last in rush defense EPA."

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