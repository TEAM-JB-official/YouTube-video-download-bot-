from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from database import get_all_users, count_users, count_premium_users, ban_user, unban_user
from helpers import get_ist_time
import os

# Admin list (from config or hardcode)
ADMIN_IDS = [123456789]  # Replace with your admin IDs

def is_admin(user_id):
    return user_id in ADMIN_IDS

@Client.on_message(filters.private & filters.command("stats") & filters.user(ADMIN_IDS))
async def stats(client, message):
    total_users = await count_users()
    premium_users = await count_premium_users()
    text = f"**📊 Bot Statistics**\n\n"
    text += f"👥 Total Users: {total_users}\n"
    text += f"💎 Premium Users: {premium_users}\n"
    text += f"📅 Uptime: (bot started)\n"  # we could add uptime
    await message.reply_text(text)

@Client.on_message(filters.private & filters.command("users") & filters.user(ADMIN_IDS))
async def list_users(client, message):
    users = await get_all_users()
    if not users:
        await message.reply_text("No users.")
        return
    text = "**👥 Users List:**\n\n"
    for user in users[:50]:  # limit to 50
        text += f"`{user['user_id']}` - @{user.get('username', 'N/A')} - {'Premium' if user.get('is_premium') else 'Free'}\n"
    if len(users) > 50:
        text += f"\n... and {len(users)-50} more"
    await message.reply_text(text)

@Client.on_message(filters.private & filters.command("broadcast") & filters.user(ADMIN_IDS))
async def broadcast(client, message):
    if not message.reply_to_message:
        await message.reply_text("Reply to a message to broadcast.")
        return
    users = await get_all_users()
    count = 0
    for user in users:
        try:
            await message.reply_to_message.copy(user['user_id'])
            count += 1
        except:
            pass
    await message.reply_text(f"Broadcast sent to {count} users.")

@Client.on_message(filters.private & filters.command("ban") & filters.user(ADMIN_IDS))
async def ban(client, message):
    if len(message.command) < 2:
        await message.reply_text("Usage: /ban <user_id>")
        return
    user_id = int(message.command[1])
    await ban_user(user_id)
    await message.reply_text(f"Banned user {user_id}.")

@Client.on_message(filters.private & filters.command("unban") & filters.user(ADMIN_IDS))
async def unban(client, message):
    if len(message.command) < 2:
        await message.reply_text("Usage: /unban <user_id>")
        return
    user_id = int(message.command[1])
    await unban_user(user_id)
    await message.reply_text(f"Unbanned user {user_id}.")

@Client.on_message(filters.private & filters.command("restart") & filters.user(ADMIN_IDS))
async def restart(client, message):
    await message.reply_text("Restarting...")
    # Graceful restart: exit and let process manager restart
    os._exit(0)

@Client.on_message(filters.private & filters.command("logs") & filters.user(ADMIN_IDS))
async def get_logs(client, message):
    if not os.path.exists("bot.log"):
        await message.reply_text("No log file.")
        return
    await message.reply_document("bot.log")
