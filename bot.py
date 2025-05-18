import os
import logging
import asyncio
import threading
import time
import re
import datetime
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pymongo import MongoClient
from pyrogram.errors import UserNotParticipant, FloodWait
from health_check import start_health_check

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment Variables
API_ID = int(os.getenv("API_ID", "27788368"))
API_HASH = os.getenv("API_HASH", "9df7e9ef3d7e4145270045e5e43e1081")
BOT_TOKEN = os.getenv("BOT_TOKEN", "7692429836:AAFhPqKJghT0G524pxCobrj-XfiXefTGnmA")
MONGO_URL = os.getenv("MONGO_URL", "mongodb+srv://aarshhub:6L1PAPikOnAIHIRA@cluster0.6shiu.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1002465297334"))
OWNER_ID = int(os.getenv("OWNER_ID", "6860316927"))
WELCOME_IMAGE = os.getenv("WELCOME_IMAGE", "https://envs.sh/n9o.jpg")
AUTO_DELETE_TIME = int(os.getenv("AUTO_DELETE_TIME", "7200"))
DAILY_POINTS = int(os.getenv("DAILY_POINTS", "15"))
DEFAULT_RESET_TIME = int(os.getenv("DEFAULT_RESET_TIME", "86400"))

AUTH_CHANNEL = [int(ch) if re.match(r'^.\d+$', ch) else ch for ch in os.getenv("AUTH_CHANNEL", "-1002490575006").split()]

# Initialize Bot & Mongo
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
        users_collection.insert_one({
            "id": user_id,
            "joined": datetime.datetime.utcnow(),
            "points": DAILY_POINTS,
            "points_reset_time": time.time() + DEFAULT_RESET_TIME
        })
    else:
        update_fields = {}
        if "points" not in user:
            update_fields["points"] = DAILY_POINTS
        if "points_reset_time" not in user:
            update_fields["points_reset_time"] = time.time() + DEFAULT_RESET_TIME
        if update_fields:
            users_collection.update_one({"id": user_id}, {"$set": update_fields})

@bot.on_message(filters.command("start"))
async def start_cmd(client, message):
    user_id = message.from_user.id
    await add_user(user_id)

    if AUTH_CHANNEL:
        try:
            for ch_id in AUTH_CHANNEL:
                chat = await client.get_chat(ch_id)
                await client.get_chat_member(ch_id, user_id)
        except UserNotParticipant:
            btn = [
                [InlineKeyboardButton(f"Join {chat.title}", url=chat.invite_link)],
                [InlineKeyboardButton("♻️ Try Again ♻️", url=f"https://t.me/{client.me.username}?start=true")]
            ]
            await message.reply_text(
                f"👋 Hello {message.from_user.mention},\n\nJoin the channel and click 'Try Again'.",
                reply_markup=InlineKeyboardMarkup(btn)
            )
            return

    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🎥 Get Random Video", callback_data="get_random_video")]])
    await message.reply_photo(
        WELCOME_IMAGE,
        caption="🎉 Welcome to the Video Bot!\n\nThis bot contains 18+ content. Access at your own risk.",
        reply_markup=keyboard
    )

async def send_random_video(client, chat_id):
    await refresh_video_cache()

    if not video_cache:
        await client.send_message(chat_id, "⚠ No videos available. Use /index first.")
        return

    user = users_collection.find_one({"id": chat_id})
    if not user:
        await client.send_message(chat_id, "⚠ You are not registered. Use /start first.")
        return

    if chat_id != OWNER_ID:
        if time.time() > user.get("points_reset_time", 0):
            users_collection.update_one({"id": chat_id}, {
                "$set": {"points": DAILY_POINTS, "points_reset_time": time.time() + DEFAULT_RESET_TIME}
            })
            user["points"] = DAILY_POINTS

        if user["points"] <= 0:
            reset_time = datetime.datetime.fromtimestamp(user["points_reset_time"]).strftime("%Y-%m-%d %H:%M:%S")
            await client.send_message(
                chat_id,
                f"⚠️ You have 0 points left. Your points will reset at {reset_time}."
            )
            return

    video = video_cache.pop()
    try:
        message = await client.get_messages(CHANNEL_ID, video["message_id"])
        if message and message.video:
            sent = await client.send_video(chat_id, video=message.video.file_id, caption="Thanks!", protect_content=True)
            if chat_id != OWNER_ID:
                users_collection.update_one({"id": chat_id}, {"$inc": {"points": -1}})
            if AUTO_DELETE_TIME > 0:
                await asyncio.sleep(AUTO_DELETE_TIME)
                await sent.delete()
    except FloodWait as e:
        await asyncio.sleep(e.value)
        await send_random_video(client, chat_id)

@bot.on_callback_query(filters.regex("get_random_video"))
async def random_video_callback(client, callback_query: CallbackQuery):
    await callback_query.answer()
    asyncio.create_task(send_random_video(client, callback_query.message.chat.id))

@bot.on_message(filters.command("points"))
async def points_status(client, message):
    user_id = message.from_user.id
    if user_id == OWNER_ID:
        await message.reply_text("✅ You are the owner and have unlimited points.")
        return

    user = users_collection.find_one({"id": user_id})
    if not user:
        await message.reply_text("⚠️ User not found. Use /start first.")
        return

    now = time.time()
    if now > user.get("points_reset_time", 0):
        users_collection.update_one({"id": user_id}, {
            "$set": {"points": DAILY_POINTS, "points_reset_time": now + DEFAULT_RESET_TIME}
        })
        user["points"] = DAILY_POINTS
        user["points_reset_time"] = now + DEFAULT_RESET_TIME

    reset_time = datetime.datetime.fromtimestamp(user["points_reset_time"]).strftime("%Y-%m-%d %H:%M:%S")
    time_left = str(datetime.timedelta(seconds=int(user["points_reset_time"] - now)))

    await message.reply_text(
        f"📊 **Your Points:**\n"
        f"🎯 Points Left: {user['points']}\n"
        f"⏳ Next Reset: {reset_time}\n"
        f"🕒 Time Until Reset: {time_left}"
    )

@bot.on_message(filters.command("setpoints") & filters.user(OWNER_ID))
async def set_point_reset_time(client, message):
    try:
        input_text = message.text.split(maxsplit=1)[1].lower()

        # Parse time (e.g., 1h30m, 45m, 2h)
        match = re.findall(r"(\d+)([hm])", input_text)
        if not match:
            raise ValueError("Invalid time format")

        total_seconds = 0
        for value, unit in match:
            if unit == "h":
                total_seconds += int(value) * 3600
            elif unit == "m":
                total_seconds += int(value) * 60

        if total_seconds <= 0:
            raise ValueError("Reset interval must be greater than 0")

        # Save in settings and update all users
        settings_collection.update_one({"_id": "points_settings"}, {"$set": {"reset_time": total_seconds}}, upsert=True)

        new_reset_time = time.time() + total_seconds
        users_collection.update_many({}, {"$set": {"points_reset_time": new_reset_time}})

        formatted = str(datetime.timedelta(seconds=total_seconds))
        await message.reply_text(f"✅ Points reset interval set to `{formatted}` and applied to all users.")
    except Exception as e:
        await message.reply_text("⚠ Usage: `/setpoints 1h30m`, `/setpoints 45m`, etc.\n\nError: " + str(e))

@bot.on_message(filters.command("files") & filters.user(OWNER_ID))
async def total_files(client, message):
    count = collection.count_documents({})
    await message.reply_text(f"📂 Total Indexed Files: `{count}`")

if __name__ == "__main__":
    threading.Thread(target=start_health_check, daemon=True).start()
    bot.run()
