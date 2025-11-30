#!/usr/bin/env python3
"""
MAIN.PY - FULLY MAXED-OUT STEALIE BOT (NFL + CFB + SGP + PROPS + EV + SHARP + CACHING)

Features:
- Multi-sport support (NFL / CFB) with easy router commands
- Odds caching with TTL to reduce API hits
- Sharp scoring (line variance, mean closeness, implied moneyline divergence)
- Props support (tries to pull player props from Odds API, falls back to AI)
- AI-backed pick generator (returns JSON-like pick + confidence)
- EV estimator, Model Grade (A-F)
- Same-Game Parlay (SGP) builder
- Auto-parlay builder + Dabble-friendly slip
- Per-chat unit tracking (persistent JSON file)
- Rate limiting, basic admin endpoints, robust error handling
- Regular question answering via OpenAI
- Uses only Flask + requests + standard library + openai client
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
CACHE_TTL = int(os.environ.get("ODDS_CACHE_TTL_SEC", 55))  # seconds

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
    # miss
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
                prob = 1.0 / avg if avg and avg > 1.01 else 0.5
                ml_divergence += abs(prob - 0.5)
            if team_probs:
                ml_divergence = ml_divergence / len(team_probs)
        score = (1.0 / (1.0 + mean_abs)) * (1.0 + mean_std * 1.2 + ml_divergence * 2.0)
        return float(round(score, 4))
    except Exception:
        logging.exception("compute_sharp_score error")
        return 0.0

# -------------------------
# AI integration (picks & props fallback + Q&A)
# -------------------------
def call_openai_for_pick(sport="NFL", odds_snippet=None):
    if not client:
        return None
    try:
        prompt = (
            f"You are a world-class sharp bettor.\n"
            f"Return a JSON object with keys: pick_text, confidence (0-1), reason, suggested_decimal_odds.\n"
            f"Make one concise pick for {sport} today's slate. Use the odds snippet for context if present.\n"
            f"Odds snippet: {odds_snippet or 'none'}\n"
            f"Keep responses strict JSON only."
        )
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=220
        )
        txt = resp.choices[0].message.content.strip()
        try:
            j = json.loads(txt)
            return j
        except Exception:
            start = txt.find("{")
            end = txt.rfind("}")
            if start != -1 and end != -1:
                try:
                    j = json.loads(txt[start:end+1])
                    return j
                except Exception:
                    pass
            return {"pick_text": txt.split("\n")[0], "confidence": 0.6, "reason": "\n".join(txt.split("\n")[1:])[:240], "suggested_decimal_odds": 1.9}
    except Exception:
        logging.exception("call_openai_for_pick error")
        return None

def call_openai_for_question(question, max_tokens=250):
    if not client:
        return "⚠️ OpenAI key not set. Cannot answer questions."
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"user","content":question}],
            temperature=0.7,
            max_tokens=max_tokens
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        logging.exception("call_openai_for_question error")
        return "⚠️ Error answering question."

def call_openai_for_props(sport="NFL", odds_snippet=None):
    if not client:
        return None
    try:
        prompt = (
            f"You are an elite sports prop matcher. For {sport} generate a JSON array of 5 objects with keys: player, line, suggestion_text, confidence (0-1), suggested_decimal_odds.\n"
            f"Use odds snippet: {odds_snippet or 'none'}\n"
            f"Return strict JSON only."
        )
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=420
        )
        txt = resp.choices[0].message.content.strip()
        try:
            arr = json.loads(txt)
            return arr
        except Exception:
            start = txt.find("[")
            end = txt.rfind("]")
            if start != -1 and end != -1:
                try:
                    arr = json.loads(txt[start:end+1])
                    return arr
                except Exception:
                    pass
            return None
    except Exception:
        logging.exception("call_openai_for_props error")
        return None

# -------------------------
# Units management (per chat)
# -------------------------
def get_units(chat_id):
    data = load_data()
    return float(data.get("users", {}).get(str(chat_id), {}).get("units", 0.0))

def set_units(chat_id, units):
    data = load_data()
    data.setdefault("users", {})
    data["users"].setdefault(str(chat_id), {})
    data["users"][str(chat_id)]["units"] = float(units)
    save_data(data)
    return float(units)

def add_units(chat_id, delta):
    current = get_units(chat_id)
    new = current + float(delta)
    set_units(chat_id, new)
    return new

# -------------------------
# Telegram send wrapper
# -------------------------
def send_telegram(chat_id, text):
    if not TELEGRAM_TOKEN:
        logging.warning("TELEGRAM_TOKEN not set; message not sent.")
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": text})
    except Exception:
        logging.exception("send_telegram failed")

# -------------------------
# Card builders (human-friendly)
# -------------------------
def game_card_text(sport="nfl", limit=DEFAULT_LIMIT):
    sport_key = "americanfootball_nfl" if sport.lower() == "nfl" else "americanfootball_ncaaf"
    header = "🔥 TODAY'S NFL GAME CARD 🔥\n" if sport.lower() == "nfl" else "🔥 TODAY'S COLLEGE FOOTBALL CARD 🔥\n"
    games = get_cached_odds(sport_key, limit=limit)
    if not games:
        return f"⚠️ {sport.upper()} odds unavailable (API/cache)."

    lines = [header]
    for g in games:
        home = g.get("home_team")
        away = g.get("away_team")
        try:
            b0 = g.get("bookmakers", [])[0]
            markets = b0.get("markets", [])
            spread = None
            total = None
            for m in markets:
                if m.get("key") == "spreads":
                    for o in m.get("outcomes", []):
                        if o.get("name") == home:
                            spread = o.get("point")
                if m.get("key") == "totals":
                    total = m.get("outcomes", [])[0].get("point")
            sharp_score = compute_sharp_score(g)
            lines.append(f"🏈 {away} @ {home}\n   {home} {spread if spread is not None else 'N/A'}  |  O/U {total if total is not None else 'N/A'}\n   SharpScore: {sharp_score}")
        except Exception:
            lines.append(f"🏈 {away} @ {home}\n")
    return "\n".join(lines)

def sharp_card_text(sport="nfl", limit=30, top_n=5):
    sport_key = "americanfootball_nfl" if sport.lower() == "nfl" else "americanfootball_ncaaf"
    games = get_cached_odds(sport_key, limit=limit)
    if not games:
        return "⚠️ No odds available for sharp report."
    scored = []
    for g in games:
        sc = compute_sharp_score(g)
        scored.append((sc, g))
    scored = sorted(scored, key=lambda x: x[0], reverse=True)[:top_n]
    out = ["⚡ SHARP EDGE REPORT ⚡"]
    for sc, g in scored:
        out.append(f"{g.get('away_team')} @ {g.get('home_team')} — SharpScore: {sc}")
    return "\n".join(out)

def props_card_text(sport="nfl"):
    sport_key = "americanfootball_nfl" if sport.lower() == "nfl" else "americanfootball_ncaaf"
    games = get_cached_odds(sport_key, limit=8)
    props = []
    if games:
        for g in games:
            for b in g.get("bookmakers", []):
                for m in b.get("markets", []):
                    k = m.get("key") or ""
                    if "player" in k or k == "player_props":
                        for o in m.get("outcomes", []):
                            props.append({
                                "player": o.get("name"),
                                "line": o.get("point"),
                                "book": b.get("title"),
                                "odds": o.get("price", 1.9)
                            })
    if props:
        lines = ["🔥 TOP PLAYER PROPS (from books) 🔥"]
        for p in props[:5]:
            lines.append(f"{p['player']} — {p['line']} @ {p['book']} (odds {p['odds']})")
        return "\n".join(lines)
    odds_snippet = json.dumps(games, default=str)[:1200] if games else None
    ai_props = call_openai_for_props("NFL" if sport.lower() == "nfl" else "CFB", odds_snippet=odds_snippet)
    if ai_props:
        lines = ["🔥 TOP 5 PLAYER PROPS (AI) 🔥"]
        for item in ai_props[:5]:
            lines.append(f"{item.get('player')} — {item.get('line')} — {item.get('suggestion_text')} (conf {item.get('confidence')})")
        return "\n".join(lines)
    return "⚠️ No props available (books or AI)."

# -------------------------
# Additional parlay, SGP, EV, grading functions
# -------------------------
# ... Keep all your original parlay, SGP, build_auto_parlay, format_dabble_slip, estimate_ev, grade_from_confidence logic here exactly
# For brevity, you can merge all remaining helper functions from your existing main.py

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
    data = request.get_json() or {}
    if "message" not in data:
        return jsonify({"ok": True})
    msg = data["message"]
    chat_id = msg["chat"]["id"]
    text = msg.get("text", "").strip()
    
    limited, wait = is_rate_limited(chat_id)
    if limited:
        send_telegram(chat_id, f"⏳ Rate limit: try again in {wait}s.")
        return jsonify({"ok": True})

    t = text.lower()

    # -----------------
    # GREETING / START
    # -----------------
    if t in ("/start","hello","hi","hey"):
        greeting = (
            "👋 Hello! I’m Stealie — your multi-sport betting and sports assistant bot.\n\n"
            "I can help with:\n"
            "• NFL & College Football game cards (odds, spreads, totals)\n"
            "• Sharp edge reports (top games to watch)\n"
            "• Player props (from books or AI suggestions)\n"
            "• Auto parlays & same-game parlays (SGP)\n
            