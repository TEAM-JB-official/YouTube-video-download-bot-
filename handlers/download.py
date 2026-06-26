import os
import uuid
import yt_dlp
import aiohttp
import aiofiles
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import Config
from database import get_user, get_user_settings, get_user_downloads_today, add_download_history, update_user
from helpers import humanbytes, get_duration
from handlers.force_sub import force_subscribe
import logging
import time

logger = logging.getLogger(__name__)

# Caches
FORMAT_CACHE = {}
PLAYLIST_CACHE = {}

# Robust YouTube URL regex
YOUTUBE_REGEX = r'^(https?://)?(www\.)?(youtube\.com|youtu\.be)/(watch\?v=|playlist\?list=|shorts/|embed/|v/|.+\?v=)?([^&?#]+)'

# Common yt-dlp options to avoid SABR and get all formats
BASE_YDL_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "extractor_args": {
        "youtube": {
            "player_client": ["android", "ios"],
            "skip": ["hls", "dash"]  # avoids some problematic streams
        }
    }
}

@Client.on_message(filters.private & filters.regex(YOUTUBE_REGEX))
async def youtube_handler(client, message):
    if Config.CHANNEL_ID:
        if await force_subscribe(client, message):
            return

    url = message.text.strip()
    user_id = message.from_user.id

    user = await get_user(user_id)
    if not user:
        await message.reply_text("Please /start the bot first.")
        return
    if user.get("banned"):
        await message.reply_text("You are banned.")
        return

    processing = await message.reply_text("🔍 Fetching video information...")

    try:
        ydl_opts = {
            **BASE_YDL_OPTS,
            "cookiefile": Config.COOKIE_FILE,
            "extract_flat": "in_playlist",
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if info.get('_type') == 'playlist':
            entries = info.get('entries', [])
            if not entries:
                await processing.edit_text("❌ Playlist is empty.")
                return

            # Cache playlist for later
            pl_key = str(uuid.uuid4())[:8]
            PLAYLIST_CACHE[pl_key] = {
                'entries': entries,
                'url': url,
                'title': info.get('title', 'Playlist')
            }

            buttons = []
            for idx, entry in enumerate(entries[:10]):
                title = entry.get('title', f'Video {idx+1}')
                buttons.append([
                    InlineKeyboardButton(
                        f"{idx+1}. {title[:35]}...",
                        callback_data=f"pl|{pl_key}|{idx}"
                    )
                ])
            buttons.append([InlineKeyboardButton("📥 Download All", callback_data=f"pl_all|{pl_key}")])
            buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
            await processing.edit_text(
                f"**Playlist Detected**\n\nSelect a video to download, or download all.",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            return

        # Single video
        await show_formats(client, processing, url, info)

    except Exception as e:
        logger.exception("Error processing URL")
        try:
            await processing.edit_text(f"❌ Error: {e}")
        except:
            await message.reply_text(f"❌ Error: {e}")

async def show_formats(client, message, url, info):
    formats = info.get('formats', [])
    vid_formats = []
    for f in formats:
        if f.get('vcodec') != 'none' and f.get('acodec') != 'none':
            height = f.get('height')
            label = f"{height}p" if height else f.get('format_note', 'Unknown')
            filesize = f.get('filesize') or f.get('filesize_approx')
            size_text = humanbytes(filesize) if filesize else "Unknown"
            vid_formats.append({
                'format_id': f['format_id'],
                'label': f"{label} ({size_text})",
                'height': height
            })
    audio_formats = []
    for f in formats:
        if f.get('acodec') != 'none' and f.get('vcodec') == 'none':
            audio_formats.append({
                'format_id': f['format_id'],
                'label': f"🎵 {f.get('format_note', 'Audio')} ({humanbytes(f.get('filesize') or 0)})"
            })

    key = str(uuid.uuid4())[:8]
    FORMAT_CACHE[key] = {'url': url, 'info': info, 'vid_formats': vid_formats, 'audio_formats': audio_formats}

    buttons = []
    seen = set()
    for fmt in vid_formats:
        if fmt['height'] in seen:
            continue
        seen.add(fmt['height'])
        buttons.append([InlineKeyboardButton(f"📹 {fmt['label']}", callback_data=f"dl|{key}|{fmt['format_id']}|video")])
    for fmt in audio_formats:
        buttons.append([InlineKeyboardButton(fmt['label'], callback_data=f"dl|{key}|{fmt['format_id']}|audio")])
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])

    title = info.get('title', 'Video')
    duration = info.get('duration')
    dur_text = get_duration(duration) if duration else 'N/A'
    await message.edit_text(
        f"**📺 {title}**\n⏱️ Duration: {dur_text}\n\nSelect format:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

@Client.on_callback_query(filters.regex(r"^dl\|"))
async def download_callback(client, callback_query):
    _, key, format_id, mode = callback_query.data.split("|")
    data = FORMAT_CACHE.get(key)
    if not data:
        await callback_query.answer("Session expired. Please resend the link.", show_alert=True)
        return
    url = data['url']

    queue = client.queue
    await callback_query.message.edit_text("⏳ Adding to queue...")

    async def progress_callback_func(text):
        try:
            await callback_query.message.edit_text(text)
        except:
            pass

    user_id = callback_query.from_user.id
    user = await get_user(user_id)
    if not user or user.get("banned"):
        await progress_callback_func("❌ You are banned.")
        return

    today_downloads = await get_user_downloads_today(user_id)
    daily_limit = user.get("daily_limit", Config.FREE_DAILY_LIMIT)
    if today_downloads >= daily_limit:
        await progress_callback_func(f"⚠️ Daily limit reached ({daily_limit}).")
        return

    await queue.add_task(user_id, url, format_id, mode, progress_callback_func)

@Client.on_callback_query(filters.regex(r"^pl\|"))
async def playlist_item(client, callback_query):
    parts = callback_query.data.split("|")
    if len(parts) < 3:
        await callback_query.answer("Invalid data.", show_alert=True)
        return
    pl_key, idx = parts[1], int(parts[2])
    playlist = PLAYLIST_CACHE.get(pl_key)
    if not playlist:
        await callback_query.answer("Playlist expired. Please resend the link.", show_alert=True)
        return
    entries = playlist['entries']
    if idx >= len(entries):
        await callback_query.answer("Video not found.", show_alert=True)
        return
    entry = entries[idx]
    video_id = entry.get('id')
    if not video_id:
        await callback_query.answer("Invalid video.", show_alert=True)
        return
    video_url = f"https://youtu.be/{video_id}"
    # Fake message to process
    class FakeMessage:
        text = video_url
        from_user = callback_query.from_user
        reply = callback_query.message.reply
    await youtube_handler(client, FakeMessage())
    await callback_query.message.delete()

@Client.on_callback_query(filters.regex(r"^pl_all\|"))
async def playlist_all(client, callback_query):
    parts = callback_query.data.split("|")
    if len(parts) < 2:
        await callback_query.answer("Invalid data.", show_alert=True)
        return
    pl_key = parts[1]
    playlist = PLAYLIST_CACHE.get(pl_key)
    if not playlist:
        await callback_query.answer("Playlist expired. Please resend the link.", show_alert=True)
        return
    entries = playlist['entries']
    if not entries:
        await callback_query.answer("No entries found.", show_alert=True)
        return

    await callback_query.message.edit_text(f"Adding {len(entries)} videos to queue...")
    queue = client.queue
    user_id = callback_query.from_user.id
    user = await get_user(user_id)
    if not user or user.get("banned"):
        await callback_query.message.edit_text("❌ You are banned.")
        return

    today_downloads = await get_user_downloads_today(user_id)
    daily_limit = user.get("daily_limit", Config.FREE_DAILY_LIMIT)
    remaining = daily_limit - today_downloads
    if remaining <= 0:
        await callback_query.message.edit_text(f"⚠️ Daily limit reached ({daily_limit}).")
        return

    max_dl = min(len(entries), 10, remaining)
    count = 0
    for entry in entries[:max_dl]:
        video_id = entry.get('id')
        if not video_id:
            continue
        video_url = f"https://youtu.be/{video_id}"
        await queue.add_task(user_id, video_url, "bestvideo+bestaudio", "video", lambda t: None)
        count += 1

    await callback_query.message.edit_text(f"✅ Added {count} videos to the queue.")

@Client.on_callback_query(filters.regex("cancel"))
async def cancel_callback(client, callback_query):
    await callback_query.message.delete()

# ---- Queue worker helpers ----

async def perform_download(user_id, url, format_id, mode, progress_callback):
    await progress_callback("⬇️ Downloading...")
    uid = str(uuid.uuid4())[:8]
    os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)
    output = f"{Config.DOWNLOAD_DIR}/{uid}.%(ext)s"

    ydl_opts = {
        **BASE_YDL_OPTS,
        "cookiefile": Config.COOKIE_FILE,
        "outtmpl": output,
        "merge_output_format": "mp4",
    }
    if mode == "audio":
        ydl_opts.update({
            "format": format_id if format_id != "bestaudio" else "bestaudio/best",
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
        })
    else:
        ydl_opts["format"] = f"{format_id}+bestaudio/best" if format_id != "bestvideo" else "bestvideo+bestaudio"

    if Config.HTTP_PROXY:
        ydl_opts["proxy"] = Config.HTTP_PROXY

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get('title', 'Video')
        duration = info.get('duration')
        width = info.get('width')
        height = info.get('height')
        thumb_url = info.get('thumbnail')
        filesize = info.get('filesize') or info.get('filesize_approx')
        ext = "mp3" if mode == "audio" else "mp4"
        file_path = f"{Config.DOWNLOAD_DIR}/{uid}.{ext}"
        if not os.path.exists(file_path):
            for f in os.listdir(Config.DOWNLOAD_DIR):
                if f.startswith(uid):
                    file_path = os.path.join(Config.DOWNLOAD_DIR, f)
                    break

        thumb_path = None
        if thumb_url:
            async with aiohttp.ClientSession() as session:
                async with session.get(thumb_url) as resp:
                    if resp.status == 200:
                        thumb_path = f"{Config.DOWNLOAD_DIR}/{uid}_thumb.jpg"
                        async with aiofiles.open(thumb_path, "wb") as f:
                            await f.write(await resp.read())
        if thumb_path:
            from fix_thumb import fix_thumb
            thumb_path = await fix_thumb(thumb_path)

        return {
            "file_path": file_path,
            "thumb": thumb_path,
            "title": title,
            "duration": duration,
            "width": width,
            "height": height,
            "size": filesize
        }

async def upload_file(client, user_id, file_path, thumb, title, duration, width, height, mode, callback):
    settings = await get_user_settings(user_id)
    chat_id = settings.get("upload_chat_id") or user_id
    caption = settings.get("caption") or f"📹 **{title}**\n📦 Size: {humanbytes(os.path.getsize(file_path))}"
    thumb_file_id = settings.get("thumb_file_id")
    if thumb_file_id:
        thumb = thumb_file_id

    try:
        if mode == "audio":
            await client.send_audio(
                chat_id=chat_id,
                audio=file_path,
                caption=caption,
                duration=duration,
                thumb=thumb if thumb else None
            )
        else:
            await client.send_video(
                chat_id=chat_id,
                video=file_path,
                caption=caption,
                duration=duration,
                width=width,
                height=height,
                thumb=thumb if thumb else None,
                supports_streaming=True
            )
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)
        if thumb and os.path.exists(thumb):
            os.remove(thumb)
