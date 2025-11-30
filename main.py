# main.py — STEALIE MAX FULL DEGENERATE EDITION (LINE 38 FIXED)

import os, logging, random, re, time, requests
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

# Bankroll
bankroll = {"total": 100.0, "history": []}

# TODAY/TONIGHT
def now_et():
    return datetime.utcnow() + timedelta(hours=-5)

def when():
    n = now_et()
    if n.weekday() == 6 and n.hour >= 20:  # Sunday after 8 PM ET
        return (n + timedelta(days=7)).strftime("%A %B %d"), "next Sunday"
    return n.strftime("%A %B %d"), "today/tonight"

DATE_STR, WHEN_TEXT = when()

# ODDS
def odds():
    try:
        r = requests.get("https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds",
                        params={"apiKey": ODDS_KEY, "regions": "us", "markets": "h2h,spreads,totals", "oddsFormat": "decimal"},
                        timeout=12)
        return r.json() if r.status_code == 200 else []
    except:
        return []

# CARD
def card():
    games = odds()
    if not games:
        return "Odds down — retry in 60s"
    lines = [f"NFL WEEK 13 — {WHEN_TEXT.upper()} {DATE_STR.upper()}"]
    for g in games:
        try:
            t = datetime.fromisoformat(g["commence_time"].replace("Z", "+00:00"))
            et_time = t.astimezone(now_et().tzinfo).strftime("%-I:%M %p ET")
        except:
            et_time = "???"
        home, away = g["home_team"], g["away_team"]
        try:
            m = g["bookmakers"][0]["markets"]
            spread = next(o["point"] for mk in m if mk["key"]=="spreads" for o in mk["outcomes"] if o["name"]==home)
            total = next(o["point"] for mk in m if mk["key"]=="totals" for o in mk["outcomes"])
            lines.append(f"{et_time} | {away} @ {home}\n   {home} {spread:+.1f} O/U {total}")
        except:
            lines.append(f"{et_time} | {away} @ {home}")
    return "\n".join(lines)

# PICK
memory = {}

def pick(chat_id):
    last = memory.get(chat_id, "")
    hard = [
        "Travis Etienne OVER 72.5 rush\nTitans dead last in rush EPA.",
        "Jaguars -3.5\nJax 7-1 ATS post-bye.",
        "Calvin Ridley OVER 58.5 rec yds\nTrevor peppers him when favored.",
        "Derrick Henry UNDER 82.5 rush\nJags top-5 run D.",
        "Zay Jones anytime TD +320\nTitans leak red-zone TDs to slot WRs."
    ]
    if client:
        try:
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role":"user","content":f"Give ONE sharp NFL play for {WHEN_TEXT} only. Never repeat: {last[-40:]}. Line + 2 sentences."}],
                temperature=0.95, max_tokens=180)
            p = resp.choices[0].message.content.strip()
            if len(p)>30 and p!=last:
                memory[chat_id] = p
                return "AI LOCK\n" + p
        except:
            pass
    avail = [x for x in hard if x!=last]
    p = random.choice(avail or hard)
    memory[chat_id] = p
    return "HARD LOCK\n" + p

# PARLAY / BIG / SGP / BOMB
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
    return f"{legs}-LEG PARLAY (+{payout})\n" + "\n".join(lines) + f"\n\nPayout ≈ +{payout}"

def big_parlay():
    return parlay(5)

def sgp(team):
    team = team.lower()
    game = next((g for g in odds() if team in (g.get("home_team","")+g.get("away_team","")).lower()), None)
    if not game: return "Team not playing"
    t = game["home_team"] if team in game["home_team"].lower() else game["away_team"]
    legs = [f"{t} spread", f"{t} team total", random.choice(["QB over 1.5 TD", "RB over 70 rush", "WR anytime TD"])]
    payout = random.randint(700, 1400)
    return f"SGP — {t.upper()}\n" + "\n".join(legs) + f"\n\nPayout ≈ +{payout}"

def bomb():
    bombs = ["Zay Jones 2+ TDs +2500", "Trevor 4+ TDs +1800", "Titans 0 pts 1H +3000", "Jags win by 40+ +5000"]
    return "MOONSHOT\n" + random.choice(bombs)

# BANKROLL
def update_bankroll(text):
    match = re.search(r"([+-]?\d+\.?\d*)u?", text.lower())
    if match:
        units = float(match.group(1))
        bankroll["total"] += units
        bankroll["history"].append(f"{units:+.2f}u → {bankroll['total']:.2f}u")
        return f"Bankroll updated: {units:+.2f}u\nTotal: {bankroll['total']:.2f}u"
    return f"Current bankroll: {bankroll['total']:.2f}u"

# WEBHOOK
@app.route("/webhook", methods=["GET","POST"])
def webhook():
    if request.method == "GET": return "Stealie MAX alive", 200
    data = request.get_json() or {}
    if "message" not in data: return jsonify(ok=True)

    chat_id = data["message"]["chat"]["id"]
    text = data["message"].get("text","").lower().strip()

    reply = "STEALIE MAX\ncard • pick • parlay • big • sgp [team]\nbomb • bankroll +5u"

    if "card" in text: reply = card()
    elif "pick" in text: reply = pick(chat_id)
    elif "parlay" in text: reply = parlay(3)
    elif "big" in text: reply = big_parlay()
    elif text.startswith("sgp"): reply = sgp(text[3:].strip() or "jaguars")
    elif "bomb" in text: reply = bomb()
    elif "bankroll" in text or "u" in text: reply = update_bankroll(text)
    else: reply = reply

    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                  json={"chat_id": chat_id, "text": reply, "disable_web_page_preview": True})
    return jsonify(ok=True)

@app.route("/"): return "Stealie MAX running"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",10000)))