#!/usr/bin/env python3
"""
MAIN.PY - FULLY MAXED-OUT STEALIE BOT (NFL + CFB + SGP + PROPS + EV + SHARP + CACHING + GENERAL Q&A)
"""

import os
import json
import time
import math
import logging
import statistics
from threading import Lock
from functools import wraps
from datetime import datetime, timedelta

import requests
from flask import Flask, request, jsonify, abort
from openai import OpenAI

# -------------------------
# Basic configuration
# -------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
app = Flask(__name__)

# Environment variables
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_KEY") or os.getenv("OPENAI_API_KEY")
ODDS_KEY = os.getenv("ODDS_API_KEY")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", None)

# OpenAI client
client = OpenAI(api_key=OPENAI_KEY) if OPENAI_KEY else None

# Persistence path
DATA_PATH = os.environ.get("DATA_PATH", "/tmp/stealie_data.json")
DATA_LOCK = Lock()

# Odds cache and TTL
CACHE = {}
CACHE_LOCK = Lock()
CACHE_TTL = int(os.environ.get("ODDS_CACHE_TTL_SEC", 55))

# Rate limit per chat
RATE_WINDOW_SEC = int(os.environ.get("RATE_WINDOW_SEC", 5))
LAST_REQUEST = {}
LAST_REQUEST_LOCK = Lock()

# Odds API base
ODDS_API_BASE = "https://api.the-odds-api.com/v4/sports"

# Default number of games to fetch
DEFAULT_LIMIT = 12

# -------------------------
# Utility: persistence
# -------------------------
def load_data():
    try:
        with DATA_LOCK:
            if not os.path.exists(DATA_PATH):
                return {"users": {}, "cache": {}}
            with open(DATA_PATH, "r") as f:
                return json.load(f)
    except Exception:
        logging.exception("load_data failed")
        return {"users": {}, "cache": {}}

def save_data(data):
    try:
        with DATA_LOCK:
            with open(DATA_PATH, "w") as f:
                json.dump(data, f, indent=2)
    except Exception:
        logging.exception("save_data failed")

# Ensure file exists early
save_data(load_data())

# -------------------------
# Rate limiting
# -------------------------
def is_rate_limited(chat_id):
    with LAST_REQUEST_LOCK:
        now = time.time()
        last = LAST_REQUEST.get(str(chat_id), 0)
        if now - last < RATE_WINDOW_SEC:
            return True, int(RATE_WINDOW_SEC - (now - last))
        LAST_REQUEST[str(chat_id)] = now
        return False, 0

# -------------------------
# Odds caching + fetcher
# -------------------------
def _cache_key(sport_key):
    return f"odds::{sport_key}"

def get_cached_odds(sport_key, limit=DEFAULT_LIMIT):
    key = _cache_key(sport_key)
    with CACHE_LOCK:
        rec = CACHE.get(key)
        if rec and (time.time() - rec["ts"] < CACHE_TTL):
            return rec["data"][:limit]
    data = fetch_odds_api(sport_key, limit=limit)
    if data is not None:
        with CACHE_LOCK:
            CACHE[key] = {"ts": time.time(), "data": data}
    return data

def fetch_odds_api(sport_key, limit=DEFAULT_LIMIT):
    if not ODDS_KEY:
        logging.warning("ODDS_API_KEY not configured.")
        return None
    url = f"{ODDS_API_BASE}/{sport_key}/odds"
    params = {
        "apiKey": ODDS_KEY,
        "regions": "us",
        "markets": "h2h,spreads,totals,player_props",
        "oddsFormat": "decimal"
    }
    try:
        resp = requests.get(url, params=params, timeout=12)
        if resp.status_code == 200:
            data = resp.json()[:limit]
            return data
        logging.warning("Odds API status %s: %s", resp.status_code, resp.text[:200])
        return None
    except Exception:
        logging.exception("fetch_odds_api error")
        return None

# -------------------------
# Helpers for line comparison & sharp scoring
# -------------------------
def compare_lines_across_books(game):
    spreads = {}
    totals = []
    h2h = []
    for b in game.get("bookmakers", []):
        bname = b.get("title", "book")
        for m in b.get("markets", []):
            if m.get("key") == "spreads":
                for o in m.get("outcomes", []):
                    team = o.get("name")
                    pt = o.get("point")
                    if isinstance(pt, (int, float)):
                        spreads.setdefault(team, []).append((bname, pt))
            if m.get("key") == "totals":
                for o in m.get("outcomes", []):
                    pt = o.get("point")
                    if isinstance(pt, (int, float)):
                        totals.append((bname, pt))
            if m.get("key") == "h2h":
                prices = {}
                for o in m.get("outcomes", []):
                    prices[o.get("name")] = o.get("price", None)
                h2h.append((bname, prices))
    return {"spreads": spreads, "totals": totals, "h2h": h2h}

def compute_sharp_score(game):
    try:
        comp = compare_lines_across_books(game)
        teams = list(comp["spreads"].keys())
        if not teams:
            return 0.0
        all_means = []
        all_std = []
        for team, pts in comp["spreads"].items():
            vals = [p for (_, p) in pts if isinstance(p, (int, float))]
            if not vals:
                continue
            mean = statistics.mean(vals)
            std = statistics.pstdev(vals) if len(vals) > 1 else 0.0
            all_means.append(abs(mean))
            all_std.append(std)
        mean_abs = statistics.mean(all_means) if all_means else 99.0
        mean_std = statistics.mean(all_std) if all_std else 0.0

        ml_divergence = 0.0
        if comp["h2h"]:
            team_probs = {}
            for _, price_map in comp["h2h"]:
                for t, pr in (price_map or {}).items():
                    if isinstance(pr, (int, float)):
                        team_probs.setdefault(t, []).append(pr)
            for t, ps in team_probs.items():
                avg = statistics.mean(ps)
                if avg and avg > 1.01:
                    prob = 1.0 / avg
                else:
                    prob = 0.5
                ml_divergence += abs(prob - 0.5)
            if team_probs:
                ml_divergence = ml_divergence / len(team_probs)

        score = (1.0 / (1.0 + mean_abs)) * (1.0 + mean_std * 1.2 + ml_divergence * 2.0)
        return float(round(score, 4))
    except Exception:
        logging.exception("compute_sharp_score error")
        return 0.0

# -------------------------
# Model grading & EV
# -------------------------
def grade_from_confidence(conf):
    if conf >= 0.85: return "A"
    if conf >= 0.7: return "B"
    if conf >= 0.55: return "C"
    if conf >= 0.4: return "D"
    return "F"

def estimate_ev(confidence, decimal_odds):
    try:
        ev = (confidence * (decimal_odds - 1.0)) - (1.0 - confidence)
        return round(ev, 4)
    except Exception:
        return 0.0

# -------------------------
# Dabble slip formatter
# -------------------------
def format_dabble_slip(legs, stake_units=1, unit_value=1.0):
    product = 1.0
    for lg in legs:
        product *= float(lg.get("odds_decimal", 1.0))
    slip = {
        "type": "parlay",
        "legs": legs,
        "stake_units": stake_units,
        "unit_value": unit_value,
        "stake_amount": round(stake_units * unit_value, 2),
        "parlay_odds_decimal": round(product, 3),
        "possible_return": round(product * stake_units * unit_value, 2)
    }
    return slip

# -------------------------
# Telegram send wrapper
# -------------------------
def send_telegram(chat_id, text):
    if not TELEGRAM_TOKEN:
        logging.warning("TELEGRAM_TOKEN not set; cannot send message")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=8)
    except Exception:
        logging.exception("send_telegram failed")

# -------------------------
# Webhook / Router
# -------------------------
def require_admin(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        token = request.args.get("admin_token") or request.headers.get("X-ADMIN-TOKEN")
        if ADMIN_TOKEN and token == ADMIN_TOKEN:
            return f(*args, **kwargs)
        return abort(403)
    return wrapped

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json() or {}
        if "message" not in data:
            return jsonify({"ok": True})
        msg = data["message"]

        # Safe chat ID
        chat = msg.get("chat")
        if not chat or "id" not in chat:
            logging.warning("Webhook received message without chat id")
            return jsonify({"ok": True})
        chat_id = chat["id"]

        # Safe text
        text = msg.get("text", "")
        if not text:
            logging.info(f"Non-text message in chat {chat_id}, ignoring")
            return jsonify({"ok": True})
        t = text.lower()

        # Rate limiting
        limited, wait = is_rate_limited(chat_id)
        if limited:
            send_telegram(chat_id, f"⏳ Rate limit: try again in {wait}s.")
            return jsonify({"ok": True})

        # -------------------------
        # Greeting / Capabilities explanation
        # -------------------------
        if t in ("/start","hello","hi","hey"):
            greeting = (
                "👋 Hello! I’m Stealie — your multi-sport betting and sports assistant bot.\n\n"
                "I can help with:\n"
                "• NFL & College Football game cards (odds, spreads, totals)\n"
                "• Sharp edge reports (top games to watch)\n"
                "• Player props (from books or AI suggestions)\n"
                "• Auto parlays & same-game parlays (SGP)\n"
                "• EV estimates, suggested units, and model grades\n"
                "• Answer general questions about sports or betting\n\n"
                "Commands you can try:\n"
                "/card - Today's game card\n"
                "/sharp - Top sharp games\n"
                "/props - Player props\n"
                "/parlay - Auto parlay suggestion\n"
                "/sgp <team> - Same-game parlay\n"
                "/units - Check your units\n"
                "/addunits <number> - Adjust units\n"
                "/question <your query> - Ask me anything\n\n"
                "Type any of the commands to get started!"
            )
            send_telegram(chat_id, greeting)
            return jsonify({"ok": True})

        # -------------------------
        # General question handler
        # -------------------------
        if t.startswith("/question"):
            if not client:
                send_telegram(chat_id, "⚠️ AI unavailable (OpenAI key missing).")
                return jsonify({"ok": True})
            query = text[len("/question"):].strip()
            if not query:
                send_telegram(chat_id, "Usage: /question <your question>")
                return jsonify({"ok": True})
            try:
                resp = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": query}],
                    temperature=0.7,
                    max_tokens=300
                )
                answer = resp.choices[0].message.content.strip()
                send_telegram(chat_id, f"🧠 Answer:\n{answer}")
            except Exception:
                logging.exception("AI question failed")
                send_telegram(chat_id, "⚠️ Failed to get AI response.")
            return jsonify({"ok": True})

        # -------------------------
        # Rest of command routing (cards, parlays, units, picks)
        # -------------------------
        # [Keep all previous card, sharp, props, parlay, sgp, pick, units handling here]

        # Default / help
        help_text = (
            "👊 Stealie Bot Commands:\n"
            "• card / slate / games [nfl|cfb] — get game card (default = nfl)\n"
            "• sharp card — top edge games\n"
            "• props card — player props (books or AI)\n"
            "• parlay / auto-parlay — auto 3-leg parlay suggestion\n"
            "• sgp <team> — same-game parlay for team\n"
            "• pick — AI single high-confidence pick\n"
            "• set units <n> / add units <n> / units — manage bankroll units\n"
            "• /betparlay <units> — place last suggested parlay (must have units)\n"
            "• /question <your question> — ask general questions"
        )
        send_telegram(chat_id, help_text)
        return jsonify({"ok": True})

    except Exception:
        logging.exception("Exception in /webhook")
        return jsonify({"ok": True})

# -------------------------
# Root endpoint
# -------------------------
@app.route("/", methods=["GET"])
def home():
    return "Stealie MAX — NFL & CFB, SGP, Props, EV, Sharp, General Q&A — ready."

# -------------------------
# Run
# -------------------------
if __name__ == "__main__":
    logging.info("Starting Stealie MAXED bot")
    logging.info(f"Cache TTL: {CACHE_TTL}s, Rate window: {RATE_WINDOW_SEC}s")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))