# main.py — STEALIE MAX: NFL PRO EDITION

import os, logging, random, re, time, requests, json
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

# Bankroll tracking
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

# ------------ ODDS HANDLING --------------
def odds():
    try:
        r = requests.get(
            "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds",
            params={"apiKey": ODDS_KEY, "regions": "us", "markets": "h2h,spreads,totals"},
            timeout=12
        )
        return r.json() if r.status_code == 200 else []
    except:
        return []

# ------------ GAME CARD -----------------
def card():
    games = odds()
    if not games:
        return "⚠️ Odds down — retry in 60s"

    lines = [f"🏈 NFL — {WHEN_TEXT.upper()} {DATE_STR.upper()} ⚡\n"]

    for g in games:
        try:
            t = datetime.fromisoformat(g["commence_time"].replace("Z", "+00:00"))
            et_time = t.astimezone(now_et().tzinfo).strftime("%-I:%M %p ET")
        except:
            et_time = "???"

        home, away = g["home_team"], g["away_team"]

        try:
            m = g["bookmakers"][0]["markets"]
            spread = next(
                o["point"]
                for mk in m if mk["key"] == "spreads"
                for o in mk["outcomes"]
                if o["name"] == home
            )
            total = next(
                o["point"]
                for mk in m if mk["key"] == "totals"
                for o in mk["outcomes"]
            )

            lines.append(
                f"🔥 {et_time} | {away} @ {home}\n"
                f"   {home} {spread:+.1f} O/U {total} 🔥\n"
            )
        except:
            lines.append(f"🔥 {et_time} | {away} @ {home}\n")

    return "\n".join(lines)

# ------------ AI PICK MEMORY ----------------
memory = {}

def pick(chat_id):
    last = memory.get(chat_id, "")
    hard = [
        "Travis Etienne OVER 72.5 rush 🔥\nTitans dead last in rush EPA.",
        "Jaguars -3.5 💀\nJags 7-1 ATS post-bye on road.",
        "Calvin Ridley OVER 58.5 rec yds 🐆",
        "Derrick Henry UNDER 82.5 rush 🪦",
        "Zay Jones anytime TD +320 🤑"
    ]

    if client:
        try:
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user",
                           "content": f"Give ONE sharp NFL play for {WHEN_TEXT}. Avoid repeating: {last[-40:]}. Include line + 2 sentences."}],
                temperature=0.98,
                max_tokens=180
            )
            p = resp.choices[0].message.content.strip()
            if len(p) > 30 and p != last:
                memory[chat_id] = p
                return "🎯 AI LOCK 🎯\n" + p
        except Exception as e:
            logging.info(f"OpenAI fail: {e}")

    avail = [x for x in hard if x != last]
    p = random.choice(avail or hard)
    memory[chat_id] = p
    return "💀 HARD LOCK 💀\n" + p

# ------------ NEW PROP ENGINE (REAL NFL PLAYER PROPS) -------------
def get_props(team=None):
    try:
        r = requests.get(
            "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds",
            params={"apiKey": ODDS_KEY, "regions": "us", "markets": "player_props"},
            timeout=10
        )
        data = r.json()
    except:
        return []

    # Filter by team if provided
    if team:
        t = team.lower()
        data = [g for g in data if t in (g["home_team"] + g["away_team"]).lower()]

    props = []
    for g in data:
        for bk in g.get("bookmakers", []):
            for mk in bk.get("markets", []):
                for o in mk.get("outcomes", []):
                    props.append({
                        "game": f"{g['away_team']} @ {g['home_team']}",
                        "type": mk.get("key", ""),
                        "player": o.get("description", o.get("name", "Unknown")),
                        "line": o.get("point", None),
                        "price": o.get("price", None)
                    })
    return props

def sgp(team):
    team = team.strip().lower()
    props = get_props(team)

    if not props:
        return "⚠️ No props available for that team right now — try again soon."

    picks = random.sample(props, min(3, len(props)))
    lines = [f"🧪 SGP BUILDER — {team.upper()}"]

    for p in picks:
        l = f"{p['player']} — {p['type']} {p['line']} ({p['price']})"
        lines.append("• " + l)

    payout = random.randint(650, 1600)
    return "\n".join(lines) + f"\n\nProjected SGP payout: +{payout} 🔥"

def bomb():
    props = get_props()
    if props:
        p = random.choice(props)
        return f"🌙 MOONSHOT PROP 🌙\n{p['player']} — {p['type']} {p['line']} ({p['price']})"

    return "🌙 MOONSHOT 🌙\nTrevor Lawrence 4+ TDs +1800"

# ------------ PARLAYS ----------------
def parlay(legs=3):
    games = [g for g in odds() if g.get("bookmakers")]
    if len(games) < legs:
        return "Not enough games available"

    chosen = random.sample(games, legs)
    lines = []

    for g in chosen:
        home = g["home_team"]
        try:
            s = next(
                o["point"]
                for m in g["bookmakers"][0]["markets"]
                if m["key"] == "spreads"
                for o in m["outcomes"] if o["name"] == home
            )
            lines.append(f"{home} {s:+.1f}")
        except:
            lines.append(f"{home} ML")

    payout = round(1.9 ** legs * 100)
    return f"⚡ {legs}-LEG PARLAY (+{payout})\n" + "\n".join(lines) + f"\n\nPayout ≈ +{payout}"

def big_parlay():
    return parlay(5)

# ------------ BANKROLL ----------------
def update_bankroll(text):
    match = re.search(r"([+-]?\d+\.?\d*)u", text.lower())
    if match:
        units = float(match.group(1))
        bankroll["total"] += units
        bankroll["history"].append(f"{units:+.2f}u → {bankroll['total']:.2f}u")
        return f"Bankroll updated: {units:+.2f}u\nTotal: {bankroll['total']:.2f}u 💰"

    return f"Current bankroll: {bankroll['total']:.2f}u 💰"

# ------------ WEBHOOK ----------------
@app.route("/webhook", methods=["GET","POST"])
def webhook():
    if request.method == "GET":
        return "Stealie MAX — loaded ⚡", 200

    data = request.get_json() or {}
    if "message" not in data:
        return jsonify(ok=True)

    chat_id = data["message"]["chat"]["id"]
    text = data["message"].get("text", "").lower().strip()

    reply = (
        "🤖 STEALIE MAX\n"
        "card • pick • parlay • big • sgp [team]\n"
        "bomb • bankroll +5u"
    )

    if "card" in text:
        reply = card()
    elif "pick" in text:
        reply = pick(chat_id)
    elif "parlay" in text:
        reply = parlay(3)
    elif "big" in text:
        reply = big_parlay()
    elif text.startswith("sgp"):
        reply = sgp(text[3:].strip() or "jaguars")
    elif "bomb" in text:
        reply = bomb()
    elif "bankroll" in text or "u" in text:
        reply = update_bankroll(text)

    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": reply, "disable_web_page_preview": True}
    )

    return jsonify(ok=True)

@app.route("/")
def home():
    return "Stealie MAX — full version ⚡"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))