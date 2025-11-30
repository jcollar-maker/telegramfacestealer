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

# Environment variables (make sure these are set in your hosting env)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_KEY") or os.getenv("OPENAI_API_KEY")
ODDS_KEY = os.getenv("ODDS_API_KEY")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", None)

# OpenAI client
client = OpenAI(api_key=OPENAI_KEY) if OPENAI_KEY else None

# Persistence path (change if needed)
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
def extract_market_by_key(bookmarks, key):
    for b in bookmarks:
        for m in b.get("markets", []):
            if m.get("key") == key:
                yield b, m

def compare_lines_across_books(game):
    """Return compact structure: spreads_by_team, totals_list, h2h_prices"""
    spreads = {}   # team -> list of (book, point)
    totals = []    # list of (book, point)
    h2h = []       # list of (book, {team: price})
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
    """
    Produces a 'sharpness' score:
    - smaller absolute mean spread => more competitive => higher interest
    - higher stddev across books => movement or disagreement => higher interest
    - divergence between implied ML and consensus => higher interest
    Returns float where larger = more interesting.
    """
    try:
        comp = compare_lines_across_books(game)
        # pick home team (if available) or any team as representative
        teams = list(comp["spreads"].keys())
        if not teams:
            return 0.0
        # compute metrics across all teams
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

        # h2h implied probability divergence
        ml_divergence = 0.0
        if comp["h2h"]:
            # compute average implied for each team
            team_probs = {}
            for _, price_map in comp["h2h"]:
                for t, pr in (price_map or {}).items():
                    if isinstance(pr, (int, float)):
                        team_probs.setdefault(t, []).append(pr)
            for t, ps in team_probs.items():
                avg = statistics.mean(ps)
                # implied probability from decimal: 1/odds
                if avg and avg > 1.01:
                    prob = 1.0 / avg
                else:
                    prob = 0.5
                # divergence from 0.5 scales
                ml_divergence += abs(prob - 0.5)
            if team_probs:
                ml_divergence = ml_divergence / len(team_probs)
        # combine metrics: lower mean_abs (closer game) and higher std & divergence increase score
        score = (1.0 / (1.0 + mean_abs)) * (1.0 + mean_std * 1.2 + ml_divergence * 2.0)
        return float(round(score, 4))
    except Exception:
        logging.exception("compute_sharp_score error")
        return 0.0

# -------------------------
# Model grading & EV
# -------------------------
def grade_from_confidence(conf):
    """
    conf: 0..1
    Returns A-F
    """
    if conf >= 0.85:
        return "A"
    if conf >= 0.7:
        return "B"
    if conf >= 0.55:
        return "C"
    if conf >= 0.4:
        return "D"
    return "F"

def estimate_ev(confidence, decimal_odds):
    """
    Simple EV model: EV = (confidence * (odds - 1)) - (1 - confidence)
    Returns expected return per $1 wager.
    """
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
# Parlay / SGP builder
# -------------------------
def build_auto_parlay(sport_key="americanfootball_nfl", n_legs=3, stake_units=1):
    games = get_cached_odds(sport_key, limit=30)
    if not games:
        return None, "Odds unavailable"

    candidates = []
    for g in games:
        try:
            comp = compare_lines_across_books(g)
            # choose smallest abs mean spread across teams as candidate (close games)
            for team, pts in comp["spreads"].items():
                vals = [p for (_, p) in pts if isinstance(p, (int, float))]
                if not vals:
                    continue
                mean = statistics.mean(vals)
                candidates.append({
                    "game": g,
                    "team": team,
                    "line": mean,
                    "score": abs(mean)
                })
        except Exception:
            continue

    # choose n_legs with smallest abs spread and unique games
    candidates_sorted = sorted(candidates, key=lambda x: x["score"])[: n_legs * 4]
    legs = []
    seen_games = set()
    for c in candidates_sorted:
        if len(legs) >= n_legs:
            break
        gid = c["game"].get("id") or (c["game"].get("home_team") + "@" + c["game"].get("away_team"))
        if gid in seen_games:
            continue
        seen_games.add(gid)
        # try to pull decimal odds from h2h if available
        odds_decimal = 1.9
        try:
            for b in c["game"].get("bookmakers", []):
                for m in b.get("markets", []):
                    if m.get("key") == "h2h":
                        for o in m.get("outcomes", []):
                            if o.get("name") == c["team"] and isinstance(o.get("price"), (int, float)):
                                odds_decimal = float(o.get("price"))
                                raise StopIteration
        except StopIteration:
            pass
        except Exception:
            pass

        legs.append({
            "team": c["team"],
            "market": "spread",
            "line": c["line"],
            "odds_decimal": float(odds_decimal),
            "book": c["game"].get("bookmakers", [{}])[0].get("title", "book")
        })

    if not legs:
        return None, "No suitable parlay legs found"

    slip = format_dabble_slip(legs, stake_units=stake_units, unit_value=1.0)
    return {"legs": legs, "slip": slip}, None

def build_sgp_for_team(team_name, sport_key="americanfootball_nfl"):
    """
    Build a same-game parlay for a single team (spread + total + player prop if available).
    """
    games = get_cached_odds(sport_key, limit=60)
    if not games:
        return None, "Odds unavailable"
    candidate = None
    for g in games:
        if team_name.lower() in g.get("home_team", "").lower() or team_name.lower() in g.get("away_team", "").lower():
            candidate = g
            break
    if not candidate:
        return None, f"No game found for {team_name}"
    legs = []
    comp = compare_lines_across_books(candidate)
    # spread leg: pick preferred team (exact string match prioritized)
    preferred_team = None
    if team_name.lower() in candidate.get("home_team", "").lower():
        preferred_team = candidate.get("home_team")
    elif team_name.lower() in candidate.get("away_team", "").lower():
        preferred_team = candidate.get("away_team")
    else:
        # fallback to away
        preferred_team = candidate.get("away_team")

    # spread leg
    spread_vals = comp["spreads"].get(preferred_team, [])
    spread_line = spread_vals[0][1] if spread_vals else None
    spread_odds = 1.9
    legs.append({"team": preferred_team, "market": "spread", "line": spread_line, "odds_decimal": spread_odds, "book": spread_vals[0][0] if spread_vals else "book"})
    # total leg
    if comp["totals"]:
        tot = comp["totals"][0]  # (book, point)
        legs.append({"team": f"{candidate.get('home_team')} v {candidate.get('away_team')}", "market": "total", "line": tot[1], "odds_decimal": 1.9, "book": tot[0]})
    # prop leg: find any player_props market if present
    prop_found = False
    for b in candidate.get("bookmakers", []):
        for m in b.get("markets", []):
            if "player" in (m.get("key") or "") or m.get("key") == "player_props":
                # take first player prop outcome
                o = m.get("outcomes", [])[0] if m.get("outcomes") else None
                if o:
                    legs.append({"team": o.get("name"), "market": "player_prop", "line": o.get("point"), "odds_decimal": float(o.get("price") or 1.9), "book": b.get("title")})
                    prop_found = True
                    break
        if prop_found:
            break
    # if no props, leave it as spread+total
    slip = format_dabble_slip(legs, stake_units=1, unit_value=1.0)
    return {"game": candidate, "legs": legs, "slip": slip}, None

# -------------------------
# AI integration (picks & props fallback)
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
        # try parse JSON
        try:
            j = json.loads(txt)
            return j
        except Exception:
            # attempt to extract JSON substring
            start = txt.find("{")
            end = txt.rfind("}")
            if start != -1 and end != -1:
                try:
                    j = json.loads(txt[start:end+1])
                    return j
                except Exception:
                    pass
            # fallback heuristic
            return {"pick_text": txt.split("\n")[0], "confidence": 0.6, "reason": "\n".join(txt.split("\n")[1:])[:240], "suggested_decimal_odds": 1.9}
    except Exception:
        logging.exception("call_openai_for_pick error")
        return None

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
            # try substring
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
            # try to get first bookmaker home spread & total
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
    # try direct props from games
    props = []
    if games:
        for g in games:
            for b in g.get("bookmakers", []):
                for m in b.get("markets", []):
                    # Some APIs use 'player_props' or include 'player' in key
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
        # take top 5 simple formatting
        lines = ["🔥 TOP PLAYER PROPS (from books) 🔥"]
        for p in props[:5]:
            lines.append(f"{p['player']} — {p['line']} @ {p['book']} (odds {p['odds']})")
        return "\n".join(lines)
    # fallback to AI-generated props
    odds_snippet = json.dumps(games, default=str)[:1200] if games else None
    ai_props = call_openai_for_props("NFL" if sport.lower() == "nfl" else "CFB", odds_snippet=odds_snippet)
    if ai_props:
        lines = ["🔥 TOP 5 PLAYER PROPS (AI) 🔥"]
        for item in ai_props[:5]:
            lines.append(f"{item.get('player')} — {item.get('line')} — {item.get('suggestion_text')} (conf {item.get('confidence')})")
        return "\n".join(lines)
    return "⚠️ No props available (books or AI)."

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

@app.route("/admin/health", methods=["GET"])
@require_admin
def admin_health():
    return jsonify({
        "ok": True,
        "time": datetime.utcnow().isoformat(),
        "cache_keys": list(CACHE.keys()),
        "cache_ttl_sec": CACHE_TTL,
        "missing_keys": [k for k in ["TELEGRAM_TOKEN", "OPENAI_KEY", "ODDS_API_KEY"] if not os.getenv(k)]
    })

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json() or {}
    if "message" not in data:
        return jsonify({"ok": True})
    msg = data["message"]
    chat_id = msg["chat"]["id"]
    text = msg.get("text", "").strip()

    # rate-limit
    limited, wait = is_rate_limited(chat_id)
    if limited:
        send_telegram(chat_id, f"⏳ Rate limit: try again in {wait}s.")
        return jsonify({"ok": True})

    t = text.lower()

    # unit management
    if t.startswith("set units"):
        try:
            parts = t.split()
            val = float(parts[2])
            set_units(chat_id, val)
            send_telegram(chat_id, f"✅ Units set to {val}.")
        except Exception:
            send_telegram(chat_id, "Usage: set units <number>")
        return jsonify({"ok": True})
    if t.startswith("add units"):
        try:
            val = float(t.split()[2])
            new = add_units(chat_id, val)
            send_telegram(chat_id, f"✅ Added {val} units. New balance: {new}")
        except Exception:
            send_telegram(chat_id, "Usage: add units <number>")
        return jsonify({"ok": True})
    if t in ("units", "my units"):
        u = get_units(chat_id)
        send_telegram(chat_id, f"Your units: {u}")
        return jsonify({"ok": True})

    # commands routing
    # Card requests
    if "card" in t or "slate" in t or "games" in t:
        # props card
        if "prop" in t or "props" in t or "player" in t:
            sport = "nfl" if "nfl" in t else "cfb" if "cfb" in t or "college" in t else "nfl"
            send_telegram(chat_id, props_card_text(sport))
            return jsonify({"ok": True})
        # sharp card
        if "sharp" in t:
            sport = "nfl" if "nfl" in t else "cfb" if "cfb" in t or "college" in t else "nfl"
            send_telegram(chat_id, sharp_card_text(sport))
            return jsonify({"ok": True})
        # sgp
        if "sgp" in t or "same-game" in t or "same game" in t:
            # expect "sgp teamname" -> take the rest as team
            parts = t.split()
            teamname = " ".join(parts[1:]) if len(parts) > 1 else ""
            if not teamname:
                send_telegram(chat_id, "Usage: sgp <team name> (e.g. sgp chiefs)")
                return jsonify({"ok": True})
            sport_key = "americanfootball_nfl" if "nfl" in t or "nfl" in text.lower() else "americanfootball_ncaaf"
            sgp, err = build_sgp_for_team(teamname, sport_key=sport_key)
            if err:
                send_telegram(chat_id, f"⚠️ {err}")
            else:
                human = f"🔒 SAME-GAME PARLAY for {teamname} 🔒\n"
                for i, leg in enumerate(sgp["legs"], 1):
                    human += f"{i}. {leg['market']} — {leg.get('team')} {leg.get('line')} @ {leg.get('book')} (odds {leg.get('odds_decimal')})\n"
                human += f"\nParlay odds: {sgp['slip']['parlay_odds_decimal']}x — Possible return on 1 unit: ${sgp['slip']['possible_return']}\n"
                human += "Use `/betparlay <units>` to place suggested parlay (must set units first)."
                # store in cache for chat
                d = load_data()
                d.setdefault("cache", {})
                d["cache"][str(chat_id)] = {"last_parlay": sgp}
                save_data(d)
                send_telegram(chat_id, human)
            return jsonify({"ok": True})
        # auto parlay
        if "parlay" in t or "auto-parlay" in t:
            sport_key = "americanfootball_nfl" if "nfl" in t else "americanfootball_ncaaf"
            parlay, err = build_auto_parlay(sport_key=sport_key, n_legs=3, stake_units=1)
            if err:
                send_telegram(chat_id, f"⚠️ {err}")
            else:
                # cache
                d = load_data()
                d.setdefault("cache", {})
                d["cache"][str(chat_id)] = {"last_parlay": parlay}
                save_data(d)
                human = "🔗 AUTO-PARLAY SUGGESTION 🔗\n"
                for i, leg in enumerate(parlay["legs"], 1):
                    human += f"{i}. {leg['team']} ({leg['market']}) {leg['line']} @ {leg['book']} — odds {leg['odds_decimal']}\n"
                human += f"\nParlay odds: {parlay['slip']['parlay_odds_decimal']}x — Possible return on 1 unit: ${parlay['slip']['possible_return']}\nUse `/betparlay <units>` to lock it in."
                send_telegram(chat_id, human)
            return jsonify({"ok": True})
        # default game card
        sport = "nfl" if "nfl" in t else "cfb" if "cfb" in t or "college" in t else "nfl"
        send_telegram(chat_id, game_card_text(sport))
        return jsonify({"ok": True})

    # bet parlay (place cached parlay)
    if t.startswith("/betparlay"):
        parts = t.split()
        units = float(parts[1]) if len(parts) > 1 else 1.0
        d = load_data()
        last = d.get("cache", {}).get(str(chat_id), {}).get("last_parlay")
        if not last:
            send_telegram(chat_id, "No cached parlay. Ask for 'parlay' first.")
            return jsonify({"ok": True})
        current_units = get_units(chat_id)
        if current_units < units:
            send_telegram(chat_id, f"You have {current_units} units but tried to bet {units}. Add units first.")
            return jsonify({"ok": True})
        add_units(chat_id, -units)
        # store bet for record
        d.setdefault("cache", {}).setdefault(str(chat_id), {}).setdefault("bets", []).append({"time": datetime.utcnow().isoformat(), "parlay": last, "units": units})
        save_data(d)
        send_telegram(chat_id, f"✅ Parlay placed for {units} units. Possible return: ${last['slip']['possible_return']}")
        return jsonify({"ok": True})

    # picks (AI)
    if "pick" in t or "play" in t or "bet" in t:
        nfl_mode = any(w in t for w in ["nfl", "pro", "week"])
        sport = "NFL" if nfl_mode else "CFB"
        # include odds snippet for context
        odds = get_cached_odds("americanfootball_nfl" if nfl_mode else "americanfootball_ncaaf", limit=6)
        snippet = json.dumps(odds, default=str)[:1200] if odds else None
        ai = call_openai_for_pick(sport=sport, odds_snippet=snippet)
        if not ai:
            send_telegram(chat_id, "⚠️ AI pick unavailable (OpenAI key or error).")
            return jsonify({"ok": True})
        # normalize
        pick_text = ai.get("pick_text") or ai.get("pick") or str(ai)
        confidence = float(ai.get("confidence", 0.6))
        dec_odds = float(ai.get("suggested_decimal_odds", 1.9))
        grade = grade_from_confidence(confidence)
        ev = estimate_ev(confidence, dec_odds)
        suggestion_units = 0.5 if grade in ("A","B") else 0.25 if grade == "C" else 0.1
        human = f"🔒 AI PICK — Grade {grade}\n{pick_text}\nConfidence: {confidence:.2f} | Odds: {dec_odds}\nEV per $1: {ev}\nSuggested stake: {suggestion_units} units"
        send_telegram(chat_id, human)
        return jsonify({"ok": True})

    # help / default
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
    )
    send_telegram(chat_id, help_text)
    return jsonify({"ok": True})

# -------------------------
# Root endpoint
# -------------------------
@app.route("/", methods=["GET"])
def home():
    return "Stealie MAX — NFL & CFB, SGP, Props, EV, Sharp — ready."

# -------------------------
# Run
# -------------------------
if __name__ == "__main__":
    logging.info("Starting Stealie MAXED bot")
    logging.info(f"Cache TTL: {CACHE_TTL}s, Rate window: {RATE_WINDOW_SEC}s")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))