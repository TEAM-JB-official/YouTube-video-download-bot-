from pyrogram import Client, filters
from database import get_user, get_user_downloads_today
from helpers import get_ist_time

@Client.on_message(filters.private & filters.command("myaccount"))
async def myaccount(client, message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    if not user:
        await message.reply_text("No account data. Please start the bot first.")
        return
    text = f"**👤 Account Info**\n\n"
    text += f"🆔 User ID: `{user_id}`\n"
    text += f"👤 Username: @{message.from_user.username or 'N/A'}\n"
    text += f"📅 Joined: {user.get('join_date', 'Unknown')}\n"
    text += f"📥 Total Downloads: {user.get('total_downloads', 0)}\n"
    text += f"📊 Today's Downloads: {await get_user_downloads_today(user_id)}\n"
    text += f"💎 Premium: {'✅ Yes' if user.get('is_premium') else '❌ No'}\n"
    if user.get('is_premium'):
        expiry = user.get('plan_expiry')
        if expiry:
            text += f"⏳ Expires: {expiry.strftime('%Y-%m-%d %H:%M')} IST\n"
    await message.reply_text(text)

@Client.on_message(filters.private & filters.command("myplan"))
async def myplan(client, message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    if not user:
        await message.reply_text("No account data.")
        return
    if user.get('is_premium'):
        text = f"**💎 Premium Plan**\n\n"
        text += f"Plan: {user.get('plan', 'Monthly')}\n"
        text += f"Expiry: {user.get('plan_expiry').strftime('%Y-%m-%d %H:%M')} IST\n"
        text += f"File Size Limit: {user.get('size_limit_mb', 0)} MB\n"
        text += f"Daily Limit: {user.get('daily_limit', 0)}\n"
        text += f"Queue Limit: {user.get('queue_limit', 0)}"
    else:
        text = "You are on **Free Plan**.\n\n"
        text += f"Daily Limit: {Config.FREE_DAILY_LIMIT}\n"
        text += f"File Size Limit: {Config.FREE_FILE_SIZE_MB} MB\n"
        text += f"Queue Limit: {Config.FREE_QUEUE_LIMIT}\n\n"
        text += "Contact admin to upgrade."
    await message.reply_text(text)

@Client.on_message(filters.private & filters.command("profile"))
async def profile(client, message):
    # Same as myaccount but maybe different format
    await myaccount(client, message)
