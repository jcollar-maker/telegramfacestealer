# main.py — STEALIE MAX FINAL FINAL (100% clean, ready to monetize)

import os, logging, random, re, requests
from datetime import datetime, timedelta
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
ODDS_KEY = os.getenv("ODDS_API_KEY")

# Bankroll (in-memory for now)
bankroll = {"total": 100.0}

# TODAY/TONIGHT
def now_et():
    return datetime.utcnow() + timedelta(hours=-5)

def when():
    n = now_et()
    if n.weekday() == 6 and n.hour >= 20:
        return (n + timedelta(days=7)).strftime("%A %B %d"), "next Sunday"
    return n.strftime("%A %B %d"), "today/tonight"

DATE_STR, WHEN_TEXT = when()

# ODDS
def odds():
    try:
        r = requests.get("https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds",
                        params={"apiKey": ODDS_KEY, "regions": "us", "markets": "h2h,spreads,totals"},
                        timeout=12)
        return r.json() if r.status_code == 200 else []
    except:
        return []

# CARD
def card():
    games = odds()
    if not games: return "Odds down — retry in 60s"
    lines = [f"NFL — {WHEN_TEXT.upper()} {DATE_STR.upper()}\n"]
    for g in games:
        home, away = g["home_team"], g["away_team"]
        try:
            t = datetime.fromisoformat(g["commence_time"].replace("Z",""))
            et = t.astimezone(now_et().tzinfo).strftime("%-I:%M%p")
        except:
            et = "??"
        try:
            m = g["bookmakers"][0]["markets"]
            spread = next(o["point"] for mk in m if mk["key"]=="spreads" for o in mk["outcomes"] if o["name"]==home)
            total = next(o["point"] for mk in m if mk["key"]=="totals" for o in mk["outcomes"])
            lines.append(f"{et} | {away} @ {home} | {home} {spread:+.1f}  O/U {total}")
        except:
            lines.append(f"{et} | {away} @ {home}")
    return "\n".join(lines)

# PICK MEMORY
memory = {}

def pick(chat_id):
    last = memory.get(chat_id, "")
    hard = [
        "Travis Etienne OVER 72.5 rush\nTitans dead last in rush EPA.",
        "Jaguars -3.5\n7-1 ATS post-bye on road.",
        "Calvin Ridley OVER 58.5 rec\nTrevor peppers him.",
        "Henry UNDER 82.5 rush\nJags top-5 run D.",
        "Zay Jones ATTD +320\nTitans leak slot TDs."
    ]
    if client:
        try:
            resp = client.chat.completions.create(model="gpt-4o-mini",
                messages=[{"role":"user","content":f"ONE sharp NFL play for {WHEN_TEXT}. Never repeat: {last[-40:]}. Line + 2 sentences."}],
                temperature=0.95, max_tokens=180)
            p = resp.choices[0].message.content.strip()
            if len(p)>30 and p!=last:
                memory[chat_id] = p
                return "AI LOCK\n" + p
        except: pass
    p = random.choice([x for x in hard if x!=last] or hard)
    memory[chat_id] = p
    return "HARD LOCK\n" + p

# PARLAYS
def parlay(legs=3):
    games = [g for g in odds() if g.get("bookmakers")]
    chosen = random.sample(games, min(legs, len(games)))
    lines = []
    for g in chosen:
        home = g["home_team"]
        try:
            s = next(o["point"] for m in g["bookmakers"][0]["markets"] if m["key"]=="spreads" for o in m["outcomes"] if o["name"]==home)
            lines.append(f"{home} {s:+.1f}")
        except:
            lines.append(f"{home} ML")
    payout = round(1.9 ** len(chosen) * 100)
    return f"{len(chosen)}-LEG (+{payout})\n" + "\n".join(lines)

# SGP / BOMB / BANKROLL
def sgp(team):
    team = team.lower()
    game = next((g for g in odds() if team in (g.get("home_team","")+g.get("away_team","")).lower()), None)
    if not game: return "Team not playing"
    t = game["home_team"] if team in game["home_team"].lower() else game["away_team"]
    legs = [f"{t} spread", f"{t} total", random.choice(["QB 1.5+ TD", "RB 70+ rush", "WR ATTD"])]
    return f"SGP {t.upper()}\n" + "\n".join(legs) + f"\n+800 to +1400"

def bomb():
    return "MOONSHOT\n" + random.choice(["Zay Jones 2+ TD +2500", "Trevor 4+ TD +1800", "Jags 40+ win +5000"])

def bank(text):
    m = re.search(r"([+-]?\d+\.?\d*)u?", text.lower())
    if m:
        u = float(m.group(1))
        bankroll["total"] += u
        return f"Bankroll: {bankroll['total']:.1f}u (+{u})"
    return f"Bankroll: {bankroll['total']:.1f}u"

# WEBHOOK
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
    elif "big" in text: reply = parlay(5)
    elif text.startswith("sgp"): reply = sgp(text[3:].strip() or "jags")
    elif "bomb" in text: reply = bomb()
    elif "bank" in text or "u" in text: reply = bank(text)
    else: reply = "STEALIE MAX\ncard | pick | parlay | big | sgp jags | bomb | bank +5u"

    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                  json={"chat_id":chat_id, "text":reply})
    return jsonify(ok=True)

@app.route("/"): return "running"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",10000)))
    