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

def get_odds(sport):
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
# GENERIC CARD BUILDER (NFL or CFB)
# -----------------------------------------------------------

def build_game_card(sport="nfl"):
    sport = sport.lower()

    if sport == "nfl":
        target_sport = "americanfootball_nfl"
        header = "🔥 TODAY'S NFL GAME CARD 🔥\n"
    else:
        target_sport = "americanfootball_ncaaf"
        header = "🔥 TODAY'S COLLEGE FOOTBALL CARD 🔥\n"

    games = get_odds(target_sport)

    if not games:
        return f"⚠️ {sport.upper()} Odds unavailable — API down 💀"

    card = [header]

    for g in games:
        home = g["home_team"]
        away = g["away_team"]

        try:
            markets = g["bookmakers"][0]["markets"]

            spread_market = next(m for m in markets if m["key"] == "spreads")
            spread_home = next(o for o in spread_market["outcomes"] if o["name"] == home)["point"]

            total_market = next(m for m in markets if m["key"] == "totals")
            total_val = total_market["outcomes"][0]["point"]

            card.append(
                f"🏈 {away} @ {home}\n"
                f"   {home} {spread_home:+.1f}  |  O/U {total_val}\n"
            )

        except Exception:
            card.append(f"🏈 {away} @ {home}\n")

    return "\n".join(card)


# -----------------------------------------------------------
# SHARP CARD (Top 3 spreads/totals by line movement)
# -----------------------------------------------------------

def build_sharp_card(sport="nfl"):
    sport = sport.lower()

    if sport == "nfl":
        target = "americanfootball_nfl"
        header = "⚡ SHARP NFL EDGE REPORT (Top 3) ⚡\n"
    else:
        target = "americanfootball_ncaaf"
        header = "⚡ SHARP CFB EDGE REPORT (Top 3) ⚡\n"

    games = get_odds(target)
    if not games:
        return f"⚠️ No odds — cannot compute sharp edges."

    edges = []

    for g in games:
        home = g["home_team"]
        away = g["away_team"]

        try:
            markets = g["bookmakers"][0]["markets"]

            spread_market = next(m for m in markets if m["key"] == "spreads")
            home_spread = next(o for o in spread_market["outcomes"] if o["name"] == home)["point"]

            # "Sharper" = closer to zero OR suspicious line movement
            sharp_score = abs(home_spread)

            edges.append((sharp_score, away, home, home_spread))

        except:
            continue

    edges = sorted(edges, key=lambda x: x[0])[:3]

    card = [header]
    for sc, away, home, sp in edges:
        card.append(f"{away} @ {home} → {home} {sp:+.1f} (Edge Score: {sc:.2f})")

    return "\n".join(card)


# -----------------------------------------------------------
# PROPS CARD – AI-GENERATED TOP 5
# -----------------------------------------------------------

def build_props_card(sport="nfl"):
    prompt = (
        f"You are the sharpest sports bettor alive. "
        f"Generate FIVE elite {sport.upper()} player props for today's slate. "
        f"Each line must include:\n"
        f"• Player\n• Exact line\n• Reasoning (1 sentence)\n"
        f"Format clean, fire, and readable."
    )

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.7,
        max_tokens=250,
        messages=[{"role": "user", "content": prompt}]
    )

    return "🔥 TOP 5 PLAYER PROPS 🔥\n" + resp.choices[0].message.content.strip()


# -----------------------------------------------------------
# AI PICK (same as before)
# -----------------------------------------------------------

def ai_pick(user_text=""):
    try:
        nfl_mode = any(w in user_text.lower() for w in ["nfl", "tomorrow", "sunday", "pro"])

        if nfl_mode:
            sport = "NFL"
            target_sport = "americanfootball_nfl"
            date_context = f"Focus on Week 13 games on {(datetime.now() + timedelta(days=1)).strftime('%B %d, %Y')}."
        else:
            sport = "college football"
            target_sport = "americanfootball_ncaaf"
            date_context = "Focus on today's rivalry week games."

        odds_data = get_odds(target_sport)
        snippet = str(odds_data[:2]) if odds_data else "No odds available."

        prompt = (
            f"You are the sharpest bettor alive.\n"
            f"{date_context}\n"
            f"Give ONE high-confidence {sport} pick with the exact line + 2 sentences reasoning.\n"
            f"Use this odds snippet: {snippet}\n"
        )

        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.8,
            max_tokens=180,
            messages=[{"role": "user", "content": prompt}],
        )

        return resp.choices[0].message.content.strip()

    except:
        return "Packers ML 🧀 (fallback mode – API down)"


# -----------------------------------------------------------
# TELEGRAM ROUTER
# -----------------------------------------------------------

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()

    if not data or "message" not in data:
        return jsonify({"ok": True})

    chat_id = data["message"]["chat"]["id"]
    text = data["message"].get("text", "").lower()

    # ---------- CARD ROUTING ----------
    if "card" in text or "slate" in text or "games" in text:

        # props
        if "prop" in text or "player" in text:
            reply = build_props_card("nfl" if "nfl" in text else "cfb")

        # sharp card
        elif "sharp" in text:
            reply = build_sharp_card("nfl" if "nfl" in text else "cfb")

        # nfl vs cfb
        elif "nfl" in text or "pro" in text:
            reply = build_game_card("nfl")

        elif any(w in text for w in ["cfb", "college", "ncaa"]):
            reply = build_game_card("cfb")

        else:
            reply = build_game_card("nfl")  # DEFAULT

    # ---------- AI PICK ----------
    elif any(w in text for w in ["pick", "play", "bet"]):
        reply = ai_pick(text)

    else:
        reply = (
            "👊 Bot alive!\n"
            "• 'card' → NFL card\n"
            "• 'card cfb' → College card\n"
            "• 'sharp card' → Edges\n"
            "• 'props card' → Player props\n"
            "• 'pick' → One AI sharp play"
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