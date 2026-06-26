# handlers/thumbnail.py

import os
import yt_dlp
import aiohttp
from pyrogram import Client, filters
from config import Config
from handlers.force_sub import force_subscribe

@Client.on_message(filters.private & filters.command("thumbnail"))
async def generate_thumbnail(client, message):
    # Force subscribe check
    if Config.CHANNEL_ID:
        if await force_subscribe(client, message):
            return

    if len(message.command) < 2:
        await message.reply_text(
            "❗ Please provide a YouTube video link.\n\n"
            "**Example:** `/thumbnail https://youtu.be/xxxx`"
        )
        return

    video_url = message.text.split(" ", 1)[1]
    wait_msg = await message.reply_text("🔍 Fetching thumbnail...")

    try:
        # Extract video info without downloading
        ydl_opts = {
            "quiet": True,
            "cookiefile": Config.COOKIE_FILE,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            thumbnail_url = info.get("thumbnail")

        if not thumbnail_url:
            await wait_msg.delete()
            await message.reply_text("⚠️ Couldn't find any thumbnail for this video.")
            return

        # Download and send the thumbnail
        async with aiohttp.ClientSession() as session:
            async with session.get(thumbnail_url) as resp:
                if resp.status == 200:
                    file_name = "temp_thumb.jpg"
                    with open(file_name, "wb") as f:
                        f.write(await resp.read())

                    await message.reply_photo(
                        photo=file_name,
                        caption="🖼️ **Video Thumbnail**"
                    )
                    os.remove(file_name)
                else:
                    await message.reply_text(f"Thumbnail URL: {thumbnail_url}")

        await wait_msg.delete()

    except Exception as e:
        await wait_msg.delete()
        await message.reply_text(f"❌ Error: `{str(e)}`")
