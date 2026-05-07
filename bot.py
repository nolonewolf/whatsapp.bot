import os
import time
import psycopg2
import json
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# ----------------------------
# CONFIG
# ----------------------------
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_NUMBER = os.environ.get("ADMIN_NUMBER")

client = Groq(api_key=GROQ_API_KEY)

USER_MODE = {}
LAST_MESSAGE_TIME = {}

# ----------------------------
# DB CONNECTION
# ----------------------------
def get_conn():
    try:
        return psycopg2.connect(DATABASE_URL)
    except Exception as e:
        print("DB ERROR:", e)
        return None

# ----------------------------
# INIT DB
# ----------------------------
def init_db():
    conn = get_conn()
    if conn is None:
        return

    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id SERIAL PRIMARY KEY,
        user_id TEXT,
        role TEXT,
        message TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        user_id TEXT UNIQUE,
        message_count INT DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS facts (
        id SERIAL PRIMARY KEY,
        user_id TEXT,
        key TEXT,
        value TEXT,
        UNIQUE(user_id, key)
    )
    """)

    conn.commit()
    conn.close()

init_db()

# ----------------------------
# USER TRACKING
# ----------------------------
def track_user(user_id):
    conn = get_conn()
    if conn is None:
        return

    cur = conn.cursor()

    cur.execute("""
    INSERT INTO users (user_id, message_count)
    VALUES (%s, 1)
    ON CONFLICT (user_id)
    DO UPDATE SET message_count = users.message_count + 1
    """, (user_id,))

    conn.commit()
    conn.close()

# ----------------------------
# MEMORY
# ----------------------------
def save_message(user_id, role, message):
    conn = get_conn()
    if conn is None:
        return

    cur = conn.cursor()

    cur.execute(
        "INSERT INTO messages (user_id, role, message) VALUES (%s, %s, %s)",
        (user_id, role, message)
    )

    conn.commit()
    conn.close()

def load_memory(user_id, limit=10):
    conn = get_conn()
    if conn is None:
        return []

    cur = conn.cursor()

    cur.execute(
        "SELECT role, message FROM messages WHERE user_id=%s ORDER BY id DESC LIMIT %s",
        (user_id, limit)
    )

    rows = cur.fetchall()
    conn.close()

    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

def clear_memory(user_id):
    conn = get_conn()
    if conn is None:
        return

    cur = conn.cursor()
    cur.execute("DELETE FROM messages WHERE user_id=%s", (user_id,))
    conn.commit()
    conn.close()

# ----------------------------
# FACT MEMORY
# ----------------------------
def save_fact(user_id, key, value):
    conn = get_conn()
    if conn is None:
        return

    cur = conn.cursor()

    cur.execute("""
    INSERT INTO facts (user_id, key, value)
    VALUES (%s, %s, %s)
    ON CONFLICT (user_id, key)
    DO UPDATE SET value = EXCLUDED.value
    """, (user_id, key, value))

    conn.commit()
    conn.close()

def get_facts(user_id):
    conn = get_conn()
    if conn is None:
        return {}

    cur = conn.cursor()
    cur.execute("SELECT key, value FROM facts WHERE user_id=%s", (user_id,))
    rows = cur.fetchall()
    conn.close()

    return {k: v for k, v in rows}

# ----------------------------
# 🧠 AI MEMORY EXTRACTION
# ----------------------------
def extract_facts_ai(message):
    try:
        prompt = f"""
Extract useful long-term facts from this message.

Message:
"{message}"

Return ONLY valid JSON like:
{{"name": "...", "location": "..."}}

If nothing important, return empty JSON: {{}}
"""

        res = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}]
        )

        text = res.choices[0].message.content.strip()

        try:
            data = json.loads(text)
            return data
        except:
            print("NON-JSON MEMORY:", text)
            return {}

    except Exception as e:
        print("MEMORY AI ERROR:", e)
        return {}

# ----------------------------
# ANTI-SPAM
# ----------------------------
def is_spamming(user_id):
    now = time.time()
    last = LAST_MESSAGE_TIME.get(user_id, 0)

    if now - last < 2:
        return True

    LAST_MESSAGE_TIME[user_id] = now
    return False

# ----------------------------
# COMMANDS
# ----------------------------
def handle_command(user_id, msg):
    msg = msg.lower().strip()

    if msg == "/reset":
        clear_memory(user_id)
        return "Memory cleared ✔"

    if msg == "/facts":
        facts = get_facts(user_id)
        return str(facts) if facts else "No facts"

    return None

# ----------------------------
# AI RESPONSE
# ----------------------------
def ai_response(user_id, message):
    try:
        save_message(user_id, "user", message)

        # 🧠 Extract facts using AI
        facts = extract_facts_ai(message)

        for k, v in facts.items():
            save_fact(user_id, k, v)

        memory = load_memory(user_id)
        user_facts = get_facts(user_id)

        fact_text = "\n".join([f"{k}: {v}" for k, v in user_facts.items()])

        system_prompt = f"""
You are a smart assistant.

User facts:
{fact_text}

Use them naturally.
"""

        messages = [{"role": "system", "content": system_prompt}] + memory

        res = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=messages
        )

        reply = res.choices[0].message.content

        save_message(user_id, "assistant", reply)

        return reply

    except Exception as e:
        print("AI ERROR:", e)
        return "AI error"

# ----------------------------
# ROUTES
# ----------------------------
@app.route("/")
def home():
    return "Running 🚀"

@app.route("/bot", methods=["POST"])
def bot():
    user_id = request.values.get("From", "")
    msg = request.values.get("Body", "")

    track_user(user_id)

    if is_spamming(user_id):
        return str(MessagingResponse().message("Slow down ⏳"))

    cmd = handle_command(user_id, msg)

    if cmd:
        reply = cmd
    else:
        reply = ai_response(user_id, msg)

    resp = MessagingResponse()
    resp.message(reply)

    return str(resp)

# ----------------------------
# RUN
# ----------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)