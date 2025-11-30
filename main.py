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
        "/betparlay - Build a parlay\n"
        "/units - Check your units\n"
        "/addunits <number> - Adjust units\n"
        "/question <your query> - Ask me anything\n\n"
        "Type any of the commands to get started!"
    )
    send_telegram(chat_id, greeting)
    return jsonify({"ok": True})