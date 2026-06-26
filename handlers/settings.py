from pyrogram import Client, filters
from pyrogram.types import ForceReply, Message
from database import update_user_settings, get_user_settings
import os

@Client.on_message(filters.private & filters.command("setchat"))
async def set_chat(client, message):
    if len(message.command) < 2:
        await message.reply_text("Usage: /setchat <chat_id> (can be channel/group id)")
        return
    chat_id = message.command[1]
    await update_user_settings(message.from_user.id, {"upload_chat_id": chat_id})
    await message.reply_text(f"Upload chat set to `{chat_id}`")

@Client.on_message(filters.private & filters.command("removechat"))
async def remove_chat(client, message):
    settings = await get_user_settings(message.from_user.id)
    if "upload_chat_id" in settings:
        del settings["upload_chat_id"]
    await update_user_settings(message.from_user.id, settings)
    await message.reply_text("Upload chat removed. Files will be sent in private.")

@Client.on_message(filters.private & filters.command("checkchat"))
async def check_chat(client, message):
    settings = await get_user_settings(message.from_user.id)
    chat_id = settings.get("upload_chat_id")
    if chat_id:
        await message.reply_text(f"Current upload chat: `{chat_id}`")
    else:
        await message.reply_text("No upload chat set. Files will be sent in private.")

@Client.on_message(filters.private & filters.command("setcaption"))
async def set_caption(client, message):
    if not message.reply_to_message:
        await message.reply_text("Reply to a message with the caption text.")
        return
    caption = message.reply_to_message.text or message.reply_to_message.caption
    if caption:
        await update_user_settings(message.from_user.id, {"caption": caption})
        await message.reply_text("Caption set.")
    else:
        await message.reply_text("No text found in replied message.")

@Client.on_message(filters.private & filters.command("removecaption"))
async def remove_caption(client, message):
    settings = await get_user_settings(message.from_user.id)
    if "caption" in settings:
        del settings["caption"]
    await update_user_settings(message.from_user.id, settings)
    await message.reply_text("Caption removed.")

@Client.on_message(filters.private & filters.command("setthumb"))
async def set_thumb(client, message):
    if not message.reply_to_message or not message.reply_to_message.photo:
        await message.reply_text("Reply to a photo to set as thumbnail.")
        return
    photo = message.reply_to_message.photo.file_id
    await update_user_settings(message.from_user.id, {"thumb_file_id": photo})
    await message.reply_text("Thumbnail set.")

@Client.on_message(filters.private & filters.command("remthumb"))
async def remove_thumb(client, message):
    settings = await get_user_settings(message.from_user.id)
    if "thumb_file_id" in settings:
        del settings["thumb_file_id"]
    await update_user_settings(message.from_user.id, settings)
    await message.reply_text("Thumbnail removed.")
