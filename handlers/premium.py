from pyrogram import Client, filters
from datetime import datetime, timedelta
import pytz
from database import add_premium, remove_premium, get_premium_users
from handlers.admin import ADMIN_IDS

@Client.on_message(filters.private & filters.command("addpremium") & filters.user(ADMIN_IDS))
async def add_premium_cmd(client, message):
    if len(message.command) < 4:
        await message.reply_text("Usage: /addpremium <user_id> <plan> <days>\nPlan: monthly, yearly")
        return
    user_id = int(message.command[1])
    plan = message.command[2]
    days = int(message.command[3])
    if plan not in ["monthly", "yearly"]:
        await message.reply_text("Plan must be 'monthly' or 'yearly'.")
        return
    await add_premium(user_id, plan, days, message.from_user.id)
    await message.reply_text(f"Added premium to {user_id} for {days} days.")

@Client.on_message(filters.private & filters.command("rempremium") & filters.user(ADMIN_IDS))
async def rem_premium_cmd(client, message):
    if len(message.command) < 2:
        await message.reply_text("Usage: /rempremium <user_id>")
        return
    user_id = int(message.command[1])
    await remove_premium(user_id)
    await message.reply_text(f"Removed premium from {user_id}.")

@Client.on_message(filters.private & filters.command("premiumusers") & filters.user(ADMIN_IDS))
async def list_premium(client, message):
    users = await get_premium_users()
    if not users:
        await message.reply_text("No premium users.")
        return
    text = "**💎 Premium Users:**\n\n"
    for user in users:
        text += f"`{user['user_id']}` - {user.get('plan', 'Unknown')} - expires {user.get('plan_expiry').strftime('%Y-%m-%d')}\n"
    await message.reply_text(text)
