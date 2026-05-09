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

    # Messages memory
    cur.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id SERIAL PRIMARY KEY,
        user_id TEXT,
        role TEXT,
        message TEXT
    )
    """)

    # User tracking
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        user_id TEXT UNIQUE,
        message_count INT DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Smart memory facts
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
        """
        INSERT INTO messages (user_id, role, message)
        VALUES (%s, %s, %s)
        """,
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
        """
        SELECT role, message
        FROM messages
        WHERE user_id=%s
        ORDER BY id DESC
        LIMIT %s
        """,
        (user_id, limit)
    )

    rows = cur.fetchall()

    conn.close()

    return [
        {"role": row[0], "content": row[1]}
        for row in reversed(rows)
    ]

def clear_memory(user_id):
    conn = get_conn()

    if conn is None:
        return

    cur = conn.cursor()

    cur.execute(
        "DELETE FROM messages WHERE user_id=%s",
        (user_id,)
    )

    cur.execute(
        "DELETE FROM facts WHERE user_id=%s",
        (user_id,)
    )

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

    cur.execute(
        "SELECT key, value FROM facts WHERE user_id=%s",
        (user_id,)
    )

    rows = cur.fetchall()

    conn.close()

    return {k: v for k, v in rows}

# ----------------------------
# SMART FACT EXTRACTION
# ----------------------------
def extract_facts_ai(message):
    try:
        prompt = f"""
Extract ONLY useful long-term user facts.

Possible facts:
- name
- age
- country
- hobbies
- favorite things
- goals
- job
- school

Message:
"{message}"

RULES:
- Return ONLY valid JSON
- No markdown
- No explanation
- No extra text
- If nothing important exists return {{}}

Example:
{{"name":"John","country":"Nigeria"}}
"""

        res = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {
                    "role": "system",
                    "content": "You only return raw JSON."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0
        )

        text = res.choices[0].message.content.strip()

        # Remove markdown if AI adds it
        text = text.replace("```json", "")
        text = text.replace("```", "")
        text = text.strip()

        try:
            data = json.loads(text)

            if isinstance(data, dict):
                return data

            return {}

        except json.JSONDecodeError:
            return {}

    except Exception as e:
        print("MEMORY AI ERROR:", e)
        return {}

# ----------------------------
# ANTI SPAM
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

    # Reset memory
    if msg == "/reset":
        clear_memory(user_id)
        return "Memory cleared ✔"

    # Show facts
    if msg == "/facts":
        facts = get_facts(user_id)

        if not facts:
            return "No saved facts"

        return "\n".join(
            [f"{k}: {v}" for k, v in facts.items()]
        )

    # Help menu
    if msg == "/help":
        return (
            "🤖 Commands:\n\n"
            "/help - Show commands\n"
            "/facts - Show saved memory\n"
            "/reset - Clear memory"
        )

    return None

# ----------------------------
# AI RESPONSE
# ----------------------------
def ai_response(user_id, message):
    try:
        # Save user message
        save_message(user_id, "user", message)

        # Extract smart memory
        facts = extract_facts_ai(message)

        for k, v in facts.items():
            save_fact(user_id, k, str(v))

        # Load conversation memory
        memory = load_memory(user_id)

        # Load facts
        user_facts = get_facts(user_id)

        fact_text = "\n".join(
            [f"{k}: {v}" for k, v in user_facts.items()]
        )

        system_prompt = f"""
You are a smart helpful AI assistant.

User facts:
{fact_text}

Rules:
- Be natural
- Be conversational
- Use user facts naturally
- Keep responses clear
- Avoid repeating yourself
"""

        messages = [
            {
                "role": "system",
                "content": system_prompt
            }
        ] + memory

        res = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=messages,
            temperature=0.7,
            max_tokens=500
        )

        reply = res.choices[0].message.content.strip()

        # Save assistant reply
        save_message(user_id, "assistant", reply)

        return reply

    except Exception as e:
        print("AI ERROR:", e)
        return "⚠ AI error occurred."

# ----------------------------
# ROUTES
# ----------------------------
@app.route("/")
def home():
    return "Bot Running 🚀"

@app.route("/bot", methods=["POST"])
def bot():
    try:
        user_id = request.values.get("From", "")
        msg = request.values.get("Body", "").strip()

        if not msg:
            return "No message"

        # Track user
        track_user(user_id)

        # Anti spam
        if is_spamming(user_id):
            resp = MessagingResponse()
            resp.message("⏳ Slow down a little.")
            return str(resp)

        # Commands
        cmd = handle_command(user_id, msg)

        if cmd:
            reply = cmd
        else:
            reply = ai_response(user_id, msg)

        # Twilio response
        resp = MessagingResponse()
        resp.message(reply)

        return str(resp)

    except Exception as e:
        print("BOT ERROR:", e)

        resp = MessagingResponse()
        resp.message("⚠ Server error.")

        return str(resp)

# ----------------------------
# RUN
# ----------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=port
    )