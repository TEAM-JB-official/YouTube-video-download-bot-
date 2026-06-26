from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import UserNotParticipant
from config import Config

async def force_subscribe(client, message):
    if not Config.CHANNEL_ID:
        return False
    try:
        user = await client.get_chat_member(Config.CHANNEL_ID, message.from_user.id)
        if user.status == "kicked":
            await message.reply_text("You are banned from using this bot.")
            return True
        return False
    except UserNotParticipant:
        invite_link = await client.create_chat_invite_link(Config.CHANNEL_ID)
        await message.reply_text(
            "**Please join my channel to use this bot.**",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔔 Join Channel", url=invite_link.invite_link)],
                [InlineKeyboardButton("🔄 I've Joined", callback_data="check_sub")]
            ])
        )
        return True
    except Exception as e:
        await message.reply_text(f"Error: {e}")
        return True

@Client.on_callback_query(filters.regex("check_sub"))
async def check_sub(client, callback_query):
    user_id = callback_query.from_user.id
    try:
        user = await client.get_chat_member(Config.CHANNEL_ID, user_id)
        if user.status != "kicked":
            await callback_query.message.delete()
            await callback_query.message.reply_text("✅ Now you can use the bot. Send me a YouTube link.")
        else:
            await callback_query.answer("You are banned.", show_alert=True)
    except UserNotParticipant:
        await callback_query.answer("You haven't joined yet.", show_alert=True)
