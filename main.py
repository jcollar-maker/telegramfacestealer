import os
from flask import Flask, request, jsonify
import requests
import random
from datetime import datetime, timedelta

try:
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_KEY"))
except:
    client = None

app = Flask(__name__)
TOKEN = os.getenv("TELEGRAM_TOKEN")
ODDS_KEY = os.getenv("ODDS_API_KEY")

def now_et():
    return datetime.utcnow() + timedelta(hours=-5)

def when():
    n = now_et()
    if n.weekday() == 6 and n.hour >= 20:
        return (n + timedelta(days=7)).strftime("%B %d"), "next Sunday"
    return n.strftime("%B %d"), "today/tonight"

DATE, WHEN = when()

def odds():
    try:
        r = requests.get("https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds", params={"apiKey": ODDS_KEY, "regions": "us", "markets": "h2h,spreads,totals"}, timeout=10)
        return r.json() if r.status_code == 200 else []
    except:
        return []

def card():
    games = odds()
    if not games:
        return "Odds down"
    out = [f"NFL - {WHEN.upper()} {DATE}"]
    for g in games:
        h = g["home_team"]
        a = g["away_team"]
        try:
            s = next(o["point"] for m in g["bookmakers"][0]["markets"] if m["key"]=="spreads" for o in m["outcomes"] if o["name"]==h)
            t = next(o["point"] for m in g["bookmakers"][0]["markets"] if m["key"]=="totals" for o in m["outcomes"])
            out.append(f"{a} @ {h} | {h} {s:+.1f} O/U {t}")
        except:
            out.append(f"{a} @ {h}")
    return "\n".join(out)

memory = {}

def pick(chat_id):
    last = memory.get(chat_id, "")
    hard = ["Etienne OVER 72.5 rush", "Jaguars -3.5", "Ridley OVER 58.5", "Henry UNDER 82.5", "Zay Jones ATTD +320"]
    if client:
        try:
            resp = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"user","content":"One sharp NFL play today."}], max_tokens=120)
            p = resp.choices[0].message.content.strip()
            if p != last:
                memory[chat_id] = p
                return "AI LOCK\n" + p
        except:
            pass
    p = random.choice([x for x in hard if x != last] or hard)
    memory[chat_id] = p
    return "LOCK\n" + p

@app.route("/webhook", methods=["GET","POST"])
def webhook():
    if request.method == "GET":
        return "alive", 200
    data = request.get_json() or {}
    if "message" not in data:
        return jsonify(ok=True)
    chat_id = data["message"]["chat"]["id"]
    text = data["message"].get("text", "").lower()
    if "card" in text:
        reply = card()
    elif "pick" in text:
        reply = pick(chat_id)
    else:
        reply = "card or pick"
    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id": chat_id, "text": reply})
    return jsonify(ok=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))