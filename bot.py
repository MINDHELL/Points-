import os
import logging
import asyncio
import threading
import time
import re
import datetime
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, BotCommand
from pymongo import MongoClient
from pyrogram.errors import InputUserDeactivated, UserNotParticipant, FloodWait, UserIsBlocked, PeerIdInvalid
from health_check import start_health_check

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_ID = int(os.getenv("API_ID", "27788368"))
API_HASH = os.getenv("API_HASH", "9df7e9ef3d7e4145270045e5e43e1081")
BOT_TOKEN = os.getenv("BOT_TOKEN", "7692429836:AAFhPqKJghT0G524pxCobrj-XfiXefTGnmA")
MONGO_URL = os.getenv("MONGO_URL", "mongodb+srv://aarshhub:6L1PAPikOnAIHIRA@cluster0.6shiu.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1002465297334"))
OWNER_ID = int(os.getenv("OWNER_ID", "6860316927"))
WELCOME_IMAGE = os.getenv("WELCOME_IMAGE", "https://envs.sh/n9o.jpg")
AUTO_DELETE_TIME = int(os.getenv("AUTO_DELETE_TIME", "7200"))
DEFAULT_POINTS = int(os.getenv("DEFAULT_POINTS", "5"))
DEFAULT_RESET_TIME = int(os.getenv("DEFAULT_RESET_TIME", "86400"))

id_pattern = re.compile(r'^.\d+$')
AUTH_CHANNEL = [int(ch) if id_pattern.search(ch) else ch for ch in os.getenv("AUTH_CHANNEL", "-1002490575006").split()]

bot = Client("video_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
mongo = MongoClient(MONGO_URL)
db = mongo["VideoBot"]
collection = db["videos"]
users_collection = db["users"]
settings_collection = db["settings"]

video_cache = []
last_cache_time = 0
CACHE_EXPIRY = 300

async def refresh_video_cache():
    global video_cache, last_cache_time
    if time.time() - last_cache_time > CACHE_EXPIRY:
        video_cache = list(collection.aggregate([{"$sample": {"size": 500}}]))
        last_cache_time = time.time()

async def add_user(user_id):
    user = users_collection.find_one({"id": user_id})
    if not user:
        settings = settings_collection.find_one({"_id": "points_settings"}) or {}
        reset_time = settings.get("reset_time", DEFAULT_RESET_TIME)
        users_collection.insert_one({
            "id": user_id,
            "joined": datetime.datetime.utcnow(),
            "points": DEFAULT_POINTS,
            "points_reset_time": time.time() + reset_time
        })

@bot.on_message(filters.command("start"))
async def start(client, message):
    user_id = message.from_user.id
    await add_user(user_id)

    if AUTH_CHANNEL:
        try:
            btn = []
            for id in AUTH_CHANNEL:
                chat = await client.get_chat(int(id))
                await client.get_chat_member(id, user_id)
        except UserNotParticipant:
            btn.append([InlineKeyboardButton(f'Join {chat.title}', url=chat.invite_link)])
            btn.append([InlineKeyboardButton("♻️ Try Again ♻️", url=f"https://t.me/{client.me.username}?start=true")])
            await message.reply_text(
                f"👋 **Hello {message.from_user.mention},**\n\nJoin the channel and click 'Try Again'.",
                reply_markup=InlineKeyboardMarkup(btn),
            )
            return
        except Exception:
            pass

    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🎥 Get Random Video", callback_data="get_random_video")]])
    await message.reply_photo(
        WELCOME_IMAGE,
        caption="🎉 Welcome to the Video Bot!\n\n<b>𝖳𝗁𝗂𝗌 𝖡𝗈𝗍 𝖢𝗈𝗇𝗍𝖺𝗂𝗇𝗌 18+ 𝖢𝗈𝗇𝗍𝖾𝗇𝗍...</b>",
        reply_markup=keyboard
    )

async def send_random_video(client, chat_id):
    await refresh_video_cache()

    if not video_cache:
        await client.send_message(chat_id, "⚠ No videos available. Use /index first!")
        return

    user = users_collection.find_one({"id": chat_id})
    if not user:
        await add_user(chat_id)
        user = users_collection.find_one({"id": chat_id})

    settings = settings_collection.find_one({"_id": "points_settings"}) or {}
    reset_interval = settings.get("reset_time", DEFAULT_RESET_TIME)

    if time.time() > user.get("points_reset_time", 0):
        users_collection.update_one(
            {"id": chat_id},
            {"$set": {"points": DEFAULT_POINTS, "points_reset_time": time.time() + reset_interval}}
        )
        user = users_collection.find_one({"id": chat_id})

    if user.get("points", 0) <= 0:
        reset_time = datetime.datetime.fromtimestamp(user.get("points_reset_time", 0)).strftime("%Y-%m-%d %H:%M:%S")
        await client.send_message(chat_id, f"⚠️ You have no points left. New points will be added at {reset_time}.")
        return

    video = video_cache.pop()
    try:
        message = await client.get_messages(CHANNEL_ID, video["message_id"])
        if message and message.video:
            sent_msg = await client.send_video(chat_id, video=message.video.file_id, caption="Thanks 😊", protect_content=True)
            users_collection.update_one({"id": chat_id}, {"$inc": {"points": -1}})

            if AUTO_DELETE_TIME > 0:
                await asyncio.sleep(AUTO_DELETE_TIME)
                await sent_msg.delete()

    except FloodWait as e:
        await asyncio.sleep(e.value)
        await send_random_video(client, chat_id)

@bot.on_callback_query(filters.regex("get_random_video"))
async def random_video_callback(client, callback_query: CallbackQuery):
    await callback_query.answer()
    asyncio.create_task(send_random_video(client, callback_query.message.chat.id))

@bot.on_message(filters.command("points"))
async def check_points(client, message):
    user_id = message.from_user.id
    user = users_collection.find_one({"id": user_id})

    if not user:
        await message.reply_text("⚠ Please start the bot first.")
        return

    settings = settings_collection.find_one({"_id": "points_settings"}) or {}
    reset_interval = settings.get("reset_time", DEFAULT_RESET_TIME)
    current_time = time.time()

    if current_time > user.get("points_reset_time", 0):
        users_collection.update_one(
            {"id": user_id},
            {"$set": {"points": DEFAULT_POINTS, "points_reset_time": current_time + reset_interval}}
        )
        user = users_collection.find_one({"id": user_id})

    points = user.get("points", 0)
    reset_time = datetime.datetime.fromtimestamp(user["points_reset_time"]).strftime("%Y-%m-%d %H:%M:%S")
    time_left = int(user["points_reset_time"] - current_time)

    await message.reply_text(
        f"⭐ **Points Left:** `{points}`\n"
        f"⏳ **Resets In:** `{str(datetime.timedelta(seconds=time_left))}`\n"
        f"🕒 **Next Reset At:** `{reset_time}`"
    )

@bot.on_message(filters.command("setpoints") & filters.user(OWNER_ID))
async def set_points_reset(client, message):
    try:
        _, duration = message.text.split()
        if duration.endswith("h"):
            hours = int(duration[:-1])
            reset_time = hours * 3600
        elif duration.endswith("m"):
            minutes = int(duration[:-1])
            reset_time = minutes * 60
        else:
            reset_time = int(duration)

        settings_collection.update_one({"_id": "points_settings"}, {"$set": {"reset_time": reset_time}}, upsert=True)
        users_collection.update_many({}, {"$set": {"points_reset_time": time.time() + reset_time}})

        await message.reply_text(f"✅ **Points reset interval updated to {duration}!**")
    except Exception as e:
        await message.reply_text("⚠ Usage: /setpoints <duration> (e.g., /setpoints 6h or /setpoints 30m)")

@bot.on_message(filters.command("getpoints") & filters.user(OWNER_ID))
async def get_points_reset(client, message):
    settings = settings_collection.find_one({"_id": "points_settings"}) or {}
    reset_time = settings.get("reset_time", DEFAULT_RESET_TIME)
    duration_str = str(datetime.timedelta(seconds=reset_time))
    await message.reply_text(f"⏱ Current points reset duration: {duration_str}")

@bot.on_message(filters.command("files") & filters.user(OWNER_ID))
async def total_files(client, message):
    total_files = collection.count_documents({})
    await message.reply_text(f"📂 Total Indexed Files: {total_files}")

if __name__ == "__main__":
    threading.Thread(target=start_health_check, daemon=True).start()
    bot.run()
