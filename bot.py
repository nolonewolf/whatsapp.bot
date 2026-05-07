import os
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from groq import Groq
from dotenv import load_dotenv

# ----------------------------
# Load environment variables
# ----------------------------
load_dotenv()

# ----------------------------
# Flask app
# ----------------------------
app = Flask(__name__)

# ----------------------------
# Groq AI client (SAFE - uses .env)
# ----------------------------
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

# ----------------------------
# Home route (health check)
# ----------------------------
@app.route("/")
def home():
    return "WhatsApp Bot is running 🚀"

# ----------------------------
# AI function
# ----------------------------
def get_ai_response(message):
    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful WhatsApp assistant. Keep replies short, simple, and friendly."
                },
                {
                    "role": "user",
                    "content": message
                }
            ]
        )

        reply = response.choices[0].message.content

        if not reply or reply.strip() == "":
            return "⚠️ I couldn't generate a response."

        return reply

    except Exception as e:
        print("AI ERROR:", e)
        return "⚠️ AI service error. Try again later."

# ----------------------------
# WhatsApp webhook (Twilio)
# ----------------------------
@app.route("/bot", methods=["POST"])
def bot():
    incoming_msg = request.values.get("Body", "")

    print("USER MESSAGE:", incoming_msg)

    reply = get_ai_response(incoming_msg)

    print("BOT REPLY:", reply)

    # Twilio response (VERY IMPORTANT FORMAT)
    resp = MessagingResponse()
    msg = resp.message()
    msg.body(reply)

    return str(resp)

# ----------------------------
# RUN SERVER (PRODUCTION SAFE)
# ----------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)