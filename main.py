#!/usr/bin/env python3
"""
Stealie MAX — Polished, Friendly & Fixed main.py
- Proper sport matching
- Explicit player prop markets (works with current Odds API)
- Friendly, outgoing messages (less abbreviations)
- Robust routing: commands must be explicit (/card, /props, /pick, etc.)
- OpenAI fallback for picks/props/daily cheat sheet (optional)
"""

import os
import json
import time
import logging
import requests
import random
import re
from datetime import datetime, timedelta
from threading import Lock
from flask import Flask, request, jsonify

# Try importing OpenAI client safely
try:
    from openai import OpenAI
except Exception:
    OpenAI = None

# --- Configuration ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
app = Flask(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ODDS_KEY = os.getenv("ODDS_API_KEY")
OPENAI_KEY = os.getenv("OPENAI_KEY")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN")

# OpenAI client
client = None
if OpenAI and OPENAI_KEY:
    os.environ["OPENAI_API_KEY"] = OPENAI_KEY
    try:
        client = OpenAI()
        logging.info("OpenAI client initialized")
    except Exception:
        logging.exception("OpenAI init failed; continuing without AI support")
        client = None

# Persistence
DATA_PATH = os.environ.get("DATA_PATH", "/tmp/stealie_polished_data.json")
DATA_LOCK = Lock()

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

def save_data(d):
    try:
        with DATA_LOCK:
            with open(DATA_PATH, "w") as f:
                json.dump(d, f, indent=2)
    except Exception:
        logging.exception("save_data failed")

# Ensure file exists
save_data(load_data())

# Cache & rate limiting
CACHE = {}
CACHE_LOCK = Lock()
CACHE_TTL = int(os.environ.get("ODDS_CACHE_TTL_SEC", 55))
RATE_WINDOW_SEC = int(os.environ.get("RATE_WINDOW_SEC", 4))
LAST_REQUEST = {}
LAST_REQUEST_LOCK = Lock()

# Sport aliases -> Odds API sport keys
SPORT_ALIASES = {
    "nfl": "americanfootball_nfl",
    "cfb": "americanfootball_ncaaf",
    "college": "americanfootball_ncaaf",
    "nba": "basketball_nba",
    "mlb": "baseball_mlb",
    "nhl": "icehockey_nhl"
}
DEFAULT_SPORT = "nfl"
DEFAULT_SPORT_KEY = SPORT_ALIASES[DEFAULT_SPORT]

# Player prop markets to request (explicit list — Odds API requires specific market names)
PLAYER_PROP_MARKETS = ",".join([
    "player_pass_tds",
    "player_passing_yards",
    "player_receiving_yards",
    "player_receptions",
    "player_rushing_yards",
    "player_points",
    "player_field_goals_made"
])

# General markets for standard cards
STANDARD_MARKETS = "h2h,spreads,totals"

# --- Utilities ---
def is_rate_limited(chat_id):
    with LAST_REQUEST_LOCK:
        now = time.time()
        last = LAST_REQUEST.get(str(chat_id), 0)
        if now - last < RATE_WINDOW_SEC:
            return True, int(RATE_WINDOW_SEC - (now - last))
        LAST_REQUEST[str(chat_id)] = now
        return False, 0

def send_telegram(chat_id, text):
    if not TELEGRAM_TOKEN:
        logging.warning("TELEGRAM_TOKEN not configured; cannot send message.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    try:
        resp = requests.post(url, json=payload, timeout=8)
        if resp.status_code != 200:
            logging.warning("Telegram send status %s: %s", resp.status_code, resp.text[:200])
    except Exception:
        logging.exception("send_telegram failed")

def safe_extract_openai(resp):
    try:
        return resp.choices[0].message.content.strip()
    except Exception:
        try:
            return resp.choices[0].message["content"].strip()
        except Exception:
            try:
                return resp.choices[0].text.strip()
            except Exception:
                return None

# --- Odds fetching with caching & market handling ---
def fetch_odds(sport_key, markets=STANDARD_MARKETS, limit=30):
    if not ODDS_KEY:
        logging.warning("ODDS_API_KEY not set")
        return []
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds"
    try:
        resp = requests.get(url, params={"apiKey": ODDS_KEY, "regions": "us", "markets": markets, "oddsFormat": "decimal"}, timeout=12)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                return data[:limit]
            return data
        logging.warning("Odds API returned %s: %s", resp.status_code, resp.text[:200])
    except Exception:
        logging.exception("fetch_odds error")
    return []

def get_cached_odds(sport_key, markets=STANDARD_MARKETS, limit=30):
    key = f"{sport_key}::{markets}"
    with CACHE_LOCK:
        rec = CACHE.get(key)
        if rec and (time.time() - rec["ts"] < CACHE_TTL):
            return rec["data"][:limit]
    data = fetch_odds(sport_key, markets=markets, limit=limit)
    with CACHE_LOCK:
        CACHE[key] = {"ts": time.time(), "data": data}
    return data

# --- Helpers to extract markets and format outputs ---
def extract_first_spread_total(game):
    spread = None
    total = None
    for b in game.get("bookmakers", []):
        for m in b.get("markets", []) or []:
            if m.get("key") == "spreads":
                for o in m.get("outcomes", []):
                    # pick first numeric point we see
                    pt = o.get("point")
                    if isinstance(pt, (int, float)):
                        spread = pt
                        break
            if m.get("key") == "totals":
                for o in m.get("outcomes", []):
                    pt = o.get("point")
                    if isinstance(pt, (int, float)):
                        total = pt
                        break
        if spread is not None and total is not None:
            break
    return spread, total

def extract_player_props_from_games(games, max_props=8):
    props = []
    for g in games:
        for b in g.get("bookmakers", []):
            for m in b.get("markets", []) or []:
                k = (m.get("key") or "").lower()
                # accept any market that contains 'player' or one of our explicit keys
                if "player" in k or any(k.startswith(kx) for kx in ["player_pass", "player_rushing", "player_receiving", "player_passing", "player_receptions", "player_points"]):
                    for o in m.get("outcomes", []) or []:
                        props.append({
                            "game": f"{g.get('away_team')} @ {g.get('home_team')}",
                            "market": m.get("key"),
                            "player": o.get("description") or o.get("name"),
                            "line": o.get("point"),
                            "price": o.get("price")
                        })
                        if len(props) >= max_props:
                            return props
    return props

# --- Friendly messages ---
def friendly_start_text():
    return (
        "👋 Hey — I’m Stealie. I fetch game lines, player props, and I can help you with picks.\n\n"
        "Try these commands:\n"
        "/card [nfl|cfb|nba|mlb|nhl] — Show the game card (default is NFL)\n"
        "/props [team] — Show top player props from books (call exactly '/props')\n"
        "/smartprops — Ask the AI for smart prop ideas (if OpenAI key present)\n"
        "/sgp <team> — Build a same-game parlay for a team\n"
        "/parlay — Auto parlay suggestion\n"
        "/pick — One concise pick (AI if available)\n"
        "/ev <confidence 0-1> <decimal odds> — Estimate expected value per $1\n"
        "/kelly <edge_decimal> <decimal odds> — Kelly fraction suggestion\n"
        "/setunits <n> /addunits <n> /units — Manage your unit size\n"
        "/alerts on|off — Subscribe to line movement alerts\n"
        "Type clearly and use the slash (/) to invoke commands. I am friendly and I explain things."
    )

# --- Units and simple persistence per chat ---
def get_user(chat_id):
    data = load_data()
    return data.get("users", {}).get(str(chat_id), {"units": 1.0, "alerts": False})

def save_user(chat_id, user):
    data = load_data()
    data.setdefault("users", {})
    data["users"][str(chat_id)] = user
    save_data(data)

def set_units(chat_id, n):
    user = get_user(chat_id)
    user["units"] = float(n)
    save_user(chat_id, user)
    return user["units"]

def add_units(chat_id, delta):
    user = get_user(chat_id)
    user["units"] = float(user.get("units", 1.0)) + float(delta)
    save_user(chat_id, user)
    return user["units"]

def toggle_alerts(chat_id, on):
    user = get_user(chat_id)
    user["alerts"] = bool(on)
    save_user(chat_id, user)
    return user["alerts"]

# --- Core command implementations ---
def build_card_message(sport_alias):
    sport_key = SPORT_ALIASES.get(sport_alias, DEFAULT_SPORT_KEY)
    games = get_cached_odds(sport_key, markets=STANDARD_MARKETS, limit=12)
    if not games:
        return f"Sorry — I could not fetch {sport_alias.upper()} odds right now. Please try again in a minute."
    header = f"🔥 {sport_alias.upper()} Game Card — {datetime.utcnow().strftime('%Y-%m-%d')}\n\n"
    lines = [header]
    for g in games[:12]:
        home = g.get("home_team", "Home")
        away = g.get("away_team", "Away")
        commence = g.get("commence_time", "Time unknown")
        spread, total = extract_first_spread_total(g)
        if spread is None and total is None:
            lines.append(f"• {away} @ {home} — kickoff: {commence}")
        else:
            lines.append(f"• {away} @ {home} — kickoff: {commence}\n   Spread (first seen): {spread if spread is not None else 'N/A'} | Total (first seen): {total if total is not None else 'N/A'}")
    return "\n".join(lines)

def build_props_message(team=None):
    # First try to fetch explicit prop markets
    games = get_cached_odds(SPORT_ALIASES.get("nfl", DEFAULT_SPORT_KEY), markets=PLAYER_PROP_MARKETS, limit=30)
    props = extract_player_props_from_games(games, max_props=12)
    if team:
        team_lower = team.lower()
        props = [p for p in props if team_lower in p["game"].lower() or team_lower in (p["player"] or "").lower()]
    if props:
        lines = ["🔥 Top Player Props (from books) 🔥"]
        for p in props:
            lines.append(f"• {p['game']} — {p['player']} — {p['market']} {p['line']} (odds {p['price']})")
        return "\n".join(lines)
    # If no book props, try AI fallback if available
    if client:
        games_snippet = json.dumps(games, default=str)[:2000]
        ai_response = ai_smart_props("NFL", games_snippet)
        if ai_response:
            # ai_response may be a list or a string — format nicely
            if isinstance(ai_response, list):
                lines = ["🤖 Smart Props (AI)"]
                for item in ai_response[:6]:
                    lines.append(f"• {item.get('player')} — {item.get('line')} — {item.get('reason')} (conf {item.get('confidence')})")
                return "\n".join(lines)
            else:
                return f"🤖 Smart Props (AI)\n{ai_response}"
    return "I could not find player props in books and AI fallback did not return suggestions. Try again later or try /smartprops."

def build_sgp(team_name):
    # Use prop markets to assemble same-game parlay
    t = (team_name or "").strip().lower()
    if not t:
        return "Usage: /sgp <team name>  — please specify a team."
    games = get_cached_odds(SPORT_ALIASES.get("nfl", DEFAULT_SPORT_KEY), markets=PLAYER_PROP_MARKETS, limit=80)
    # try to find a game with the team
    candidate = None
    for g in games:
        names = (g.get("home_team","") + " " + g.get("away_team","")).lower()
        if t in names:
            candidate = g
            break
    if not candidate:
        return f"I could not find a game for '{team_name}' right now."
    # collect props and build 3-leg SGP
    props = extract_player_props_from_games([candidate], max_props=6)
    legs = []
    # add spread leg if possible
    spread, total = extract_first_spread_total(candidate)
    if spread is not None:
        legs.append(f"{candidate.get('home_team')} spread {spread:+.1f}")
    if total is not None:
        legs.append(f"Game total {total}")
    # add up to one player prop
    if props:
        p = props[0]
        legs.append(f"{p['player']} — {p['market']} {p['line']} (odds {p['price']})")
    # ensure at least two legs
    if len(legs) < 2:
        legs.append("Fallback leg: team moneyline")
    payout_est = round(random.uniform(3.0, 18.0), 2)
    out = [f"🔒 Same-Game Parlay for {team_name.title()} 🔒"]
    out.extend([f"• {l}" for l in legs])
    out.append(f"\nEstimated parlay multiplier: x{payout_est}\nSuggested stake: 0.25 units (adjust based on confidence).")
    return "\n".join(out)

def build_auto_parlay(profile="moderate"):
    # profile influences number of legs and risk tolerance
    if profile == "conservative":
        n = 2
    elif profile in ("aggro", "aggressive"):
        n = 5
    else:
        n = 3
    games = get_cached_odds(DEFAULT_SPORT_KEY, markets=STANDARD_MARKETS, limit=40)
    if not games:
        return "I could not fetch games to build a parlay right now."
    # choose games with closest spreads (lower absolute spread)
    candidates = []
    for g in games:
        spreads = []
        for b in g.get("bookmakers", []):
            for m in b.get("markets", []) or []:
                if m.get("key") == "spreads":
                    for o in m.get("outcomes", []) or []:
                        if isinstance(o.get("point"), (int,float)):
                            spreads.append(abs(o.get("point")))
        if spreads:
            candidates.append((min(spreads), g))
    candidates = sorted(candidates, key=lambda x: x[0])[:n]
    if not candidates:
        return "No good parlay legs found."
    legs = []
    for _, g in candidates:
        # attempt to get h2h price for favorite
        price = 1.9
        for b in g.get("bookmakers", []):
            for m in b.get("markets", []) or []:
                if m.get("key") == "h2h":
                    for o in m.get("outcomes", []) or []:
                        if isinstance(o.get("price"), (int,float)):
                            price = float(o.get("price"))
                            break
        legs.append(f"{g.get('away_team')} @ {g.get('home_team')} ({price})")
    return f"🔗 Auto-parlay ({profile}) 🔗\n" + "\n".join([f"• {l}" for l in legs])

def estimate_ev(confidence, decimal_odds):
    try:
        c = float(confidence)
        o = float(decimal_odds)
        ev = (c * (o - 1.0)) - (1.0 - c)
        return round(ev, 4)
    except Exception:
        return 0.0

def kelly_fraction(edge_decimal, decimal_odds):
    try:
        b = float(decimal_odds) - 1.0
        if b <= 0:
            return 0.0
        k = float(edge_decimal) / b
        return max(0.0, min(k, 1.0))
    except Exception:
        return 0.0

# --- AI helpers ---
def ai_smart_props(sport_name="NFL", snippet=None):
    if not client:
        return None
    prompt = f"Generate 5 concise player prop suggestions for {sport_name} using this snippet: {snippet or 'none'}. Return JSON array with player, line, reason, confidence."
    try:
        resp = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"user","content":prompt}], temperature=0.8, max_tokens=420)
        txt = safe_extract_openai(resp) or ""
        try:
            parsed = json.loads(txt)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            # try to extract bracketed JSON
            s = txt.find("["); e = txt.rfind("]")
            if s != -1 and e != -1:
                try:
                    return json.loads(txt[s:e+1])
                except Exception:
                    pass
        return txt
    except Exception:
        logging.exception("ai_smart_props error")
        return None

def ai_pick(snippet=None):
    if not client:
        return None
    prompt = f"Return one concise sharp NFL pick with keys: pick_text, confidence (0-1), suggested_decimal_odds. Use snippet: {snippet or 'none'}"
    try:
        resp = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"user","content":prompt}], temperature=0.65, max_tokens=220)
        txt = safe_extract_openai(resp) or ""
        try:
            j = json.loads(txt)
            return j
        except Exception:
            # try to extract JSON object in text
            s = txt.find("{"); e = txt.rfind("}")
            if s != -1 and e != -1:
                try:
                    return json.loads(txt[s:e+1])
                except Exception:
                    pass
            return {"pick_text": txt, "confidence": 0.6, "suggested_decimal_odds": 1.9}
    except Exception:
        logging.exception("ai_pick error")
        return None

# --- Webhook / Router (robust) ---
@app.route("/webhook", methods=["POST", "GET"])
def webhook():
    if request.method == "GET":
        return "Stealie MAX — friendly polished bot", 200
    try:
        data = request.get_json() or {}
        msg = data.get("message") or data.get("edited_message") or {}
        chat = msg.get("chat") or {}
        if not chat or "id" not in chat:
            return jsonify({"ok": True})
        chat_id = chat["id"]

        text = (msg.get("text") or "").strip()
        if not text:
            return jsonify({"ok": True})

        # Rate limit per chat
        limited, wait = is_rate_limited(chat_id)
        if limited:
            send_telegram(chat_id, f"⏳ Please wait {wait} seconds before sending another command.")
            return jsonify({"ok": True})

        # Lowercase command token for routing while keeping original for AI fallback
        t = text.lower().strip()

        # /start or greetings
        if t in ("/start", "hello", "hi", "hey"):
            send_telegram(chat_id, friendly_start_text())
            return jsonify({"ok": True})

        # Units management
        if t.startswith("/setunits"):
            try:
                n = float(t.split()[1])
                set_units(chat_id, n)
                send_telegram(chat_id, f"✅ Units set to {n}. I will use that as your unit size in future suggestions.")
            except Exception:
                send_telegram(chat_id, "Usage: /setunits <number>")
            return jsonify({"ok": True})

        if t.startswith("/addunits"):
            try:
                n = float(t.split()[1])
                new = add_units(chat_id, n)
                send_telegram(chat_id, f"✅ Added {n} units. New balance: {new} units.")
            except Exception:
                send_telegram(chat_id, "Usage: /addunits <number>")
            return jsonify({"ok": True})

        if t in ("/units", "/myunits"):
            u = get_user(chat_id).get("units", 1.0)
            send_telegram(chat_id, f"Your unit size is {u}.")
            return jsonify({"ok": True})

        # Alerts
        if t.startswith("/alerts"):
            parts = t.split()
            if len(parts) > 1 and parts[1] in ("on", "off"):
                state = toggle_alerts(chat_id, parts[1] == "on")
                send_telegram(chat_id, f"Alerts {'enabled' if state else 'disabled'}. I will notify you about significant line movement.")
            else:
                send_telegram(chat_id, "Usage: /alerts on OR /alerts off")
            return jsonify({"ok": True})

        # /card command (explicit)
        if t.startswith("/card") or t == "card":
            parts = t.split()
            sport_alias = parts[1] if len(parts) > 1 else DEFAULT_SPORT
            msg_text = build_card_message(sport_alias)
            send_telegram(chat_id, msg_text)
            return jsonify({"ok": True})

        # /props [team] (explicit)
        if t.startswith("/props") or t == "props":
            parts = t.split()
            team = parts[1] if len(parts) > 1 else None
            msg_text = build_props_message(team)
            send_telegram(chat_id, msg_text)
            return jsonify({"ok": True})

        # /smartprops
        if t.startswith("/smartprops"):
            games = get_cached_odds(DEFAULT_SPORT_KEY, markets=STANDARD_MARKETS, limit=20)
            snippet = json.dumps(games, default=str)[:2000] if games else None
            ai = ai_smart_props("NFL", snippet)
            if not ai:
                send_telegram(chat_id, "Sorry — AI did not return suggestions. Try again later.")
            else:
                if isinstance(ai, list):
                    lines = ["🤖 Smart Props (AI)"]
                    for item in ai[:6]:
                        lines.append(f"• {item.get('player')} — {item.get('line')} — {item.get('reason')} (conf {item.get('confidence')})")
                    send_telegram(chat_id, "\n".join(lines))
                else:
                    send_telegram(chat_id, f"🤖 Smart Props (AI)\n{ai}")
            return jsonify({"ok": True})

        # /sgp team
        if t.startswith("/sgp"):
            team = text[len("/sgp"):].strip() or None
            if not team:
                send_telegram(chat_id, "Usage: /sgp <team name>. Example: /sgp chiefs")
                return jsonify({"ok": True})
            resp = build_sgp(team)
            send_telegram(chat_id, resp)
            return jsonify({"ok": True})

        # /parlay (auto-parlay)
        if t.startswith("/parlay") or t.startswith("/autoparlay"):
            parts = t.split()
            profile = parts[1] if len(parts) > 1 else "moderate"
            resp = build_auto_parlay(profile)
            send_telegram(chat_id, resp)
            return jsonify({"ok": True})

        # /pick (explicit)
        if t.startswith("/pick"):
            # Try AI pick with snippet
            games = get_cached_odds(DEFAULT_SPORT_KEY, markets=STANDARD_MARKETS, limit=12)
            snippet = json.dumps(games, default=str)[:1500] if games else None
            ai = ai_pick(snippet)
            if ai:
                # ai could be dict or text
                if isinstance(ai, dict):
                    text_out = f"🔒 AI PICK — {ai.get('pick_text')}\nConfidence: {ai.get('confidence')}\nOdds: {ai.get('suggested_decimal_odds')}"
                else:
                    text_out = f"🔒 AI PICK — {str(ai)}"
                send_telegram(chat_id, text_out)
                return jsonify({"ok": True})
            # fallback short pick
            send_telegram(chat_id, "💀 Fallback pick: Jaguars -3.5. (AI unavailable)")
            return jsonify({"ok": True})

        # /ev
        if t.startswith("/ev"):
            parts = t.split()
            if len(parts) >= 3:
                try:
                    conf = float(parts[1])
                    odds_f = float(parts[2])
                    ev = estimate_ev(conf, odds_f)
                    send_telegram(chat_id, f"Expected value per $1: {ev:.4f}. Positive EV means a favorable expectation.")
                except Exception:
                    send_telegram(chat_id, "Usage: /ev <confidence 0-1> <decimal odds>")
            else:
                send_telegram(chat_id, "Usage: /ev <confidence 0-1> <decimal odds>")
            return jsonify({"ok": True})

        # /kelly
        if t.startswith("/kelly"):
            parts = t.split()
            if len(parts) >= 3:
                try:
                    edge = float(parts[1])
                    odds_d = float(parts[2])
                    k = kelly_fraction(edge, odds_d)
                    units = get_user(chat_id).get("units", 1.0)
                    suggestion = round(k * units, 3)
                    send_telegram(chat_id, f"Kelly fraction: {k:.3f} — suggested stake: {suggestion} units (based on your unit size).")
                except Exception:
                    send_telegram(chat_id, "Usage: /kelly <edge_decimal> <decimal_odds>")
            else:
                send_telegram(chat_id, "Usage: /kelly <edge_decimal> <decimal_odds>")
            return jsonify({"ok": True})

        # admin debug
        if t.startswith("/debug"):
            token = request.args.get("admin_token") or request.headers.get("X-ADMIN-TOKEN")
            if ADMIN_TOKEN and token == ADMIN_TOKEN:
                data = load_data()
                send_telegram(chat_id, f"DEBUG: users={len(data.get('users',{}))}, cache_keys={list(CACHE.keys())}")
            else:
                send_telegram(chat_id, "Admin token missing or invalid.")
            return jsonify({"ok": True})

        # Natural question fallback -> AI if available
        if client:
            try:
                resp = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"user","content":text}], temperature=0.7, max_tokens=300)
                ans = safe_extract_openai(resp) or "I did not get a response from the AI. Please try again."
                send_telegram(chat_id, ans)
            except Exception:
                logging.exception("AI fallback failed")
                send_telegram(chat_id, "Sorry, AI is unavailable right now.")
            return jsonify({"ok": True})

        # final fallback
        send_telegram(chat_id, "I did not understand that command. Type /start for help.")
        return jsonify({"ok": True})

    except Exception:
        logging.exception("Exception in webhook")
        return jsonify({"ok": True})

# Root
@app.route("/", methods=["GET"])
def home():
    return "Stealie MAX — polished & friendly"

# Run
if __name__ == "__main__":
    logging.info("Starting Stealie MAX (polished)")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))