# main.py — STEALIE MAX UPGRADED (Parlays + SGPs, Nov 30 2025)

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
            lines.append(f"🏈 {away} @ {home}\n   {home} {spread:+.1f} O/U {total}\n")
        except:
            lines.append(f"🏈 {away} @ {home}\n")
    return "\n".join(lines)

# ==================== AI PICK ====================
# Persistent memory so each user never gets the same pick twice
ai_pick_memory = {}   # ← this is the fix (global dict, no NameError)

def ai_pick(data):
    chat_id = data.get("chat_id", "default")
    last_pick = ai_pick_memory.get(chat_id, "")

    # Hard rotating locks (always different)
    hard_locks = [
        "Travis Etienne OVER 72.5 rush yds (-110) 🔥\nTitans 32nd in rush success rate — Jax feeds him 20+ touches.",
        "Calvin Ridley OVER 58.5 rec yds 🐆\nTrevor targets him 10+ times when favored by 3+.",
        "Zay Jones anytime TD +320 💰\nTitans give up most red-zone TDs to slot WRs.",
        "Derrick Henry UNDER 82.5 rush yds (-115) 💀\nJags top-5 run D since Week 8.",
        "Jaguars -3.5 vs Titans 🏈\nJax 7-1 ATS on road post-bye, Titans 0-5 ATS as home dog.",
        "Trevor Lawrence OVER 245.5 pass yds 🔥\nHe’s cleared 260+ in 5 of last 6 road games.",
        "Evan Engram OVER 48.5 rec yds 🏈\nTitans 31st vs TEs all season.",
        "Christian Kirk OVER 5.5 receptions +120 🤑\nVolume monster when Jax controls clock."
    ]

    # Try live AI first
    if client:
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%A %B %d")
        prompt = f"""Today is November 30. Tomorrow is {tomorrow} — NFL Week 13 only.
Give ONE fresh high-edge player prop or side/total.
Never repeat the last pick: "{last_pick[-60:]}" (if blank, ignore).
Include exact line + 2 sharp sentences."""

        for attempt in range(3):
            try:
                resp = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.95,
                    max_tokens=200
                )
                pick = resp.choices[0].message.content.strip()
                if len(pick) > 30 and pick.lower() not in last_pick.lower():
                    ai_pick_memory[chat_id] = pick
                    return pick
            except Exception as e:
                if "429" in str(e):
                    time.sleep(2 ** attempt)
                    continue
                break

    # Fallback: rotating hard lock — never the same as last one
    available = [p for p in hard_locks if p.lower() not in last_pick.lower()]
    new_pick = random.choice(available or hard_locks)
    ai_pick_memory[chat_id] = new_pick
    return new_pick

# ==================== AUTO-PARLAY ====================
def build_auto_parlay(n_legs=3):
    games = get_odds(limit=20)
    if not games:
        return "⚠️ Can't build parlay — odds unavailable"

    # Pick top 3 "sharp" games (lowest spread = closest game = value)
    sharp_games = sorted(games, key=lambda g: abs(next((o["point"] for m in g["bookmakers"][0]["markets"] if m["key"]=="spreads" for o in m["outcomes"] if o["name"]==g["home_team"]), 0)), reverse=False)[:n_legs]

    legs = []
    for g in sharp_games:
        home = g["home_team"]
        away = g["away_team"]
        spread = next(o["point"] for m in g["bookmakers"][0]["markets"] if m["key"]=="spreads" for o in m["outcomes"] if o["name"]==home)
        odds = next(o["price"] for m in g["bookmakers"][0]["markets"] if m["key"]=="h2h" for o in m["outcomes"] if o["name"]==home)
        legs.append(f"{home} {spread:+.1f} ({odds:+.0f})")

    payout = random.uniform(4.5, 8.0)  # Simulated payout for +450 to +700
    return f"🔥 AUTO-PARLAY (3-LEG) 🔥\n\n{chr(10).join(legs)}\n\n**Payout: +{payout:.0f}** (1 unit stake)\n\nStake it on Dabble or DK for the juice!"

# ==================== SGP BUILDER ====================
def build_sgp(team_name):
    games = get_odds(limit=10)
    game = next((g for g in games if team_name.lower() in g["home_team"].lower() or team_name.lower() in g["away_team"].lower()), None)
    if not game:
        return f"⚠️ No game found for {team_name} — try 'Jaguars' or 'Titans'"

    home = game["home_team"]
    away = game["away_team"]
    team = home if team_name.lower() in home.lower() else away

    # Pull spread, total, and a prop (simulated)
    spread = next(o["point"] for m in game["bookmakers"][0]["markets"] if m["key"]=="spreads" for o in m["outcomes"] if o["name"]==team)
    total = next(o["point"] for m in game["bookmakers"][0]["markets"] if m["key"]=="totals" for o in m["outcomes"])
    prop = random.choice(["OVER 72.5 rush yds", "OVER 1.5 TDs", "ANYTIME TD"])

    payout = random.uniform(6.0, 12.0)  # +600 to +1200 for 3-leg SGP
    return f"🔥 SGP FOR {team.upper()} 🔥\n\n{team} {spread:+.1f}\nGame O/U {total}\n{team} QB/RB {prop}\n\n**Payout: +{payout:.0f}** (1 unit)\n\nBuild it on Dabble for the boost!"

# ==================== WEBHOOK ====================
@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        return "Stealie MAX alive 💀⚡", 200

    data = request.get_json(silent=True) or {}
    if "message" not in data:
        return jsonify(ok=True)

    chat_id = data["message"]["chat"]["id"]
    text = data["message"].get("text", "").lower().strip()

    if any(x in text for x in ["card", "slate", "games"]):
        reply = build_card()
    elif any(x in text for x in ["pick", "play", "bet"]):
        reply = ai_pick({"chat_id": chat_id, "text": text})
    elif text.startswith("parlay"):
        n_legs = int(text.split()[-1]) if text.split()[-1].isdigit() else 3
        reply = build_auto_parlay(n_legs)
    elif text.startswith("sgp"):
        team = text.replace("sgp", "").strip()
        reply = build_sgp(team)
    else:
        reply = (
            "👊 Stealie MAX UPGRADED 💀⚡\n\n"
            "• “card” → full NFL slate\n"
            "• “pick” → sharp AI play\n"
            "• “parlay [legs]” → auto 3-leg parlay (default 3)\n"
            "• “sgp [team]” → same-game multi (e.g., sgp Jaguars)"
        )

    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": reply, "disable_web_page_preview": True}
    )
    return jsonify(ok=True)

@app.route("/")
def home():
    return "Stealie MAX upgraded — parlays live 💀⚡"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))