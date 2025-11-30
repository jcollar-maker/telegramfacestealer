import os
import requests
from flask import Flask, request, jsonify
from openai import OpenAI
from datetime import datetime, timedelta

app = Flask(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN")
client = OpenAI(api_key=os.getenv("OPENAI_KEY"))
ODDS_KEY = os.getenv("ODDS_API_KEY")

# -----------------------------------------------------------
# ODDS API WRAPPER
# -----------------------------------------------------------

def get_odds(sport="americanfootball_ncaaf"):
    """Fetch odds for a given sport."""
    url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds"
    params = {
        "apiKey": ODDS_KEY,
        "regions": "us",
        "markets": "h2h,spreads,totals",
        "oddsFormat": "decimal"
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            return r.json()[:10]
        return None
    except:
        return None


# -----------------------------------------------------------
# BUILD TODAY'S CARD
# -----------------------------------------------------------

def build_card():
    games = get_odds("americanfootball_ncaaf")

    if not games:
        return "⚠️ Odds API down or key expired — still alive though 💀"

    card = ["🔥 TODAY'S CFB SHARP CARD 🔥\n"]

    for g in games:
        home = g["home_team"]
        away = g["away_team"]

        try:
            markets = g["bookmakers"][0]["markets"]

            # Spread
            spread_market = next(m for m in markets if m["key"] == "spreads")
            spread = next(o for o in spread_market["outcomes"] if o["name"] == home)
            spread_val = spread["point"]

            # Total
            total_market = next(m for m in markets if m["key"] == "totals")
            total = total_market["outcomes"][0]["point"]

            card.append(
                f"🏈 {away} @ {home}\n"
                f"   {home} {spread_val:+.1f}  |  O/U {total}\n"
            )

        except:
            card.append(f"🏈 {away} @ {home}\n")

    return "\n".join(card)


# -----------------------------------------------------------
# AI PICK GENERATOR
# -----------------------------------------------------------

def ai_pick(user_text=""):
    try:
        # Detect NFL or CFB
        nfl_mode = any(word in user_text.lower() for word in ["nfl", "tomorrow", "sunday", "pro"])

        if nfl_mode:
            sport = "NFL"
            target_sport = "americanfootball_nfl"
            date_context = f"Focus on Week 13 games on {(datetime.now() + timedelta(days=1)).strftime('%B %d, %Y')}."
        else:
            sport = "college football"
            target_sport = "americanfootball_ncaaf"
            date_context = "Focus on today's rivalry week games (November 29, 2025)."

        # Grab odds
        odds_data = get_odds(target_sport)
        odds_snippet = str(odds_data[:2]) if odds_data else "No live odds available."

        prompt = (
            f"You are the sharpest sports bettor alive.\n"
            f"{date_context}\n"
            f"Give ONE high-confidence {sport} pick (side/total or a player prop).\n"
            f"Include the exact line and 2-3 sentences of elite reasoning.\n"
            f"Use this odds snippet only for context: {odds_snippet}\n"
            f"Keep it short, confident, and locked to today's slate."
        )

        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.8,
            max_tokens=180,
            messages=[{"role": "user", "content": prompt}]
        )

        return resp.choices[0].message.content.strip()

    except Exception as e:
        # HARD FALLBACK PICK
        nfl_fallback = get_odds("americanfootball_nfl")

        if nfl_fallback:
            game = nfl_fallback[0]
            home = game["home_team"]
            away = game["away_team"]
            try:
                spread_market = next(m for m in game["bookmakers"][0]["markets"] if m["key"] == "spreads")
                spread = next(o for o in spread_market["outcomes"] if o["name"] == home)["point"]

                return (
                    f"{away} +{spread:.1f} @ {home} 🔥\n"
                    f"{away} is 7-3 ATS on the road; {home}'s defense leaking badly — strong upset cover angle."
                )
            except:
                pass

        # Soft fallback
        if nfl_mode:
            return (
                "Packers ML vs Lions 🧀\n"
                "Detroit's run D ranks bottom 5 and Love has surged recently — live dog spot."
            )

        return (
            "Jeremiah Smith OVER 75.5 receiving yards vs Michigan 💀\n"
            "He's gone 90+ in six straight; Michigan DBs are banged up and vulnerable deep."
        )


# -----------------------------------------------------------
# TELEGRAM WEBHOOK
# -----------------------------------------------------------

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()

    if data and data.get("message"):
        chat_id = data["message"]["chat"]["id"]
        text = data["message"].get("text", "").lower()

        if any(w in text for w in ["card", "slate", "games"]):
            reply = build_card()
        elif any(w in text for w in ["pick", "play", "bet"]):
            reply = ai_pick(text)
        else:
            reply = (
                "👊 Bot alive!\n"
                "• 'card' → full slate\n"
                "• 'pick' → one sharp AI play"
            )

        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": reply}
        )

    return jsonify({"ok": True})


@app.route("/")
def home():
    return "Stealie printing tickets 24/7 💀⚡️"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))