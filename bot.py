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
DEFAULT_RESET_TIME = int(os.getenv("DEFAULT_RESET_TIME", "18000"))

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

PREMIUM_TIERS = {
    "silver": 10,
    "gold": 20,
    "diamond": 30,
    "platinum": 40
}

REFERRAL_TIERS = {
    5: ("silver", 10),
    10: ("gold", 20),
    20: ("diamond", 50)
}


def get_user(user_id):
    user = users_collection.find_one({"id": user_id})
    if not user:
        settings = settings_collection.find_one({"_id": "points_settings"}) or {}
        reset_time = settings.get("reset_time", DEFAULT_RESET_TIME)
        user = {
            "id": user_id,
            "joined": datetime.datetime.utcnow(),
            "points": DEFAULT_POINTS,
            "points_reset_time": time.time() + reset_time,
            "referral_points": 0,
            "referrals": [],
            "premium_used": 0,
            "premium": None
        }
        users_collection.insert_one(user)
    return user


async def refresh_video_cache():
    global video_cache, last_cache_time
    if time.time() - last_cache_time > CACHE_EXPIRY:
        video_cache = list(collection.aggregate([{"$sample": {"size": 500}}]))
        last_cache_time = time.time()


async def add_user(user_id):
    return get_user(user_id)


@bot.on_message(filters.command("start"))
async def start(client, message):
    user_id = message.from_user.id
    args = message.text.split()
    await add_user(user_id)

    if len(args) > 1 and args[1].startswith("ref-"):
        ref_id = int(args[1].split("-")[1])
        if ref_id != user_id:
            user = get_user(user_id)
            if ref_id not in user.get("referrals", []):
                users_collection.update_one({"id": ref_id}, {"$addToSet": {"referrals": user_id}})

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

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🎥 Get Random Video", callback_data="get_random_video")]]
    )
    await message.reply_photo(
        WELCOME_IMAGE,
        caption="🎉 Welcome to the Video Bot!\n\n<b>𝖳𝗁𝗂𝗌 𝖡𝗈𝗍 𝖢𝗈𝗇𝗍𝖺𝗂𝗇𝗌 18+ 𝖢𝗈𝗇𝗍𝖾𝗇𝗍...</b>",
        reply_markup=keyboard
    )


async def calculate_total_points(user):
    total = user.get("points", 0)
    referral_points = user.get("referral_points", 0)
    premium = user.get("premium")
    premium_points = 0
    if premium and time.time() < premium.get("expiry", 0):
        tier = premium.get("tier")
        max_premium = PREMIUM_TIERS.get(tier, 0)
        used = user.get("premium_used", 0)
        premium_points = max_premium - used if used < max_premium else 0
        total += premium_points
    total += referral_points
    return total, referral_points, premium_points


async def reset_points_if_needed(user):
    settings = settings_collection.find_one({"_id": "points_settings"}) or {}
    reset_interval = settings.get("reset_time", DEFAULT_RESET_TIME)
    if time.time() > user.get("points_reset_time", 0):
        referral_points = 0
        for r, (name, pts) in REFERRAL_TIERS.items():
            if len(user.get("referrals", [])) >= r:
                referral_points = pts
        update_fields = {
            "points": DEFAULT_POINTS,
            "points_reset_time": time.time() + reset_interval,
            "referral_points": referral_points
        }
        if user.get("premium") and time.time() < user["premium"].get("expiry", 0):
            update_fields["premium_used"] = 0
        users_collection.update_one({"id": user["id"]}, {"$set": update_fields})
        user = get_user(user["id"])
    return user


async def send_random_video(client, chat_id):
    await refresh_video_cache()

    if not video_cache:
        await client.send_message(chat_id, "⚠ No videos available. Use /index first!")
        return

    if chat_id == OWNER_ID:
        consume = False
    else:
        user = get_user(chat_id)
        user = await reset_points_if_needed(user)
        if user["points"] > 0:
            users_collection.update_one({"id": chat_id}, {"$inc": {"points": -1}})
        elif user["referral_points"] > 0:
            users_collection.update_one({"id": chat_id}, {"$inc": {"referral_points": -1}})
        elif user.get("premium") and time.time() < user["premium"].get("expiry", 0):
            tier = user["premium"]["tier"]
            max_premium = PREMIUM_TIERS.get(tier, 0)
            if user.get("premium_used", 0) < max_premium:
                users_collection.update_one({"id": chat_id}, {"$inc": {"premium_used": 1}})
            else:
                reset_time = datetime.datetime.fromtimestamp(user.get("points_reset_time", 0)).strftime("%Y-%m-%d %H:%M:%S")
                await client.send_message(chat_id, f"⚠️ You have no points left. New points will be added at {reset_time}.")
                return
        else:
            reset_time = datetime.datetime.fromtimestamp(user.get("points_reset_time", 0)).strftime("%Y-%m-%d %H:%M:%S")
            await client.send_message(chat_id, f"⚠️ You have no points left. New points will be added at {reset_time}.")
            return

    video = video_cache.pop()
    try:
        message = await client.get_messages(CHANNEL_ID, video["message_id"])
        if message and message.video:
            sent_msg = await client.send_video(chat_id, video=message.video.file_id, caption="Thanks 😊", protect_content=True)
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
    user = get_user(message.from_user.id)
    user = await reset_points_if_needed(user)
    points, ref, prem = await calculate_total_points(user)
    reset_time = datetime.datetime.fromtimestamp(user["points_reset_time"]).strftime("%Y-%m-%d %H:%M:%S")
    time_left = int(user["points_reset_time"] - time.time())
    await message.reply_text(
        f"⭐ Points: {user.get('points', 0)}\n"
        f"🤝 Referral Points: {ref}\n"
        f"💎 Premium Bonus Left: {prem}\n"
        f"⏳ Next Reset In: {str(datetime.timedelta(seconds=time_left))}\n"
        f"🕒 Reset At: {reset_time}"
    )


@bot.on_message(filters.command("addpremium") & filters.user(OWNER_ID))
async def add_premium(client, message):
    try:
        _, uid, level, days = message.text.split()
        uid = int(uid)
        days = int(days)
        expiry = time.time() + (days * 86400)
        users_collection.update_one({"id": uid}, {"$set": {"premium": {"tier": level.lower(), "expiry": expiry}, "premium_used": 0}})
        await message.reply_text("✅ Premium added.")
    except:
        await message.reply_text("Usage: /addpremium <user_id> <tier> <days>")


@bot.on_message(filters.command("removepremium") & filters.user(OWNER_ID))
async def remove_premium(client, message):
    try:
        _, uid = message.text.split()
        uid = int(uid)
        users_collection.update_one({"id": uid}, {"$unset": {"premium": "", "premium_used": ""}})
        await message.reply_text("✅ Premium removed.")
    except:
        await message.reply_text("Usage: /removepremium <user_id>")


@bot.on_message(filters.command("myplans"))
async def my_plans(client, message):
    user = get_user(message.from_user.id)
    premium = user.get("premium")
    text = "Your Plans:\n"
    if premium:
        expiry = datetime.datetime.fromtimestamp(premium["expiry"]).strftime("%Y-%m-%d")
        text += f"💎 Premium: {premium['tier'].capitalize()} (until {expiry})\n"
    else:
        text += "💎 Premium: None\n"
    ref_count = len(user.get("referrals", []))
    text += f"🤝 Referrals: {ref_count}\n"
    await message.reply_text(text)


@bot.on_message(filters.command("referral"))
async def referral_link(client, message):
    user_id = message.from_user.id
    link = f"https://t.me/{client.me.username}?start=ref-{user_id}"
    await message.reply_text(f"🔗 Your referral link:\n{link}")


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
        await message.reply_text(f"✅ Points reset interval updated to {duration}!")
    except:
        await message.reply_text("⚠ Usage: /setpoints <duration> (e.g., /setpoints 6h or /setpoints 30m)")


@bot.on_message(filters.command("getpoints") & filters.user(OWNER_ID))
async def get_points_reset(client, message):
    settings = settings_collection.find_one({"_id": "points_settings"}) or {}
    reset_time = settings.get("reset_time", DEFAULT_RESET_TIME)
    duration_str = str(datetime.timedelta(seconds=reset_time))
    await message.reply_text(f"⏱ Current points reset duration: {duration_str}")

@bot.on_message(filters.command("myreferrals"))
async def my_referrals(client, message):
    user = get_user(message.from_user.id)
    referrals = user.get("referrals", [])
    count = len(referrals)
    tier_name = "None"

    for threshold, (name, _) in sorted(REFERRAL_TIERS.items(), reverse=True):
        if count >= threshold:
            tier_name = name.capitalize()
            break

    await message.reply_text(
        f"🤝 You have referred **{count}** user(s).\n"
        f"🏅 Your current referral tier: **{tier_name}**\n\n"
        "🔗 Share your referral link using /referral"
    )


@bot.on_message(filters.command("premiumusers") & filters.user(OWNER_ID))
async def list_premium_users(client, message):
    users = users_collection.find({"premium": {"$ne": None}})
    lines = []

    for user in users:
        uid = user["id"]
        tier = user["premium"].get("tier", "Unknown").capitalize()
        expiry_ts = user["premium"].get("expiry", 0)
        expiry = datetime.datetime.fromtimestamp(expiry_ts).strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"User ID: {uid} | Tier: {tier} | Expiry: {expiry}")

    if not lines:
        await message.reply_text("❌ No premium users found.")
        return

    file_path = "/tmp/premium_users.txt"
    with open(file_path, "w") as f:
        f.write("\n".join(lines))

    await message.reply_document(file_path, caption="📄 Premium Users List")


@bot.on_message(filters.command("plans"))
async def show_plans(client, message):
    text = (
        "💎 **Premium Plans**\n\n"
        "• **Silver** – 10 daily points\n"
        "   └ Rs. 29 / $0.35\n\n"
        "• **Gold** – 20 daily points\n"
        "   └ Rs. 59 / $0.70\n\n"
        "• **Diamond** – 30 daily points\n"
        "   └ Rs. 89 / $1.05\n\n"
        "• **Platinum** – 40 daily points\n"
        "   └ Rs. 129 / $1.50\n\n"
        "⏳ Plans renew daily until expiry.\n"
        "🧾 Custom duration available.\n\n"
        "📞 Contact us to buy a plan!"
    )

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 Buy Plan", url="https://t.me/YourUsername")]
    ])
    await message.reply_text(text, reply_markup=buttons)


@bot.on_message(filters.command("files") & filters.user(OWNER_ID))
async def total_files(client, message):
    total_files = collection.count_documents({})
    await message.reply_text(f"📂 Total Indexed Files: {total_files}")


if __name__ == "__main__":
    threading.Thread(target=start_health_check, daemon=True).start()
    bot.run()
