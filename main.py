# main.py - battle-ready version (Nov 2025)

import os
import requests
from flask import Flask, request, jsonify
from openai import OpenAI

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_KEY")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

client = OpenAI(api_key=OPENAI_KEY)
app = Flask(__name__)

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


def get_odds(sport_key="americanfootball_ncaaf"):  # default to college, change to nfl if you want
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "us",
        "markets": "h2h,spreads,totals,player_pass_yds,player_rush_yds,player_recv_yds",
        "oddsFormat": "decimal",
        "dateFormat": "iso"
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return None


def build_game_summary():
    games = get_odds("americanfootball_ncaaf") or get_odds("americanfootball_nfl")
    if not games:
        return "⚠️ Odds API down or key expired."

    lines = []
    for game in games[:8]:  # top 8 games
        home = game["home_team"]
        away = game["away_team"]
        commence = game["commence_time"][:10]

        try:
            bk = game["bookmakers"][0]["markets"]
            # Find spread
            spread = next(m for m in bk if m["key"] == "spreads")
            home_spread = next(o for o in spread["outcomes"] if o["name"] == home)["point"]
            total = next(m for m in bk if m["key"] == "totals")
            over = next(o for o in total["outcomes"] if o["point"] > 0)["point"]

            lines.append(f"🏈 {away} @ {home}\n   {home} {home_spread:+.1f} | O/U {over}\n   {commence}")
        except:
            lines.append(f"🏈 {away} @ {home} – {commence}")

    return "🔥 Today's Sharp Card:\n\n" + "\n\n".join(lines)


def ai_pick_engine(user_message):
    odds_data = get_odds("americanfootball_ncaaf") or get_odds("americanfootball_nfl")
    context = "You are an elite sharp sports bettor. Give ONE strong college or NFL player prop or side/total with reasoning under 100 words."

    if odds_data:
        context += f"\n\nLive games snippet: {str(odds_data[:3])}"

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.7,
            max_tokens=200,
            messages=[
                {"role": "system", "content": context},
                {"role": "user", "content": user_message}
            ]
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"AI choked: {e}"


@app.route("/webhook", methods=["POST"])
def webhook():
    update = request.get_json()

    if "message" in update:
        chat_id = update["message"]["chat"]["id"]
        text = update["message"].get("text", "").strip().lower()

        if any(x in text for x in ["card", "slate", "games", "today"]):
            reply = build_game_summary()
        elif any(x in text for x in ["pick", "play", "bet", "sharp"]):
            reply = ai_pick_engine(text)
        else:
            reply = "👊 Send me:\n• “card” → today’s slate\n• “pick” → one sharp AI play\n• or just ask anything NFL/CFB"

        requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": reply, "parse_mode": "Markdown"}
        )

    return jsonify({"ok": True})


# Health check so Railway/Render doesn’t kill it
@app.route("/")
def home():
    return "Bot alive 🤖"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))