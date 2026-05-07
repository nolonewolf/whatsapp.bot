import os
import sqlite3
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from groq import Groq
from dotenv import load_dotenv

# ----------------------------
# Load local environment (ignored in Railway)
# ----------------------------
load_dotenv()

# ----------------------------
# Flask app
# ----------------------------
app = Flask(__name__)

# ----------------------------
# Groq API (secure)
# ----------------------------
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

# ----------------------------
# IMPORTANT: Stable DB path for Railway
# ----------------------------
DB_PATH = "/app/memory.db"

# ----------------------------
# INIT DATABASE
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
# AI RESPONSE FUNCTION
# ----------------------------
def get_ai_response(user_id, message):
    try:
        # Save user message
        save_message(user_id, "user", message)

        # Load memory
        history = load_memory(user_id)

        messages = [
            {
                "role": "system",
                "content": "You are a helpful WhatsApp AI assistant. Keep replies short, natural, and remember user context."
            }
        ]

        messages += history

        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=messages
        )

        reply = response.choices[0].message.content

        # Save bot reply
        save_message(user_id, "assistant", reply)

        return reply

    except Exception as e:
        print("AI ERROR:", e)
        return "⚠️ AI service error. Try again later."

# ----------------------------
# HOME ROUTE (health check)
# ----------------------------
@app.route("/")
def home():
    return "WhatsApp Bot is running 🚀"

# ----------------------------
# WHATSAPP WEBHOOK
# ----------------------------
@app.route("/bot", methods=["POST"])
def bot():
    try:
        incoming_msg = request.values.get("Body", "")
        user_id = request.values.get("From", "")

        print("USER:", user_id, incoming_msg)

        reply = get_ai_response(user_id, incoming_msg)

        print("BOT:", reply)

        resp = MessagingResponse()
        resp.message().body(reply)

        return str(resp)

    except Exception as e:
        print("WEBHOOK ERROR:", e)
        return "Error"

# ----------------------------
# RAILWAY ENTRY POINT
# ----------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)