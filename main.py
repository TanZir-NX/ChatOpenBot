import os
import logging
import threading
from flask import Flask, request
import telebot
from telebot import types
from openai import OpenAI

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# ── Environment Variables ─────────────────────────────────────────────────────
BOT_TOKEN  = os.environ.get("BOT_TOKEN", "")
ADMIN_IDS  = [int(x.strip()) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()]
UNI_API    = os.environ.get("UNI_API", "")          # Universal API key (OpenRouter, HuggingFace, Groq, NVIDIA …)
API_BASE   = os.environ.get("API_BASE", "https://openrouter.ai/api/v1")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set!")

# ── Runtime State (in-memory; persistent across restarts only if you add a DB) ─
state = {
    "api_key":    UNI_API,
    "api_base":   API_BASE,
    "model":      "deepseek/deepseek-r1-0528:free",
    "system_msg": "You are ChatOpen, a helpful and friendly AI assistant.",
    "max_tokens": 1024,
    "temperature": 0.7,
    "allowed_users": set(),  # empty = everyone allowed
    "maintenance": False,
    "stats": {"total_messages": 0, "total_users": set()},
}

# Per-user conversation histories  {user_id: [ {role, content}, … ]}
conversations: dict[int, list] = {}

# ── Helpers ───────────────────────────────────────────────────────────────────
bot    = telebot.TeleBot(BOT_TOKEN, parse_mode="Markdown")
app    = Flask(__name__)

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def get_client() -> OpenAI:
    return OpenAI(api_key=state["api_key"], base_url=state["api_base"])

def notify_admins(text: str):
    for aid in ADMIN_IDS:
        try:
            bot.send_message(aid, f"⚠️ *Admin Alert*\n\n{text}", parse_mode="Markdown")
        except Exception as e:
            logger.error("Could not notify admin %s: %s", aid, e)

def chat_with_ai(user_id: int, user_text: str) -> str:
    if user_id not in conversations:
        conversations[user_id] = []

    conversations[user_id].append({"role": "user", "content": user_text})

    messages = [{"role": "system", "content": state["system_msg"]}] + conversations[user_id]

    client = get_client()
    try:
        resp = client.chat.completions.create(
            model=state["model"],
            messages=messages,
            max_tokens=state["max_tokens"],
            temperature=state["temperature"],
        )
        reply = resp.choices[0].message.content.strip()
        conversations[user_id].append({"role": "assistant", "content": reply})
        # Keep last 20 turns to avoid runaway context
        if len(conversations[user_id]) > 40:
            conversations[user_id] = conversations[user_id][-40:]
        return reply
    except Exception as e:
        err_msg = str(e)
        logger.error("AI API error: %s", err_msg)
        notify_admins(
            f"🔴 API call failed!\n"
            f"Model: `{state['model']}`\n"
            f"Error: `{err_msg}`\n\n"
            f"Use /setapikey and /setapibase to update credentials."
        )
        return "❌ I'm having trouble connecting to the AI service right now. Please try again shortly."

# ── Admin command state machine ───────────────────────────────────────────────
# Tracks what input the bot is waiting for from an admin
waiting_for: dict[int, str] = {}

# ── User Commands ─────────────────────────────────────────────────────────────

@bot.message_handler(commands=["start"])
def cmd_start(msg: types.Message):
    uid  = msg.from_user.id
    name = msg.from_user.first_name or "there"
    state["stats"]["total_users"].add(uid)
    bot.send_message(
        uid,
        f"👋 Hello *{name}*! I'm *ChatOpen* — your AI assistant.\n\n"
        f"Just send me a message and I'll reply using `{state['model']}`.\n\n"
        f"📌 Commands:\n"
        f"/start — Show this message\n"
        f"/reset — Clear your conversation history\n"
        f"/model — Show current model\n"
        f"/help — Help & info",
    )

@bot.message_handler(commands=["help"])
def cmd_help(msg: types.Message):
    bot.send_message(
        msg.chat.id,
        "🤖 *ChatOpen Bot Help*\n\n"
        "Send any text message and I'll respond using AI.\n\n"
        "Commands:\n"
        "/start — Welcome message\n"
        "/reset — Clear your conversation history\n"
        "/model — See active AI model\n"
        "/help  — This message\n\n"
        "_Powered by OpenRouter / Universal API_",
    )

@bot.message_handler(commands=["reset"])
def cmd_reset(msg: types.Message):
    conversations.pop(msg.from_user.id, None)
    bot.send_message(msg.chat.id, "🗑️ Your conversation history has been cleared. Fresh start!")

@bot.message_handler(commands=["model"])
def cmd_model(msg: types.Message):
    bot.send_message(msg.chat.id, f"🧠 Current model: `{state['model']}`")

# ── Admin Commands ────────────────────────────────────────────────────────────

def admin_only(func):
    """Decorator: only execute for admins."""
    def wrapper(msg: types.Message):
        if not is_admin(msg.from_user.id):
            bot.send_message(msg.chat.id, "🚫 You are not authorised to use this command.")
            return
        return func(msg)
    return wrapper

# 1. Set API Key
@bot.message_handler(commands=["setapikey"])
@admin_only
def cmd_setapikey(msg: types.Message):
    waiting_for[msg.from_user.id] = "api_key"
    bot.send_message(msg.chat.id, "🔑 Send me the new API key:")

# 2. Set API Base URL
@bot.message_handler(commands=["setapibase"])
@admin_only
def cmd_setapibase(msg: types.Message):
    waiting_for[msg.from_user.id] = "api_base"
    bot.send_message(msg.chat.id, "🌐 Send me the new API base URL (e.g. `https://openrouter.ai/api/v1`):")

# 3. Change model
@bot.message_handler(commands=["setmodel"])
@admin_only
def cmd_setmodel(msg: types.Message):
    waiting_for[msg.from_user.id] = "model"
    bot.send_message(
        msg.chat.id,
        f"🧠 Current model: `{state['model']}`\n\nSend me the new model name:"
    )

# 4. Set system prompt
@bot.message_handler(commands=["setsystem"])
@admin_only
def cmd_setsystem(msg: types.Message):
    waiting_for[msg.from_user.id] = "system_msg"
    bot.send_message(
        msg.chat.id,
        f"📝 Current system prompt:\n`{state['system_msg']}`\n\nSend the new system prompt:"
    )

# 5. Bot statistics
@bot.message_handler(commands=["stats"])
@admin_only
def cmd_stats(msg: types.Message):
    total_convs = sum(len(v) // 2 for v in conversations.values())
    bot.send_message(
        msg.chat.id,
        f"📊 *Bot Statistics*\n\n"
        f"👥 Unique users this session: `{len(state['stats']['total_users'])}`\n"
        f"💬 Active conversations: `{len(conversations)}`\n"
        f"🔁 Total turns this session: `{total_convs}`\n"
        f"🧠 Model: `{state['model']}`\n"
        f"🌐 API Base: `{state['api_base']}`\n"
        f"🔧 Maintenance mode: `{'ON' if state['maintenance'] else 'OFF'}`",
    )

# 6. Broadcast message to all known users
@bot.message_handler(commands=["broadcast"])
@admin_only
def cmd_broadcast(msg: types.Message):
    waiting_for[msg.from_user.id] = "broadcast"
    bot.send_message(msg.chat.id, "📢 Send the message to broadcast to all users:")

# 7. Toggle maintenance mode
@bot.message_handler(commands=["maintenance"])
@admin_only
def cmd_maintenance(msg: types.Message):
    state["maintenance"] = not state["maintenance"]
    status = "🔴 ON" if state["maintenance"] else "🟢 OFF"
    bot.send_message(msg.chat.id, f"🛠️ Maintenance mode is now *{status}*")

# 8. Clear all conversations
@bot.message_handler(commands=["clearall"])
@admin_only
def cmd_clearall(msg: types.Message):
    conversations.clear()
    bot.send_message(msg.chat.id, "🗑️ All conversation histories have been cleared.")

# 9. Set max tokens
@bot.message_handler(commands=["setmaxtokens"])
@admin_only
def cmd_setmaxtokens(msg: types.Message):
    waiting_for[msg.from_user.id] = "max_tokens"
    bot.send_message(msg.chat.id, f"🔢 Current max_tokens: `{state['max_tokens']}`\nSend new value (e.g. `2048`):")

# 10. Set temperature
@bot.message_handler(commands=["settemp"])
@admin_only
def cmd_settemp(msg: types.Message):
    waiting_for[msg.from_user.id] = "temperature"
    bot.send_message(msg.chat.id, f"🌡️ Current temperature: `{state['temperature']}`\nSend new value (0.0 – 2.0):")

# Admin panel overview
@bot.message_handler(commands=["admin"])
@admin_only
def cmd_admin(msg: types.Message):
    bot.send_message(
        msg.chat.id,
        "🛠️ *Admin Panel — ChatOpen Bot*\n\n"
        "1️⃣  /setapikey      — Change API key\n"
        "2️⃣  /setapibase     — Change API base URL\n"
        "3️⃣  /setmodel       — Change AI model\n"
        "4️⃣  /setsystem      — Change system prompt\n"
        "5️⃣  /stats          — View bot statistics\n"
        "6️⃣  /broadcast      — Broadcast message to all users\n"
        "7️⃣  /maintenance    — Toggle maintenance mode\n"
        "8️⃣  /clearall       — Clear all conversation histories\n"
        "9️⃣  /setmaxtokens   — Set max response tokens\n"
        "🔟  /settemp        — Set AI temperature\n",
    )

# ── General Message Handler ───────────────────────────────────────────────────

@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_text(msg: types.Message):
    uid  = msg.from_user.id
    text = msg.text.strip()

    state["stats"]["total_users"].add(uid)
    state["stats"]["total_messages"] = state["stats"].get("total_messages", 0) + 1

    # ── Handle pending admin input ──
    if uid in waiting_for:
        field = waiting_for.pop(uid)

        if field == "api_key":
            state["api_key"] = text
            bot.send_message(uid, "✅ API key updated successfully.")

        elif field == "api_base":
            state["api_base"] = text
            bot.send_message(uid, f"✅ API base URL updated to:\n`{text}`")

        elif field == "model":
            state["model"] = text
            bot.send_message(uid, f"✅ Model changed to `{text}`")

        elif field == "system_msg":
            state["system_msg"] = text
            bot.send_message(uid, "✅ System prompt updated.")

        elif field == "max_tokens":
            try:
                state["max_tokens"] = int(text)
                bot.send_message(uid, f"✅ max_tokens set to `{state['max_tokens']}`")
            except ValueError:
                bot.send_message(uid, "❌ Invalid number. Please send an integer.")

        elif field == "temperature":
            try:
                val = float(text)
                if 0.0 <= val <= 2.0:
                    state["temperature"] = val
                    bot.send_message(uid, f"✅ Temperature set to `{val}`")
                else:
                    bot.send_message(uid, "❌ Temperature must be between 0.0 and 2.0")
            except ValueError:
                bot.send_message(uid, "❌ Invalid number.")

        elif field == "broadcast":
            sent = 0
            for target_uid in state["stats"]["total_users"]:
                try:
                    bot.send_message(target_uid, f"📢 *Announcement*\n\n{text}")
                    sent += 1
                except Exception:
                    pass
            bot.send_message(uid, f"✅ Broadcast sent to {sent} user(s).")
        return

    # ── Maintenance mode check ──
    if state["maintenance"] and not is_admin(uid):
        bot.send_message(uid, "🛠️ The bot is currently under maintenance. Please try again later.")
        return

    # ── Normal AI chat ──
    bot.send_chat_action(msg.chat.id, "typing")
    reply = chat_with_ai(uid, text)
    bot.send_message(msg.chat.id, reply)

# ── Flask Webhook ─────────────────────────────────────────────────────────────

WEBHOOK_PATH = f"/{BOT_TOKEN}"

@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    if request.headers.get("content-type") == "application/json":
        json_str = request.get_data(as_text=True)
        update  = telebot.types.Update.de_json(json_str)
        bot.process_new_updates([update])
    return "OK", 200

@app.route("/", methods=["GET"])
def health():
    return "ChatOpen Bot is running 🚀", 200

# ── Entry Point ───────────────────────────────────────────────────────────────

def set_webhook():
    render_url = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
    if render_url:
        webhook_url = render_url + WEBHOOK_PATH
        bot.remove_webhook()
        bot.set_webhook(url=webhook_url)
        logger.info("Webhook set to %s", webhook_url)
    else:
        logger.warning("RENDER_EXTERNAL_URL not set — running in polling mode")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))

    render_url = os.environ.get("RENDER_EXTERNAL_URL", "")
    if render_url:
        # Webhook mode (Render)
        threading.Thread(target=set_webhook, daemon=True).start()
        app.run(host="0.0.0.0", port=port)
    else:
        # Local polling mode
        logger.info("Starting in polling mode …")
        bot.remove_webhook()
        bot.infinity_polling(logger_level=logging.INFO)
