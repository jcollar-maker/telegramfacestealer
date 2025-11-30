#!/usr/bin/env python3
"""
Stealie MAX 2.0 — Nuclear Edition
Features:
- Multi-sport cards (NFL default), NBA, MLB, CFB support
- EV / Kelly calculators
- Auto-parlay with risk profiles
- AI-powered smart props / daily cheat sheet
- Line movement / sharp steam tracker with alerts
- Injury auto-pull (optional via INJURY_API_URL)
- VIP admin endpoints protected by ADMIN_TOKEN
- Persistent user data (units, subscriptions) in JSON
"""

import os
import json
import time
import math
import logging
import random
import re
import requests
from datetime import datetime, timedelta
from threading import Lock, Thread
from flask import Flask, request, jsonify, abort

# OpenAI safe import
try:
    from openai import OpenAI
except Exception:
    OpenAI = None

# -------------------------
# Configuration & Env
# -------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
app = Flask(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ODDS_KEY = os.getenv("ODDS_API_KEY")
OPENAI_KEY = os.getenv("OPENAI_KEY")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN")
INJURY_API_URL = os.getenv("INJURY_API_URL")

DATA_PATH = os.environ.get("DATA_PATH", "/tmp/stealie2_data.json")
DATA_LOCK = Lock()

# Initialize OpenAI client if available
client = None
if OpenAI and OPENAI_KEY:
    os.environ["OPENAI_API_KEY"] = OPENAI_KEY
    try:
        client = OpenAI()
        logging.info("OpenAI client initialized")
    except Exception:
        logging.exception("OpenAI init failed — continuing without AI")

# Cache & movement tracking
CACHE = {}
CACHE_LOCK = Lock()
CACHE_TTL = int(os.environ.get("ODDS_CACHE_TTL_SEC", 60))
MOVEMENT_ALERT_THRESHOLD = float(os.environ.get("MOVEMENT_THRESHOLD", 0.5))  # points

# Defaults
DEFAULT_SPORT = "americanfootball_nfl"  # NFL default
SPORT_ALIASES = {
    "nfl": "americanfootball_nfl",
    "cfb": "americanfootball_ncaaf",
    "nba": "basketball_nba",
    "mlb": "baseball_mlb",
    "nhl": "icehockey_nhl"
}
DEFAULT_ODDS_LIMIT = 30

# -------------------------
# Persistence helpers
# -------------------------
def load_data():
    try:
        with DATA_LOCK:
            if not os.path.exists(DATA_PATH):
                return {"users": {}, "cache": {}, "alerts": {}}
            with open(DATA_PATH, "r") as f:
                return json.load(f)
    except Exception:
        logging.exception("load_data failed")
        return {"users": {}, "cache": {}, "alerts": {}}

def save_data(d):
    try:
        with DATA_LOCK:
            with open(DATA_PATH, "w") as f:
                json.dump(d, f, indent=2)
    except Exception:
        logging.exception("save_data failed")

# Ensure file exists
save_data(load_data())

# -------------------------
# Units and user helpers
# -------------------------
def get_user(chat_id):
    d = load_data()
    return d.get("users", {}).get(str(chat_id), {"units": 1.0, "alerts": False, "vip": False})

def set_user(chat_id, user_dict):
    d = load_data()
    d.setdefault("users", {})
    d["users"][str(chat_id)] = user_dict
    save_data(d)

def set_units(chat_id, units):
    user = get_user(chat_id)
    user["units"] = float(units)
    set_user(chat_id, user)
    return user["units"]

def add_units(chat_id, delta):
    user = get_user(chat_id)
    user["units"] = float(user.get("units", 1.0)) + float(delta)
    set_user(chat_id, user)
    return user["units"]

def get_units(chat_id):
    return float(get_user(chat_id).get("units", 1.0))

def subscribe_alerts(chat_id, on=True):
    user = get_user(chat_id)
    user["alerts"] = bool(on)
    set_user(chat_id, user)
    return user["alerts"]

# -------------------------
# Odds fetcher & caching
# -------------------------
def odds_api_fetch(sport_key, limit=DEFAULT_ODDS_LIMIT, markets="h2h,spreads,totals,player_props"):
    if not ODDS_KEY:
        logging.warning("ODDS_API_KEY missing")
        return []
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds"
    try:
        r = requests.get(url, params={"apiKey": ODDS_KEY, "regions": "us", "markets": markets, "oddsFormat": "decimal"}, timeout=12)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                return data[:limit]
            return data
        logging.warning("Odds API status %s", r.status_code)
    except Exception:
        logging.exception("odds_api_fetch error")
    return []

def cache_get(sport_key):
    key = f"odds::{sport_key}"
    with CACHE_LOCK:
        rec = CACHE.get(key)
        if rec and (time.time() - rec["ts"]) < CACHE_TTL:
            return rec["data"]
    # miss: fetch and store, but also compare to previous for movement
    new = odds_api_fetch(sport_key)
    with CACHE_LOCK:
        prev = CACHE.get(key, {}).get("data")
        CACHE[key] = {"ts": time.time(), "data": new}
    # check movement
    if prev and new:
        detect_movement_and_alerts(prev, new, sport_key)
    return new

# -------------------------
# Movement detection + alerts
# -------------------------
def find_spread_for_team(game, team_name):
    for b in game.get("bookmakers", []):
        for m in b.get("markets", []):
            if m.get("key") == "spreads":
                for o in m.get("outcomes", []):
                    if (o.get("name") or "").lower() == team_name.lower():
                        return o.get("point")
    return None

def detect_movement_and_alerts(prev_list, new_list, sport_key):
    # build dict by id or teams
    prev_map = {}
    for g in prev_list:
        gid = g.get("id") or f"{g.get('home_team')}@{g.get('away_team')}"
        prev_map[gid] = g
    for g in new_list:
        gid = g.get("id") or f"{g.get('home_team')}@{g.get('away_team')}"
        p = prev_map.get(gid)
        if not p: continue
        # compare home spreads
        home = g.get("home_team")
        try:
            new_spread = find_spread_for_team(g, home)
            old_spread = find_spread_for_team(p, home)
            if new_spread is not None and old_spread is not None:
                diff = abs(float(new_spread) - float(old_spread))
                if diff >= MOVEMENT_ALERT_THRESHOLD:
                    # send alert to subscribers
                    send_movement_alerts(g, home, old_spread, new_spread, sport_key)
        except Exception:
            continue

def send_movement_alerts(game, team, old, new, sport_key):
    d = load_data()
    for chat_str, user in d.get("users", {}).items():
        if user.get("alerts"):
            try:
                chat_id = int(chat_str)
                txt = (f"⚠️ Line Movement Alert — {game.get('away_team')} @ {game.get('home_team')}\n"
                       f"{team} moved from {old} to {new} ({sport_key})")
                send_telegram(chat_id, txt)
            except Exception:
                continue

# -------------------------
# Helpers: extract markets / sharp scoring
# -------------------------
def compare_lines(game):
    spreads = {}
    totals = []
    h2h = []
    for b in game.get("bookmakers", []):
        bname = b.get("title", "book")
        for m in b.get("markets") or []:
            key = m.get("key")
            if key == "spreads":
                for o in m.get("outcomes", []):
                    spreads.setdefault(o.get("name"), []).append((bname, o.get("point")))
            elif key == "totals":
                for o in m.get("outcomes", []):
                    totals.append((bname, o.get("point")))
            elif key == "h2h":
                prices = {}
                for o in m.get("outcomes", []):
                    prices[o.get("name")] = o.get("price")
                h2h.append((bname, prices))
    return {"spreads": spreads, "totals": totals, "h2h": h2h}

def compute_sharp(game):
    try:
        comp = compare_lines(game)
        all_std = []
        for team, pts in comp["spreads"].items():
            vals = [p for (_, p) in pts if isinstance(p, (int, float))]
            if len(vals) > 1:
                all_std.append(float(max(vals) - min(vals)))
        score = (sum(all_std) / len(all_std)) if all_std else 0.0
        return round(score, 3)
    except Exception:
        return 0.0

# -------------------------
# EV & Kelly calculators
# -------------------------
def estimate_ev(confidence, decimal_odds):
    try:
        conf = float(confidence)
        ev = (conf * (decimal_odds - 1.0)) - (1.0 - conf)
        return round(ev, 4)
    except Exception:
        return 0.0

def kelly_fraction(edge, decimal_odds):
    # edge in decimal (e.g., 0.1 for 10%)
    try:
        b = decimal_odds - 1.0
        q = 1.0 - edge
        k = (edge / b) if b > 0 else 0.0
        return max(0.0, min(k, 1.0))
    except Exception:
        return 0.0

# -------------------------
# OpenAI helpers (robust)
# -------------------------
def safe_extract(resp):
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

def ai_daily_cheatsheet(sport_name="NFL", games_snippet=None):
    if not client:
        return None
    prompt = (f"Create a short daily cheat sheet for {sport_name}. Use the snippet: {games_snippet or 'none'}. "
              "Return bullet points with 6 items: top pick, value, prop to watch, injury to monitor, sharp edge, note.")
    try:
        resp = client.chat.completions.create(model="gpt-4o-mini",
                                             messages=[{"role": "user", "content": prompt}],
                                             temperature=0.7, max_tokens=300)
        return safe_extract(resp)
    except Exception:
        logging.exception("ai_daily_cheatsheet failed")
        return None

def ai_smart_props(sport_name="NFL", snippet=None):
    if not client:
        return None
    prompt = (f"Generate 5 smart player props for {sport_name} from this snippet: {snippet or 'none'}."
              "Return JSON array of objects with player,line,reason,confidence.")
    try:
        resp = client.chat.completions.create(model="gpt-4o-mini",
                                             messages=[{"role": "user", "content": prompt}],
                                             temperature=0.8, max_tokens=400)
        txt = safe_extract(resp) or ""
        # try parse JSON
        try:
            return json.loads(txt)
        except Exception:
            # try to extract bracketed JSON
            s = txt.find("["); e = txt.rfind("]")
            if s != -1 and e != -1:
                try:
                    return json.loads(txt[s:e+1])
                except Exception:
                    pass
        return None
    except Exception:
        logging.exception("ai_smart_props failed")
        return None

# -------------------------
# Telegram send wrapper
# -------------------------
def send_telegram(chat_id, text):
    if not TELEGRAM_TOKEN:
        logging.warning("TELEGRAM_TOKEN missing")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=8)
    except Exception:
        logging.exception("send_telegram failed")

# -------------------------
# Injury fetch (optional)
# -------------------------
def fetch_injuries():
    if not INJURY_API_URL:
        return {}
    try:
        r = requests.get(INJURY_API_URL, timeout=6)
        if r.status_code == 200:
            return r.json()
    except Exception:
        logging.exception("fetch_injuries failed")
    return {}

# -------------------------
# Command router
# -------------------------
@app.route("/webhook", methods=["POST", "GET"])
def webhook():
    if request.method == "GET":
        return "Stealie MAX 2.0 — online", 200
    try:
        data = request.get_json() or {}
        # support edited_message too
        msg = data.get("message") or data.get("edited_message") or {}
        chat = msg.get("chat") or {}
        if not chat or "id" not in chat:
            return jsonify({"ok": True})
        chat_id = chat["id"]
        text = (msg.get("text") or "").strip()
        if not text:
            return jsonify({"ok": True})
        t = text.lower().strip()

        # basic commands
        if t in ("/start", "hi", "hello"):
            greeting = ("👋 Stealie MAX 2.0 — Nuclear Edition\n"
                        "Commands: /card [nfl|nba|mlb|cfb], /pick, /parlay [profile], /autoparlay [profile],\n"
                        "/props [team], /smartprops, /daily, /ev <conf> <odds>, /kelly <edge_pct> <odds>,\n"
                        "/units, /setunits <n>, /addunits <n>, /alerts on|off, /vip (admin)\n")
            send_telegram(chat_id, greeting)
            return jsonify({"ok": True})

        # set / add units
        if t.startswith("/setunits"):
            try:
                n = float(t.split()[1])
                set_units(chat_id, n)
                send_telegram(chat_id, f"✅ Units set to {n}")
            except Exception:
                send_telegram(chat_id, "Usage: /setunits <number>")
            return jsonify({"ok": True})

        if t.startswith("/addunits"):
            try:
                n = float(t.split()[1])
                new = add_units(chat_id, n)
                send_telegram(chat_id, f"✅ Added {n} units — New: {new}")
            except Exception:
                send_telegram(chat_id, "Usage: /addunits <number>")
            return jsonify({"ok": True})

        if t in ("/units", "/myunits"):
            send_telegram(chat_id, f"Units: {get_units(chat_id)}")
            return jsonify({"ok": True})

        # alerts subscribe
        if t.startswith("/alerts"):
            parts = t.split()
            if len(parts) > 1 and parts[1] in ("on", "off"):
                state = subscribe_alerts(chat_id, parts[1] == "on")
                send_telegram(chat_id, f"Alerts {'enabled' if state else 'disabled'}")
            else:
                send_telegram(chat_id, "Usage: /alerts on|off")
            return jsonify({"ok": True})

        # admin vip endpoint
        if t.startswith("/vip"):
            token = request.args.get("admin_token") or request.headers.get("X-ADMIN-TOKEN")
            if ADMIN_TOKEN and token == ADMIN_TOKEN:
                send_telegram(chat_id, "✅ VIP access confirmed.")
            else:
                send_telegram(chat_id, "❌ VIP token missing or invalid.")
            return jsonify({"ok": True})

        # EV calc: /ev 0.65 2.1
        if t.startswith("/ev"):
            parts = t.split()
            if len(parts) >= 3:
                try:
                    conf = float(parts[1])
                    odds_f = float(parts[2])
                    ev = estimate_ev(conf, odds_f)
                    send_telegram(chat_id, f"EV per $1: {ev}")
                except Exception:
                    send_telegram(chat_id, "Usage: /ev <confidence 0-1> <decimal_odds>")
            else:
                send_telegram(chat_id, "Usage: /ev <confidence 0-1> <decimal_odds>")
            return jsonify({"ok": True})

        # Kelly calc: /kelly 0.1 2.2  (edge decimal, odds decimal)
        if t.startswith("/kelly"):
            parts = t.split()
            if len(parts) >= 3:
                try:
                    edge = float(parts[1])
                    odds_d = float(parts[2])
                    k = kelly_fraction(edge, odds_d)
                    units = get_units(chat_id)
                    suggested = round(k * units, 3)
                    send_telegram(chat_id, f"Kelly fraction: {k:.3f} — Suggested units: {suggested}")
                except Exception:
                    send_telegram(chat_id, "Usage: /kelly <edge_decimal> <decimal_odds>")
            else:
                send_telegram(chat_id, "Usage: /kelly <edge_decimal> <decimal_odds>")
            return jsonify({"ok": True})

        # /card [sport]
        if t.startswith("/card") or t.startswith("card"):
            parts = t.split()
            sp = parts[1] if len(parts) > 1 else "nfl"
            sport_key = SPORT_ALIASES.get(sp, DEFAULT_SPORT)
            games = cache_get(sport_key) or []
            if not games:
                send_telegram(chat_id, f"No {sp.upper()} odds available right now.")
                return jsonify({"ok": True})
            # build short card
            out = [f"🔥 {sp.upper()} CARD — {datetime.utcnow().strftime('%Y-%m-%d')}"]
            for g in games[:10]:
                home = g.get("home_team","?")
                away = g.get("away_team","?")
                time_str = g.get("commence_time","?")
                out.append(f"{away} @ {home} — {time_str}")
            send_telegram(chat_id, "\n".join(out))
            return jsonify({"ok": True})

        # /props [team]
        if t.startswith("/props") or t.startswith("props"):
            parts = t.split()
            team = parts[1] if len(parts)>1 else None
            sport_key = SPORT_ALIASES.get("nfl")
            props = odds_api_fetch(sport_key, markets="player_props")
            # filter by team if requested
            filtered = []
            for g in props:
                if team:
                    if team.lower() in (g.get("home_team","")+" "+g.get("away_team","")).lower():
                        filtered.append(g)
                else:
                    filtered.append(g)
            if not filtered:
                # AI fallback
                if client:
                    snippet = json.dumps(props, default=str)[:1500]
                    ai = ai_smart_props("NFL", snippet)
                    if ai:
                        send_telegram(chat_id, f"🔥 AI Smart Props 🔥\n{ai}")
                        return jsonify({"ok": True})
                send_telegram(chat_id, "⚠️ No props available (books or AI).")
                return jsonify({"ok": True})
            # present top props from filtered
            out = ["🔥 TOP PLAYER PROPS 🔥"]
            count = 0
            for g in filtered:
                for bk in g.get("bookmakers", []):
                    for m in bk.get("markets", []):
                        if "player" in (m.get("key") or ""):
                            for o in m.get("outcomes", [])[:3]:
                                player = o.get("description") or o.get("name")
                                line = o.get("point"); price = o.get("price")
                                out.append(f"{g.get('away_team')} @ {g.get('home_team')} — {player} {line} ({price})")
                                count += 1
                                if count >= 8: break
                        if count >= 8: break
                    if count >= 8: break
                if count >= 8: break
            send_telegram(chat_id, "\n".join(out))
            return jsonify({"ok": True})

        # /smartprops -> use AI to suggest props across snippet
        if t.startswith("/smartprops"):
            snippet = ""
            games = cache_get(SPORT_ALIASES.get("nfl", DEFAULT_SPORT))
            if games:
                snippet = json.dumps(games[:8], default=str)[:2000]
            ai = ai_smart_props("NFL", snippet)
            if ai:
                # ai is a list of dicts
                lines = ["🔥 SMART PROPS (AI) 🔥"]
                try:
                    for item in ai[:5]:
                        lines.append(f"{item.get('player')} — {item.get('line')} — {item.get('reason')} (conf {item.get('confidence')})")
                    send_telegram(chat_id, "\n".join(lines))
                except Exception:
                    send_telegram(chat_id, str(ai))
            else:
                send_telegram(chat_id, "⚠️ AI unavailable or failed.")
            return jsonify({"ok": True})

        # /daily cheat-sheet
        if t.startswith("/daily"):
            games = cache_get(DEFAULT_SPORT)[:12]
            snippet = json.dumps(games, default=str)[:2000] if games else None
            sheet = ai_daily_cheatsheet("NFL", snippet) if client else None
            if sheet:
                send_telegram(chat_id, f"🗞 DAILY CHEAT SHEET 🗞\n{sheet}")
            else:
                # fallback: quick sharp picks from compute_sharp
                out = ["🗞 QUICK CHEAT SHEET (fallback)"]
                scored = []
                for g in games[:20]:
                    scored.append((compute_sharp(g), g))
                scored = sorted(scored, key=lambda x: x[0], reverse=True)[:5]
                for sc, g in scored:
                    out.append(f"{g.get('away_team')} @ {g.get('home_team')} — SharpScore {sc}")
                send_telegram(chat_id, "\n".join(out))
            return jsonify({"ok": True})

        # /autoparlay [profile]
        if t.startswith("/autoparlay") or t.startswith("/parlay"):
            parts = t.split()
            profile = parts[1] if len(parts) > 1 else "moderate"
            # build n legs based on profile
            if profile == "conservative":
                n=2
            elif profile == "aggro" or profile == "aggressive":
                n=5
            else:
                n=3
            games = cache_get(DEFAULT_SPORT, ) or []
            # pick n closest spreads (i.e., closer spreads = safer)
            candidates = []
            for g in games:
                try:
                    spares = []
                    for b in g.get("bookmakers", []):
                        for m in b.get("markets", []):
                            if m.get("key")=="spreads":
                                for o in m.get("outcomes", []):
                                    if isinstance(o.get("point"), (int,float)):
                                        spares.append(abs(o.get("point")))
                    if spares:
                        candidates.append((min(spares), g))
                except Exception:
                    continue
            candidates = sorted(candidates, key=lambda x: x[0])[:n]
            if not candidates:
                send_telegram(chat_id, "⚠️ Could not build auto parlay now.")
                return jsonify({"ok": True})
            legs=[]
            for _, g in candidates:
                home = g.get("home_team"); away = g.get("away_team")
                # try to fetch h2h price
                price = 1.9
                for b in g.get("bookmakers", []):
                    for m in b.get("markets", []):
                        if m.get("key")=="h2h":
                            for o in m.get("outcomes", []):
                                price = float(o.get("price") or price)
                legs.append(f"{away} @ {home} ({price})")
            send_telegram(chat_id, f"🔗 AUTO-PARLAY ({profile}) 🔗\n" + "\n".join(legs))
            return jsonify({"ok": True})

        # /pick (AI or fallback)
        if t.startswith("/pick") or "pick" in t:
            # use AI if present
            if client:
                try:
                    prompt = f"Return one concise NFL pick with confidence 0-1 and suggested_decimal_odds for {datetime.utcnow().date()}."
                    resp = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"user","content":prompt}], temperature=0.7, max_tokens=180)
                    txt = safe_extract(resp) or ""
                    send_telegram(chat_id, f"🔒 AI PICK 🔒\n{txt}")
                    return jsonify({"ok": True})
                except Exception:
                    logging.exception("AI pick failed")
            # fallback
            send_telegram(chat_id, "💀 Fallback pick: Jaguars -3.5")
            return jsonify({"ok": True})

        # /sgp team
        if t.startswith("/sgp"):
            team = t.replace("/sgp","").strip() or "jaguars"
            # call our earlier SGP builder which uses props
            response = sgp(team)
            send_telegram(chat_id, response)
            return jsonify({"ok": True})

        # /ev and /kelly handled above

        # admin debug endpoint via command
        if t.startswith("/debug"):
            token = request.args.get("admin_token") or request.headers.get("X-ADMIN-TOKEN")
            if ADMIN_TOKEN and token == ADMIN_TOKEN:
                d = load_data()
                send_telegram(chat_id, f"DEBUG: {json.dumps({'cache_keys': list(CACHE.keys()), 'users_count': len(d.get('users',{}))})}")
            else:
                send_telegram(chat_id, "Admin token missing")
            return jsonify({"ok": True})

        # fallback: simple natural question -> AI if available
        if client:
            try:
                resp = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"user","content":text}], temperature=0.7, max_tokens=250)
                ans = safe_extract(resp) or "No response"
                send_telegram(chat_id, ans)
            except Exception:
                logging.exception("AI fallback failed")
                send_telegram(chat_id, "⚠️ AI unavailable.")
            return jsonify({"ok": True})
        else:
            send_telegram(chat_id, "⚠️ Command not recognized. Use /start for help.")
            return jsonify({"ok": True})

    except Exception:
        logging.exception("Exception in webhook")
        return jsonify({"ok": True})

# -------------------------
# Root and run
# -------------------------
@app.route("/")
def home():
    return "Stealie MAX 2.0 — Nuclear Edition"

if __name__ == "__main__":
    logging.info("Starting Stealie MAX 2.0")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))