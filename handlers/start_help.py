from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from database import get_user, update_user
from handlers.force_sub import force_subscribe
from config import Config
import datetime

currentTime = datetime.datetime.now()
if currentTime.hour < 12:
    wish = "Good morning 🌞"
elif 12 <= currentTime.hour < 18:
    wish = "Good afternoon 🌤️"
else:
    wish = "Good evening 🌝"

@Client.on_message(filters.private & filters.command("start"))
async def start(client, message):
    user_id = message.from_user.id

    # 🔹 Register user in MongoDB if not exists
    user = await get_user(user_id)
    if not user:
        await update_user(user_id, {
            "username": message.from_user.username,
            "first_name": message.from_user.first_name,
            "join_date": datetime.datetime.now().isoformat(),
            "is_premium": False,
            "total_downloads": 0,
            "daily_limit": Config.FREE_DAILY_LIMIT,
            "size_limit_mb": Config.FREE_FILE_SIZE_MB,
            "queue_limit": Config.FREE_QUEUE_LIMIT
        })

    if Config.CHANNEL_ID:
        if await force_subscribe(client, message):
            return

    text = f"**Hello {message.from_user.first_name}!** {wish}\n\n"
    text += "Send me a YouTube link to download video/audio.\n"
    text += "Use /help for more info."

    await message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Channel", url="https://t.me/NT_BOT_CHANNEL"),
             InlineKeyboardButton("👥 Support", url="https://t.me/NT_BOTS_SUPPORT")],
            [InlineKeyboardButton("❓ Help", callback_data="help")]
        ])
    )

@Client.on_message(filters.private & filters.command("help"))
async def help(client, message):
    if Config.CHANNEL_ID:
        if await force_subscribe(client, message):
            return
    text = """**📖 Help Guide**

Send me a YouTube link (or playlist) and I'll show available formats.

**Commands:**
/start - Start the bot
/help - This help
/about - About bot
/date - Current date/time
/thumbnail - Generate thumbnail from video link

**Account:**
/myaccount - Your account info
/myplan - Your plan details
/profile - Your profile

**Settings:**
/setchat - Set upload chat (channel/group)
/removechat - Remove upload chat
/checkchat - Check current chat
/setcaption - Set default caption
/removecaption - Remove caption
/setthumb - Set custom thumbnail
/remthumb - Remove thumbnail

**Premium (Admin only):**
/addpremium - Add premium user
/rempremium - Remove premium
/premiumusers - List premium users

**Admin (Admin only):**
/stats - Bot statistics
/users - List users
/broadcast - Broadcast message
/ban - Ban user
/unban - Unban user
/restart - Restart bot
/logs - Get logs

**Features:**
- YouTube video (multiple qualities)
- Audio (MP3/M4A)
- Playlist support
- Shorts support
- Live stream recording
- Queue system
- Force subscribe
- Upload to chat/channel/group
"""
    await message.reply_text(text)

@Client.on_message(filters.private & filters.command("about"))
async def about(client, message):
    if Config.CHANNEL_ID:
        if await force_subscribe(client, message):
            return
    text = """**🤖 YouTube Download Bot V4**

**Framework:** Pyrogram
**Language:** Python 3.13
**Database:** MongoDB
**Developer:** @TeamJB_bot

©️ 2026 TeamJB
"""
    await message.reply_text(text)

@Client.on_callback_query(filters.regex("help"))
async def help_callback(client, callback_query):
    await callback_query.message.delete()
    await help(client, callback_query.message)
