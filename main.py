# main.py - clean version

import os
import requests
from flask import Flask, request
from openai import OpenAI

# Load secrets from environment variables
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_KEY")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

if not TELEGRAM_TOKEN:
    raise ValueError("Missing TELEGRAM_TOKEN environment variable")
if not OPENAI_KEY:
    raise ValueError("Missing OPENAI_KEY environment variable")
if not ODDS_API_KEY:
    raise ValueError("Missing ODDS_API_KEY environment variable")

client = OpenAI(api_key=OPENAI_KEY)
app = Flask(__name__)

TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

# Get NFL Odds
def get_betting_odds(message_text):
    url = f"https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds/?apiKey={ODDS_API_KEY}&regions=us&markets=h2h"
    response = requests.get(url)

    if response.status_code == 200:
        try:
            data = response.json()
            if data and isinstance(data, list):
                game = data[0]
                home = game['home_team']
                away = game['away_team']
                bookmaker = game['bookmakers'][0]
                market = bookmaker['markets'][0]
                outcomes = market['outcomes']

                return f"Example Odds:\n{home} vs {away}\n{outcomes}"
        except:
            pass

    return "Sorry, I couldn't fetch odds."


# Generate AI response
def generate_ai_response(message_text):
    prompt = f"Analyze NFL odds and comment: {message_text}"

    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150
        )
        return completion.choices[0].message.content.strip()

    except Exception as e:
        return f"AI Error: {str(e)}"


@app.route(f"/webhook", methods=["POST"])
def webhook():
    data = request.json

    if "message" in data:
        chat_id = data["message"]["chat"]["id"]
        text = data["message"].get("text", "")

        if "odds" in text.lower():
            reply = get_betting_odds(text)
        else:
            reply = generate_ai_response(text)

        requests.post(
            TELEGRAM_URL,
            json={"chat_id": chat_id, "text": reply}
        )

    return {"ok": True}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
