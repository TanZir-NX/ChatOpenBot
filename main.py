import os
import threading
import json
import time
from datetime import datetime
from flask import Flask
import telebot
from telebot import types
import openai

# --- Environment Variables ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
# Split comma-separated admin IDs into a list of integers
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()]
OR_API = os.environ.get("OR_API")

# --- Global Variables ---
MODEL = "deepseek/deepseek-v4-flash:free"
MAINTENANCE = False
START_TIME = datetime.now()
ERRORS = []

# --- Persistence Files ---
CONFIG_FILE = "config.json"
USERS_FILE = "users.json"
HISTORIES_FILE = "histories.json"
BANNED_FILE = "banned.json"

# --- Flask App for Render Health Check ---
app = Flask(__name__)

@app.route("/")
def health_check():
    return "ChatOpen Bot is alive and polling!", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# --- Global Data Stores ---
USERS = []
HISTORIES = {}
BANNED_USERS = []

# --- Data Management Functions ---
def load_data():
    global OR_API, MODEL, MAINTENANCE, USERS, HISTORIES, BANNED_USERS
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            cfg = json.load(f)
            OR_API = cfg.get("api_key", OR_API)
            MODEL = cfg.get("model", MODEL)
            MAINTENANCE = cfg.get("maintenance", False)
            
    USERS = json.load(open(USERS_FILE, "r")) if os.path.exists(USERS_FILE) else []
    HISTORIES = json.load(open(HISTORIES_FILE, "r")) if os.path.exists(HISTORIES_FILE) else {}
    BANNED_USERS = json.load(open(BANNED_FILE, "r")) if os.path.exists(BANNED_FILE) else []

def save_config():
    with open(CONFIG_FILE, "w") as f:
        json.dump({"api_key": OR_API, "model": MODEL, "maintenance": MAINTENANCE}, f)

def save_users():
    with open(USERS_FILE, "w") as f:
        json.dump(USERS, f)

def save_histories():
    with open(HISTORIES_FILE, "w") as f:
        json.dump(HISTORIES, f)

def save_banned():
    with open(BANNED_FILE, "w") as f:
        json.dump(BANNED_USERS, f)

def log_error(err):
    global ERRORS
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ERRORS.append(f"[{timestamp}] {str(err)}")
    if len(ERRORS) > 10:
        ERRORS.pop(0)

load_data()
bot = telebot.TeleBot(BOT_TOKEN)

# --- Helper Functions ---
def is_admin(user_id):
    return user_id in ADMIN_IDS

def call_openrouter(messages):
    global OR_API, MODEL
    try:
        client = openai.OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=OR_API,
        )
        
        completion = client.chat.completions.create(
            extra_headers={
                "HTTP-Referer": "https://telegram.org",
                "X-Title": "ChatOpen Bot",
            },
            model=MODEL,
            messages=messages
        )
        return completion.choices[0].message.content
    except Exception as e:
        err_str = str(e)
        log_error(err_str)
        
        # Feature 15: Auto-notify Admin when API key fails (Authentication/401)
        if "401" in err_str or "Incorrect API key" in err_str or "authentication" in err_str.lower():
            for admin in ADMIN_IDS:
                try:
                    bot.send_message(admin, f"🚨 API KEY FAILED!\nError: {err_str}\nPlease use /setapikey to update the key.")
                except:
                    pass
        return f"⚠️ Error: {err_str}"

# --- Bot Handlers ---
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    uid = str(message.from_user.id)
    if uid not in USERS:
        USERS.append(uid)
        save_users()
    
    if MAINTENANCE and not is_admin(message.from_user.id):
        bot.reply_to(message, "🛠️ Bot is currently under maintenance. Please try again later.")
        return

    bot.reply_to(message, "👋 Welcome to ChatOpen Bot! Just send me a message to start chatting.")

# --- Admin Commands (Features 1-14) ---
@bot.message_handler(commands=['setapikey']) # Feature 1
def admin_setapikey(message):
    if not is_admin(message.from_user.id): return
    try:
        new_key = message.text.split(" ", 1)[1]
        global OR_API
        OR_API = new_key
        save_config()
        bot.reply_to(message, "✅ API Key updated successfully!")
    except:
        bot.reply_to(message, "Usage: /setapikey <new_key>")

@bot.message_handler(commands=['setmodel']) # Feature 2
def admin_setmodel(message):
    if not is_admin(message.from_user.id): return
    try:
        new_model = message.text.split(" ", 1)[1]
        global MODEL
        MODEL = new_model
        save_config()
        bot.reply_to(message, f"✅ Model updated to {new_model}!")
    except:
        bot.reply_to(message, "Usage: /setmodel <new_model_name>")

@bot.message_handler(commands=['config']) # Feature 4
def admin_config(message):
    if not is_admin(message.from_user.id): return
    masked_key = OR_API[:5] + "..." + OR_API[-5:] if OR_API and len(OR_API) > 10 else "Not set"
    bot.reply_to(message, f"⚙️ Current Configuration:\nAPI Key: {masked_key}\nModel: {MODEL}")

@bot.message_handler(commands=['restart']) # Feature 5
def admin_restart(message):
    if not is_admin(message.from_user.id): return
    load_data()
    bot.reply_to(message, "🔄 Configuration reloaded!")

@bot.message_handler(commands=['broadcast']) # Feature 6
def admin_broadcast(message):
    if not is_admin(message.from_user.id): return
    try:
        text = message.text.split(" ", 1)[1]
        count = 0
        for uid in USERS:
            try:
                bot.send_message(int(uid), f"📢 Broadcast:\n{text}")
                count += 1
            except:
                pass
        bot.reply_to(message, f"✅ Broadcast sent to {count} users.")
    except:
        bot.reply_to(message, "Usage: /broadcast <message>")

@bot.message_handler(commands=['stats']) # Feature 7
def admin_stats(message):
    if not is_admin(message.from_user.id): return
    bot.reply_to(message, f"📊 Bot Statistics:\nTotal Users: {len(USERS)}\nActive Chat Histories: {len(HISTORIES)}\nBanned Users: {len(BANNED_USERS)}")

@bot.message_handler(commands=['ban']) # Feature 8
def admin_ban(message):
    if not is_admin(message.from_user.id): return
    try:
        uid = message.text.split(" ", 1)[1]
        if uid not in BANNED_USERS:
            BANNED_USERS.append(uid)
            save_banned()
        bot.reply_to(message, f"🔨 User {uid} banned.")
    except:
        bot.reply_to(message, "Usage: /ban <user_id>")

@bot.message_handler(commands=['unban']) # Feature 9
def admin_unban(message):
    if not is_admin(message.from_user.id): return
    try:
        uid = message.text.split(" ", 1)[1]
        if uid in BANNED_USERS:
            BANNED_USERS.remove(uid)
            save_banned()
        bot.reply_to(message, f"✅ User {uid} unbanned.")
    except:
        bot.reply_to(message, "Usage: /unban <user_id>")

@bot.message_handler(commands=['resetuser']) # Feature 10
def admin_resetuser(message):
    if not is_admin(message.from_user.id): return
    try:
        uid = message.text.split(" ", 1)[1]
        if uid in HISTORIES:
            del HISTORIES[uid]
            save_histories()
        bot.reply_to(message, f"🧹 History for user {uid} cleared.")
    except:
        bot.reply_to(message, "Usage: /resetuser <user_id>")

@bot.message_handler(commands=['resetall']) # Feature 11
def admin_resetall(message):
    if not is_admin(message.from_user.id): return
    global HISTORIES
    HISTORIES = {}
    save_histories()
    bot.reply_to(message, "🧹 All user histories cleared.")

@bot.message_handler(commands=['maintenance']) # Feature 12
def admin_maintenance(message):
    if not is_admin(message.from_user.id): return
    try:
        status = message.text.split(" ", 1)[1].lower()
        global MAINTENANCE
        if status in ["on", "true"]:
            MAINTENANCE = True
            save_config()
            bot.reply_to(message, "🛠️ Maintenance mode ON.")
        elif status in ["off", "false"]:
            MAINTENANCE = False
            save_config()
            bot.reply_to(message, "✅ Maintenance mode OFF.")
        else:
            bot.reply_to(message, "Usage: /maintenance <on|off>")
    except:
        bot.reply_to(message, "Usage: /maintenance <on|off>")

@bot.message_handler(commands=['errors']) # Feature 13
def admin_errors(message):
    if not is_admin(message.from_user.id): return
    if not ERRORS:
        bot.reply_to(message, "✅ No recent errors.")
    else:
        err_text = "\n".join(ERRORS[-5:])
        bot.reply_to(message, f"🚨 Recent Errors:\n{err_text}")

@bot.message_handler(commands=['msg']) # Feature 14
def admin_msg(message):
    if not is_admin(message.from_user.id): return
    try:
        parts = message.text.split(" ", 2)
        uid = parts[1]
        text = parts[2]
        bot.send_message(int(uid), f"✉️ Message from Admin:\n{text}")
        bot.reply_to(message, "✅ Message sent.")
    except Exception as e:
        bot.reply_to(message, f"Error: {e}\nUsage: /msg <user_id> <text>")

@bot.message_handler(commands=['uptime']) # Feature 15
def admin_uptime(message):
    if not is_admin(message.from_user.id): return
    delta = datetime.now() - START_TIME
    bot.reply_to(message, f"⏱️ Uptime: {delta}")

@bot.message_handler(commands=['help_admin'])
def admin_help(message):
    if not is_admin(message.from_user.id): return
    help_text = """
🛡️ Admin Commands (15 Features):
1. /setapikey - Change OpenRouter API key
2. /setmodel - Change AI model
3. Auto-notify Admin on API failure
4. /config - View current config
5. /restart - Reload bot config
6. /broadcast - Message all users
7. /stats - View bot statistics
8. /ban - Ban a user
9. /unban - Unban a user
10. /resetuser - Clear user history
11. /resetall - Clear all histories
12. /maintenance - Toggle maintenance
13. /errors - View recent errors
14. /msg - DM a specific user
15. /uptime - Check bot uptime
"""
    bot.reply_to(message, help_text)

# --- General Message Handler ---
@bot.message_handler(func=lambda message: True, content_types=['text'])
def handle_message(message):
    uid = str(message.from_user.id)
    
    if uid in BANNED_USERS:
        bot.reply_to(message, "🚫 You are banned from using this bot.")
        return

    if MAINTENANCE and not is_admin(message.from_user.id):
        bot.reply_to(message, "🛠️ Bot is currently under maintenance.")
        return

    if uid not in USERS:
        USERS.append(uid)
        save_users()

    if uid not in HISTORIES:
        HISTORIES[uid] = []
        
    # Limit history to avoid exceeding token limits
    HISTORIES[uid].append({"role": "user", "content": message.text})
    if len(HISTORIES[uid]) > 10:
        HISTORIES[uid] = HISTORIES[uid][-10:]
        
    wait_msg = bot.send_message(message.chat.id, "⏳ Thinking...", reply_to_message_id=message.message_id)
    
    response = call_openrouter(HISTORIES[uid])
    
    HISTORIES[uid].append({"role": "assistant", "content": response})
    save_histories()
    
    try:
        bot.delete_message(message.chat.id, wait_msg.message_id)
    except:
        pass
        
    # Handle Telegram's 4096 character limit per message
    for i in range(0, len(response), 4096):
        bot.send_message(message.chat.id, response[i:i+4096])

if __name__ == "__main__":
    print("Starting Flask server...")
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    print("Starting Telegram Bot polling...")
    while True:
        try:
            bot.infinity_polling(timeout=10, long_polling_timeout=5)
        except Exception as e:
            log_error(e)
            time.sleep(15)
