# main.py — STEALIE MAX FINAL (Sunday November 30, 2025 — TODAY/TONIGHT)

import os
import logging
from datetime import datetime, timedelta
import requests
from flask import Flask, request, jsonify
import random
import time

# OpenAI
try:
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_KEY"))
except:
    client = None

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("TELEGRAM_TOKEN")
ODDS_KEY = os.getenv("ODDS_API_KEY")

# ==================== TODAY / TONIGHT LOGIC (NO MORE WRONG DATES) ====================
def get_target_date():
    # Simple Eastern Time (UTC-5, no DST issues in November)
    now_et = datetime.utcnow() + timedelta(hours=-5)
    if now_et.weekday() == 6 and now_et.hour >= 20:  # Sunday after 8 PM ET → next week
        target = now_et + timedelta(days=7)
        return target.strftime("%A %B %d"), "next Sunday"
    else:
        return now_et.strftime("%A %B %d"), "today/tonight"

target_date_str, when_text = get_target_date()

# ==================== ODDS ====================
def get_odds():
    if not ODDS_KEY:
        return None
    url = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"
    params = {
        "apiKey": ODDS_KEY,
        "regions": "us",
        "markets": "h2h,spreads,totals",
        "oddsFormat": "decimal"
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        if r.status_code == 200:
            return r.json()[:12]
    except:
        pass
    return None

# ==================== CARD ====================
def build_card():
    games = get_odds()
    if not games:
        return "Odds temporarily down — retry in 60s"
    lines = [f"NFL WEEK 13 — {when_text.upper()} {target_date_str.upper()}\n"]
    for g in games:
        home = g.get("home_team")
        away = g.get("away_team")
        try:
            m = g["bookmakers"][0]["markets"]
            spread = next(o["point"] for mk in m if mk["key"]=="spreads" for o in mk["outcomes"] if o["name"]==home)
            total = next(o["point"] for mk in m if mk["key"]=="totals" for o in mk["outcomes"])
            lines.append(f"{away} @ {home}\n   {home} {spread:+.1f} O/U {total}\n")
        except:
            lines.append(f"{away} @ {home}\n")
    return "\n".join(lines)

# ==================== AI PICK (NO REPEATS) ====================
pick_memory = {}

def ai_pick(chat_id):
    last = pick_memory.get(chat_id, "")

    hard_locks = [
        "Travis Etienne OVER 72.5 rush yds (-110)\nTitans dead last in rush EPA — feed the beast.",
        "Jaguars -3.5 vs Titans\nJax 7-1 ATS on road post-bye.",
        "Calvin Ridley OVER 58.5 rec yds\nTrevor peppers him when favored.",
        "Derrick Henry UNDER 82.5 rush yds\nJags top-5 run D last 6 weeks.",
        "Zay Jones anytime TD +320\nTitans give up most red-zone TDs to slot.",
        "Trevor Lawrence OVER 245.5 pass yds\nCleared 260+ in 5/6 road games.",
        "Evan Engram OVER 48.5 rec yds\nTitans 31st vs TEs."
    ]

    if client:
        prompt = f"Today is Sunday November 30, 2025 — NFL Week 13 games are {when_text}.\nGive ONE sharp player prop or side for TODAY's games only.\nNever repeat this: \"{last[-50:]}\"\nExact line + 2 sentences."

        for _ in range(3):
            try:
                resp = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.95,
                    max_tokens=180
                )
                pick = resp.choices[0].message.content.strip()
                if len(pick) > 30 and pick != last:
                    pick_memory[chat_id] = pick
                    return pick
            except Exception as e:
                if "429" in str(e):
                    time.sleep(2)
                continue

    # Fallback rotation
    available = [p for p in hard_locks if p != last]
    pick = random.choice(available or hard_locks)
    pick_memory[chat_id] = pick
    return pick

# ==================== WEBHOOK ====================
@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        return "Stealie MAX alive", 200

    data = request.get_json() or {}
    if "message" not in data:
        return jsonify(ok=True)

    chat_id = data["message"]["chat"]["id"]
    text = data["message"].get("text", "").lower()

    if any(cmd in text for cmd in ["card", "slate", "games"]):
        reply = build_card()
    elif any(cmd in text for cmd in ["pick", "play", "bet"]):
        reply = ai_pick(chat_id)
    else:
        reply = "Stealie MAX live\n• card → full slate\n• pick → sharp play"

    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": reply}
    )
    return jsonify(ok=True)

@app.route("/")
def home():
    return "Stealie MAX running"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))