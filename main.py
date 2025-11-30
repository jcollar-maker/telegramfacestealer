import os
import logging
from datetime import datetime, timedelta
import requests
from flask import Flask, request, jsonify
import random
import re

try:
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_KEY"))
except:
    client = None

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("TELEGRAM_TOKEN")
ODDS_KEY = os.getenv("ODDS_API_KEY")

bankroll = {"total": 100.0}

def now_et():
    return datetime.utcnow() + timedelta(hours=-5)

def when():
    n = now_et()
    if n.weekday() == 6 and n.hour >= 20:
        return (n + timedelta(days=7)).strftime("%A %B %d"), "next Sunday"
    return n.strftime("%A %B %d"), "today/tonight"

DATE_STR, WHEN_TEXT = when()

def odds():
    try:
        r = requests.get("https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds", params={"apiKey": ODDS_KEY, "regions": "us", "markets": "h2h,spreads,totals", "oddsFormat": "decimal"}, timeout=12)
        return r.json() if r.status_code == 200 else []
    except:
        return []

def card():
    games = odds()
    if not games:
        return "Odds down - retry in 60s"
    lines = [f"NFL WEEK 13 - {WHEN_TEXT.upper()} {DATE_STR.upper()}"]
    for g in games:
        home = g["home_team"]
        away = g["away_team"]
        try:
            spread = next(o["point"] for m in g["bookmakers"][0]["markets"] if m["key"]=="spreads" for o in m["outcomes"] if o["name"]==home)
            total = next(o["point"] for m in g["bookmakers"][0]["markets"] if m["key"]=="totals" for o in m["outcomes"])
            lines.append(f"{away} @ {home} | {home} {spread:+.1f}  O/U {total}")
        except:
            lines.append(f"{away} @ {home}")
    return "\n".join(lines)

memory = {}

def pick(chat_id):
    last = memory.get(chat_id, "")
    hard = [
        "Travis Etienne OVER 72.5 rush\nTitans dead last in rush EPA.",
        "Jaguars -3.5\n7-1 ATS post-bye.",
        "Calvin Ridley OVER 58.5 rec\nTrevor peppers him.",
        "Derrick Henry UNDER 82.5 rush\nJags top-5 run D.",
        "Zay Jones anytime TD +320\nTitans leak red-zone TDs."
    ]
    if client:
        try:
            resp = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"user","content":f"ONE sharp NFL play for {WHEN_TEXT}. Line + 2 sentences."}], temperature=0.95, max_tokens=180)
            p = resp.choices[0].message.content.strip()
            if len(p)>30 and p!=last:
                memory[chat_id] = p
                return "AI LOCK\n" + p
        except:
            pass
    p = random.choice([x for x in hard if x!=last] or hard)
    memory[chat_id] = p
    return "HARD LOCK\n" + p

def parlay(legs=3):
    games = [g for g in odds() if g.get("bookmakers")]
    if len(games) < legs: return "Not enough games"
    chosen = random.sample(games, legs)
    lines = []
    for g in chosen:
        home = g["home_team"]
        try:
            s = next(o["point"] for m in g["bookmakers"][0]["markets"] if m["key"]=="spreads" for o in m["outcomes"] if o["name"]==home)
            lines.append(f"{home} {s:+.1f}")
        except:
            lines.append(f"{home} ML")
    payout = round(1.9 ** legs * 100)
    return f"{legs}-LEG PARLAY (+{payout})\n" + "\n".join(lines) + f"\nPayout {payout}"

def big_parlay():
    return parlay(5)

def sgp(team):
    team = team.lower()
    game = next((g for g in odds() if team in (g.get("home_team","")+g.get("away_team","")).lower()), None)
    if not game: return "Team not playing"
    t = game["home_team"] if team in game["home_team"].lower() else game["away_team"]
    legs = [f"{t} spread", f"{t} total", random.choice(["QB over 1.5 TD", "RB over 70 rush", "WR anytime TD"])]
    payout = random.randint(700, 1400)
    return f"SGP {t.upper()}\n" + "\n".join(legs) + f"\nPayout +{payout}"

def bomb():
    bombs = ["Zay Jones 2+ TDs +2500", "Trevor 4+ TDs +1800", "Titans 0 pts 1H +3000", "Jags win by 40+ +5000"]
    return "MOONSHOT\n" + random.choice(bombs)

def bank(text):
    m = re.search(r"([+-]?\d+\.?\d*)u?", text.lower())
    if m:
        u = float(m.group(1))
        bankroll["total"] += u
        return f"Bankroll: {bankroll['total']:.1f}u (+{u:.1f}u)"
    return f"Bankroll: {bankroll['total']:.1f}u"

@app.route("/webhook", methods=["GET","POST"])
def webhook():
    if request.method == "GET": return "Stealie MAX alive", 200
    data = request.get_json() or {}
    if "message" not in data: return jsonify(ok=True)
    chat_id = data["message"]["chat"]["id"]
    text = data["message"].get("text","").lower().strip()

    if "card" in text: reply = card()
    elif "pick" in text: reply = pick(chat_id)
    elif "parlay" in text: reply = parlay(3)
    elif "big" in text: reply = big_parlay()
    elif text.startswith("sgp"): reply = sgp(text[3:].strip() or "jaguars")
    elif "bomb" in text: reply = bomb()
    elif "bankroll" in text or "u" in text: reply = bank(text)
    else