import os
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)
TOKEN = os.getenv("TELEGRAM_TOKEN")

@app.route("/")
def home():
    return "WORKING – Stealie is alive 💀⚡️"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    if data and data.get("message"):
        chat_id = data["message"]["chat"]["id"]
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": "Bot is now 100% alive and ready to print tickets 💀⚡️"}
        )
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))