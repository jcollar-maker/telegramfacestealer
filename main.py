# main.py — STEALIE MAX: FULL DEGENERATE BELLS & WHISTLES EDITION

import os, logging, random, re, time, requests, json
from datetime import datetime, timedelta
from flask import Flask, request, jsonify

# OpenAI
try:
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_KEY"))
except:
    client = None

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("TELEGRAM_TOKEN")
ODDS_KEY = os.getenv("ODDS_API_KEY")

# Bankroll tracking (persists in memory)
bankroll = {"total": 100.0, "history": []}

# TODAY/TONIGHT
def now_et():
    return datetime.utcnow() + timedelta(hours=-5)

def when():
    n = now_et()
    if n.weekday() == 6 and n.hour >= 20:
        return (n + timedelta(days=7)).strftime("%A %B %d"), "next Sunday"
    return n.strftime("%A %B %d"), "today/tonight"

DATE_STR, WHEN_TEXT = when()

# ODDS
def odds():
    try:
        r = requests.get("https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds",
                        params={"apiKey": ODDS_KEY, "regions": "us", "markets": "h2h,spreads,totals", "oddsFormat": "decimal"},
                        timeout=12)
        return r.json() if r.status_code == 200 else []
    except:
        return []

# CARD
def card():
    games = odds()
    if not games: return "⚠️ Odds down — retry in 60s"
    lines = [f"🏈 NFL WEEK 13 — {WHEN_TEXT.upper()} {DATE_STR.upper()} 💀⚡\n"]
    for g in games:
        try:
            t = datetime.fromisoformat(g["commence_time"].replace("Z", "+00:00"))
            et_time = t.astimezone(now