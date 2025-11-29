import os
from flask import Flask, request
import requests
import openai

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") OPENAI_KEY = os.getenv("OPENAI_KEY") ODDS_API_KEY = os.getenv("ODDS_API_KEY")

TELEGRAM_URL = f"https://nam12.safelinks.protection.outlook.com/?url=https%3A%2F%2Fapi.telegram.org%2Fbot&data=05%7C02%7Cjcollar1%40iuhealth.org%7C9116f2a36d8c48de236a08de2f6b55c3%7Cd9d470633f5e4de9bf99f083657fa0fe%7C0%7C0%7C639000335208603065%7CUnknown%7CTWFpbGZsb3d8eyJFbXB0eU1hcGkiOnRydWUsIlYiOiIwLjAuMDAwMCIsIlAiOiJXaW4zMiIsIkFOIjoiTWFpbCIsIldUIjoyfQ%3D%3D%7C0%7C%7C%7C&sdata=L1o4wPUwyfbwiNmeVIcUbAfs1X8fEoF%2F5ZWtqj9MUdw%3D&reserved=0{TELEGRAM_TOKEN}"

app = Flask(__name__)
openai.api_key = OPENAI_KEY

def get_betting_odds(message_text):
    url = f"https://nam12.safelinks.protection.outlook.com/?url=https%3A%2F%2Fapi.the-odds-api.com%2Fv4%2Fsports%2Famericanfootball_nfl%2Fodds%2F%3FapiKey%3D&data=05%7C02%7Cjcollar1%40iuhealth.org%7C9116f2a36d8c48de236a08de2f6b55c3%7Cd9d470633f5e4de9bf99f083657fa0fe%7C0%7C0%7C639000335208623538%7CUnknown%7CTWFpbGZsb3d8eyJFbXB0eU1hcGkiOnRydWUsIlYiOiIwLjAuMDAwMCIsIlAiOiJXaW4zMiIsIkFOIjoiTWFpbCIsIldUIjoyfQ%3D%3D%7C0%7C%7C%7C&sdata=FGSOowkEORW%2BDiTbvw79EZnQOEgUr9ANo7z8FaTcfZM%3D&reserved=0{ODDS_API_KEY}"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        if data:
            return f"Example odds: {data[0]['home_team']} vs {data[0]['away_team']} - {data[0]['bookmakers'][0]['markets'][0]['outcomes']}"
    return "Sorry, I could not fetch odds."

def generate_ai_response(message_text):
    prompt = f"Analyze NFL odds and comment: {message_text}"
    try:
        completion = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        return f"Error generating AI response: {e}"

def send_telegram_message(chat_id, text):
    url = f"{TELEGRAM_URL}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    requests.post(url, json=payload)

@app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"]) def webhook():
    data = request.get_json()
    if "message" in data:
        chat_id = data["message"]["chat"]["id"]
        message_text = data["message"].get("text", "")
        odds_text = get_betting_odds(message_text)
        ai_text = generate_ai_response(message_text)
        full_response = f"{odds_text}\n\n{ai_text}"
        send_telegram_message(chat_id, full_response)
    return "OK"

@app.route("/")
def index():
    return "Telegram AI Betting Bot is running!"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
