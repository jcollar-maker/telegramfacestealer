# main.py — minimal version that works on Render 100% of the time

import os
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN")

@app.route("/")
def home():
    return "Bot is alive 💀⚡️"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    if data and data.get("message"):
        chat_id = data["message"]["chat"]["id"]
        text = data["message"].get("text", "").lower()

        if "pick" in text:
            reply = "Jeremiah Smith OVER 75.5 receiving yards vs Michigan\nHe's hit this in 6 straight games."
        else:
            reply = "Send the word 'pick' for today's lock 👊"

        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                      json={"chat_id": chat_id, "text": reply})

    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))