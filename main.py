# main.py — 100% Render-proof version (works even with bad keys)

import os
import requests
import logging
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# These can be missing — we won’t crash
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_KEY")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

@app.route("/")
def home():
    return "Stealie is fully awake and ready to bet 💀⚡️"

@app.route("/webhook", methods=["POST"])
def webhook():
    logger.info("WEBHOOK HIT — bot is alive!")
    data = request.get_json(force=True) or {}

    if data.get("message"):
        chat_id = data["message"]["chat"]["id"]
        text = data["message"].get("text", "").lower()

        if "card" in text or "slate" in text:
            reply = "🔥 Today's CFB slate loading… (full version in 2 mins)"
        elif "pick" in text:
            reply = "Sharp play: Jeremiah Smith OVER 75.5 yards vs Michigan 💀⚡️\nHe’s hit this in 6 straight."
        else:
            reply = "Yo 👊\n• Send “card” for today’s slate\n• Send “pick” for a lock"

        if TELEGRAM_TOKEN:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": reply}
            )

    return jsonify({"status": "ok"})

# ←←← THIS PORT LINE IS THE MOST IMPORTANT PART FOR RENDER
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)