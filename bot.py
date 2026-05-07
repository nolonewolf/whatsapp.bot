import os
import sqlite3
import time
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# ----------------------------
# CONFIG
# ----------------------------
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

DB_PATH = "/app/memory.db"

ADMIN_NUMBER = os.environ.get("ADMIN_NUMBER", "")  # your WhatsApp number
USER_MODE = {}
LAST_MESSAGE_TIME = {}

# ----------------------------
# DATABASE
# ----------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            role TEXT,
            message TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

# ----------------------------
# MEMORY
# ----------------------------
def save_message(user_id, role, message):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO messages (user_id, role, message) VALUES (?, ?, ?)",
        (user_id, role, message)
    )
    conn.commit()
    conn.close()

def load_memory(user_id, limit=10):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT role, message FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (user_id, limit)
    )
    rows = cursor.fetchall()
    conn.close()
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

def clear_memory(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM messages WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

# ----------------------------
# RATE LIMIT (ANTI-SPAM)
# ----------------------------
def is_spamming(user_id):
    now = time.time()
    last = LAST_MESSAGE_TIME.get(user_id, 0)

    if now - last < 3:
        return True

    LAST_MESSAGE_TIME[user_id] = now
    return False

# ----------------------------
# COMMAND SYSTEM
# ----------------------------
def handle_command(user_id, message):
    msg = message.strip().lower()

    # HELP
    if msg == "/help":
        return (
            "Commands:\n"
            "/help\n"
            "/reset\n"
            "/memory\n"
            "/mode fun\n"
            "/mode smart"
        )

    # RESET
    if msg == "/reset":
        clear_memory(user_id)
        return "Memory cleared ✔"

    # MEMORY VIEW
    if msg == "/memory":
        data = load_memory(user_id, 5)
        if not data:
            return "No memory yet."
        return "\n".join([f"{d['role']}: {d['content']}" for d in data])

    # MODE
    if msg.startswith("/mode"):
        mode = msg.replace("/mode", "").strip()
        if mode in ["fun", "smart"]:
            USER_MODE[user_id] = mode
            return f"Mode set to {mode} ✔"
        return "Use /mode fun or /mode smart"

    return None

# ----------------------------
# AI ENGINE
# ----------------------------
def get_ai_response(user_id, message):
    try:
        save_message(user_id, "user", message)

        history = load_memory(user_id)
        mode = USER_MODE.get(user_id, "smart")

        system_prompt = (
            "You are a helpful WhatsApp AI assistant."
            if mode == "smart"
            else "You are a funny WhatsApp assistant. Use emojis and jokes."
        )

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
        return "AI error. Try again later."

# ----------------------------
# WEBHOOK
# ----------------------------
@app.route("/")
def home():
    return "WhatsApp AI Bot Running 🚀"

@app.route("/bot", methods=["POST"])
def bot():
    user_id = request.values.get("From", "")
    message = request.values.get("Body", "")

    print("USER:", user_id, message)

    # spam protection
    if is_spamming(user_id):
        return str(MessagingResponse().message("Slow down ⏳"))

    # commands first
    command_reply = handle_command(user_id, message)

    if command_reply:
        reply = command_reply
    else:
        reply = get_ai_response(user_id, message)

    resp = MessagingResponse()
    resp.message(reply)
    return str(resp)

# ----------------------------
# RUN
# ----------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)