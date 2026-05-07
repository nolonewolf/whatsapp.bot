import os
import time
import psycopg2
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
        print("Skipping DB init")
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
# FACT MEMORY (SMART MEMORY)
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

def extract_fact(message):
    message = message.lower()

    if "my name is" in message:
        return ("name", message.split("my name is")[-1].strip())

    if "i live in" in message:
        return ("location", message.split("i live in")[-1].strip())

    if "i like" in message:
        return ("likes", message.split("i like")[-1].strip())

    return None

# ----------------------------
# ANTI-SPAM
# ----------------------------
def is_spamming(user_id):
    now = time.time()
    last = LAST_MESSAGE_TIME.get(user_id, 0)

    if now - last < 3:
        return True

    LAST_MESSAGE_TIME[user_id] = now
    return False

# ----------------------------
# COMMANDS
# ----------------------------
def handle_command(user_id, msg):
    msg = msg.strip().lower()

    if msg == "/help":
        return "/help /reset /memory /facts /mode fun /mode smart"

    if msg == "/reset":
        clear_memory(user_id)
        return "Memory cleared ✔"

    if msg == "/memory":
        data = load_memory(user_id, 5)
        if not data:
            return "No memory"
        return "\n".join([f"{d['role']}: {d['content']}" for d in data])

    if msg == "/facts":
        facts = get_facts(user_id)
        if not facts:
            return "No facts saved"
        return "\n".join([f"{k}: {v}" for k, v in facts.items()])

    if msg.startswith("/mode"):
        mode = msg.replace("/mode", "").strip()
        if mode in ["fun", "smart"]:
            USER_MODE[user_id] = mode
            return f"Mode set to {mode}"
        return "Use /mode fun or /mode smart"

    return None

# ----------------------------
# ADMIN SYSTEM
# ----------------------------
def is_admin(user_id):
    return user_id == ADMIN_NUMBER

def admin_commands(user_id, msg):
    if not is_admin(user_id):
        return None

    conn = get_conn()
    if conn is None:
        return "DB error"

    cur = conn.cursor()

    if msg == "/stats":
        cur.execute("SELECT COUNT(*) FROM messages")
        messages = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM users")
        users = cur.fetchone()[0]

        conn.close()
        return f"Users: {users}\nMessages: {messages}"

    if msg == "/users":
        cur.execute("SELECT user_id FROM users LIMIT 10")
        data = cur.fetchall()
        conn.close()

        return "\n".join([u[0] for u in data]) or "No users"

    if msg == "/top":
        cur.execute("""
        SELECT user_id, message_count 
        FROM users 
        ORDER BY message_count DESC 
        LIMIT 5
        """)
        data = cur.fetchall()
        conn.close()

        return "\n".join([f"{u[0]}: {u[1]}" for u in data]) or "No data"

    return None

# ----------------------------
# AI RESPONSE
# ----------------------------
def ai_response(user_id, message):
    try:
        save_message(user_id, "user", message)

        # Save facts if detected
        fact = extract_fact(message)
        if fact:
            save_fact(user_id, fact[0], fact[1])

        history = load_memory(user_id)
        facts = get_facts(user_id)

        fact_text = "\n".join([f"{k}: {v}" for k, v in facts.items()])

        mode = USER_MODE.get(user_id, "smart")

        system_prompt = f"""
You are a smart assistant.

User facts:
{fact_text}

Use them naturally in conversation.
"""

        if mode == "fun":
            system_prompt += "\nBe funny and use emojis."

        messages = [{"role": "system", "content": system_prompt}]
        messages += history

        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=messages
        )

        reply = response.choices[0].message.content

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
    return "Bot running 🚀"

@app.route("/bot", methods=["POST"])
def bot():
    user_id = request.values.get("From", "")
    msg = request.values.get("Body", "")

    track_user(user_id)

    if is_spamming(user_id):
        return str(MessagingResponse().message("Slow down ⏳"))

    admin_reply = admin_commands(user_id, msg)

    if admin_reply:
        reply = admin_reply
    else:
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