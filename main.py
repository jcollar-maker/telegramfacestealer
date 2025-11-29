import os
import requests
from flask import Flask, request, jsonify from openai import OpenAI

# Load secrets from environment variables TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") OPENAI_KEY = os.getenv("OPENAI_KEY") ODDS_API_KEY = os.getenv("ODDS_API_KEY")

# Validate that secrets exist
if not TELEGRAM_TOKEN:
    raise ValueError("Missing TELEGRAM_TOKEN environment variable")

if not OPENAI_KEY:
    raise ValueError("Missing OPENAI_KEY environment variable")

if not ODDS_API_KEY:
    raise ValueError("Missing ODDS_API_KEY environment variable")

# Set up API clients
client = OpenAI(api_key=OPENAI_KEY)
app = Flask(__name__)

# Telegram base URL
TELEGRAM_URL = f"https://nam12.safelinks.protection.outlook.com/?url=https%3A%2F%2Fapi.telegram.org%2Fbot&data=05%7C02%7Cjcollar1%40iuhealth.org%7C1b4247443ade4595041108de2f79650b%7Cd9d470633f5e4de9bf99f083657fa0fe%7C0%7C0%7C639000395612684108%7CUnknown%7CTWFpbGZsb3d8eyJFbXB0eU1hcGkiOnRydWUsIlYiOiIwLjAuMDAwMCIsIlAiOiJXaW4zMiIsIkFOIjoiTWFpbCIsIldUIjoyfQ%3D%3D%7C0%7C%7C%7C&sdata=tga0%2BF3GRcKatzOTvGuyLEAlIrWYn3kNZZk5MVV9uL0%3D&reserved=0{TELEGRAM_TOKEN}"


# ---------------------------------------------------------
#  GET NFL ODDS
# ---------------------------------------------------------
def get_betting_odds():
    url = f"https://nam12.safelinks.protection.outlook.com/?url=https%3A%2F%2Fapi.the-odds-api.com%2Fv4%2Fsports%2Famericanfootball_nfl%2Fodds%2F%3FapiKey%3D&data=05%7C02%7Cjcollar1%40iuhealth.org%7C1b4247443ade4595041108de2f79650b%7Cd9d470633f5e4de9bf99f083657fa0fe%7C0%7C0%7C639000395612710297%7CUnknown%7CTWFpbGZsb3d8eyJFbXB0eU1hcGkiOnRydWUsIlYiOiIwLjAuMDAwMCIsIlAiOiJXaW4zMiIsIkFOIjoiTWFpbCIsIldUIjoyfQ%3D%3D%7C0%7C%7C%7C&sdata=OD%2Fw206WxrBEJfCNM2Ll01Wpzaq2TaGSJhi9t0omdoI%3D&reserved=0{ODDS_API_KEY}&regions=us&markets=h2h"

    response = requests.get(url)
    if response.status_code != 200:
        return "Could not fetch odds."

    data = response.json()
    if not data:
        return "No NFL odds found."

    game = data[0]  # First game in list
    home = game["home_team"]
    away = game["away_team"]

    home_price = game["bookmakers"][0]["markets"][0]["outcomes"][0]["price"]
    away_price = game["bookmakers"][0]["markets"][0]["outcomes"][1]["price"]

    return f"{home} ({home_price}) vs {away} ({away_price})"


# ---------------------------------------------------------
#  GENERATE AI RESPONSE
# ---------------------------------------------------------
def generate_ai_response(message_text):
    try:
        prompt = f"Analyze NFL odds and answer user question: {message_text}"

        completion = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "user", "content": prompt}
            ],
            max_tokens=200
        )

        return completion.choices[0].message.content.strip()

    except Exception as e:
        return f"AI error: {e}"


# ---------------------------------------------------------
#  SEND MESSAGE BACK TO TELEGRAM
# ---------------------------------------------------------
def send_message(chat_id, text):
    url = f"{TELEGRAM_URL}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    requests.post(url, json=payload)


# ---------------------------------------------------------
#  WEBHOOK ROUTE
# ---------------------------------------------------------
@app.route("/", methods=["POST"])
def webhook():
    update = request.json

    if "message" in update:
        chat_id = update["message"]["chat"]["id"]
        user_text = update["message"].get("text", "")

        if "odds" in user_text.lower():
            reply = get_betting_odds()
        else:
            reply = generate_ai_response(user_text)

        send_message(chat_id, reply)

    return jsonify({"status": "ok"})


# ---------------------------------------------------------
#  HEALTH CHECK
# ---------------------------------------------------------
@app.route("/", methods=["GET"])
def home():
    return "Telegram betting bot is running!"


# Start server (Render uses $PORT)
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
