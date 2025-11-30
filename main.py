# main.py — STEALIE MAX: FULL DEGENERATE EDITION (FIXED LINE 53)

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

# Bankroll tracking
bankroll = {"total": 100.0, "history": []}

# TODAY/TONIGHT
def now_et():
    return datetime.utcnow() + timedelta(hours=-5)

def when():
    n = now_et()
    if n.weekday() == 6 and n.hour >= 20:  # Sunday after 8 PM ET
        return (n + timedelta(days=7)).strftime("%A %B %d"), "next Sunday"
    return n.strftime("%A %B %d"), "today/tonight"

DATE_STR, WHEN_TEXT = when()

# ODDS
def odds():
    try:
        r = requests.get("https://api.the-odds-api.com