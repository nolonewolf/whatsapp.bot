import os
import sqlite3
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from groq import Groq
from dotenv import load_dotenv

# ----------------------------
# ENV
# ----------------------------
load_dotenv()

# ----------------------------
# APP
# ----------------------------
app = Flask(__name__)

# ----------------------------
# AI CLIENT
# ----------------------------
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

# ----------------------------
# MEMORY DB
# ----------------------------
DB_PATH = "/app/memory.db"

USER_MODE = {}

# ----------------------------
# INIT DB
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
# SAVE MESSAGE
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

# ----------------------------
# LOAD MEMORY
# ----------------------------
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

# ----------------------------
# COMMANDS
# ----------------------------
def handle_command(user_id, message):
    msg = message.strip().lower()

    if msg == "/help":
        return (
            "Commands:\n"
            "/help - show commands\n"
            "/reset - clear memory\n"
            "/memory - show last chats\n"
            "/mode fun - fun AI\n"
            "/mode smart - normal AI"
        )

    if msg == "/reset":
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM messages WHERE user_id=?", (user_id,))
        conn.commit()
        conn.close()
        return "Memory cleared ✔"

    if msg == "/memory":
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute(
            "SELECT role, message FROM messages WHERE user_id=? ORDER BY id DESC LIMIT 5",
            (user_id,)
        )

        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return "No memory found."

        return "\n".join([f"{r[0]}: {r[1]}" for r in reversed(rows)])

    if msg.startswith("/mode"):
        mode = msg.replace("/mode", "").strip()

        if mode in ["fun", "smart"]:
            USER_MODE[user_id] = mode
            return f"Mode set to {mode} ✔"

        return "Use /mode fun or /mode smart"

    return None

# ----------------------------
# AI RESPONSE
# ----------------------------
def get_ai_response(user_id, message):
    try:
        save_message(user_id, "user", message)

        history = load_memory(user_id)
        mode = USER_MODE.get(user_id, "smart")

        if mode == "fun":
            system_prompt = "You are a funny WhatsApp assistant. Use emojis and jokes."
        else:
            system_prompt = "You are a helpful WhatsApp assistant. Be clear and short."

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
        return "AI error. Try again."

# ----------------------------
# ROUTES
# ----------------------------
@app.route("/")
def home():
    return "Bot running 🚀"

@app.route("/bot", methods=["POST"])
def bot():
    incoming_msg = request.values.get("Body", "")
    user_id = request.values.get("From", "")

    print("USER:", user_id, incoming_msg)

    command_reply = handle_command(user_id, incoming_msg)

    if command_reply:
        reply = command_reply
    else:
        reply = get_ai_response(user_id, incoming_msg)

    resp = MessagingResponse()
    resp.message().body(reply)

    return str(resp)

# ----------------------------
# RUN
# ----------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)