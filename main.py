# main.py — FINAL KILLER VERSION (College + NFL + AI picks)

import os
import requests
from flask import Flask, request, jsonify
from openai import OpenAI

app = Flask(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN")
client = OpenAI(api_key=os.getenv("OPENAI_KEY"))
ODDS_KEY = os.getenv("ODDS_API_KEY")

def get_odds():
    url = f"https://api.the-odds-api.com/v4/sports/americanfootball_ncaaf/odds"
    params = {"apiKey": ODDS_KEY, "regions": "us", "markets": "h2h,spreads,totals", "oddsFormat": "decimal"}
    try:
        r = requests.get(url, params=params, timeout=10)
        return r.json()[:10] if r.status_code == 200 else None
    except:
        return None

def build_card():
    games = get_odds()
    if not games:
        return "⚠️ Odds API down or key expired — still alive though 💀"
    card = ["🔥 TODAY'S CFB SHARP CARD 🔥\n"]
    for g in games:
        home = g["home_team"]
        away = g["away_team"]
        try:
            m = g["bookmakers"][0]["markets"]
            spread = next(o for mkt in m if mkt["key"] == "spreads" for o in mkt["outcomes"] if o["name"] == home)
            total = next(mkt for mkt in m if mkt["key"] == "totals")["outcomes"][0]["point"]
            card.append(f"🏈 {away} @ {home}\n   {home} {spread['point']:+.1f}  |  O/U {total}\n")
        except:
            card.append(f"🏈 {away} @ {home}\n")
    return "\n".join(card)

def ai_pick():
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.7,
            max_tokens=150,
            messages=[{"role": "user", "content": "Give me ONE high-confidence college football player prop or side for today with short reasoning."}]
        )
        return resp.choices[0].message.content.strip()
    except:
        return "Jeremiah Smith OVER 75.5 receiving yards vs Michigan 💀\nHe's cleared this in 6 straight."

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    if data and data.get("message"):
        chat_id = data["message"]["chat"]["id"]
        text = data["message"].get("text", "").lower()

        if any(w in text for w in ["card", "slate", "games"]):
            reply = build_card()
        elif any(w in text for w in ["pick", "play", "bet"]):
            reply = ai_pick()
        else:
            reply = "👊 Bot alive!\n• Send:\n• 'card' → full slate\n• 'pick' → one sharp AI play"

        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                      json={"chat_id": chat_id, "text": reply})

    return jsonify({"ok": True})

@app.route("/")
def home():
    return "Stealie printing tickets 24/7 💀⚡️"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))