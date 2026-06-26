import os
import uuid
import yt_dlp
import aiohttp
import aiofiles
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import Config
from database import get_user, get_user_settings, get_user_downloads_today, add_download_history
from helpers import humanbytes, progress_callback, get_duration
from queue_manager import DownloadQueue
from handlers.force_sub import force_subscribe
import logging

logger = logging.getLogger(__name__)

# Cache for format info
FORMAT_CACHE = {}

@Client.on_message(filters.regex(r'^(http(s)?://)?(www\.)?(youtube\.com|youtu\.be)/.+'))
async def youtube_handler(client, message):
    if Config.CHANNEL_ID:
        if await force_subscribe(client, message):
            return
    url = message.text.strip()
    user_id = message.from_user.id

    # Check user exists
    user = await get_user(user_id)
    if not user:
        await message.reply_text("Please /start the bot first.")
        return

    # Check if user is banned
    if user.get("banned"):
        await message.reply_text("You are banned.")
        return

    processing = await message.reply_text("🔍 Fetching video information...")

    try:
        # Extract info
        ydl_opts = {
            "quiet": True,
            "cookiefile": Config.COOKIE_FILE,
            "extract_flat": "in_playlist",
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        # Check if it's a playlist
        if info.get('_type') == 'playlist':
            entries = info.get('entries', [])
            if not entries:
                await processing.edit_text("❌ Playlist is empty.")
                return
            # Show first 10 entries with buttons
            buttons = []
            for idx, entry in enumerate(entries[:10]):
                title = entry.get('title', f'Video {idx+1}')
                video_id = entry.get('id')
                buttons.append([
                    InlineKeyboardButton(
                        f"{idx+1}. {title[:30]}...",
                        callback_data=f"pl|{url}|{video_id}|{idx}"
                    )
                ])
            buttons.append([InlineKeyboardButton("📥 Download All", callback_data=f"pl_all|{url}")])
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
        await processing.edit_text(f"❌ Error: {e}")

async def show_formats(client, message, url, info):
    # Extract formats
    formats = info.get('formats', [])
    # Filter video formats with audio (or we'll merge)
    vid_formats = []
    for f in formats:
        if f.get('vcodec') != 'none' and f.get('acodec') != 'none':
            # Has both video and audio
            height = f.get('height')
            if height:
                label = f"{height}p"
            else:
                label = f.get('format_note', 'Unknown')
            filesize = f.get('filesize') or f.get('filesize_approx')
            size_text = humanbytes(filesize) if filesize else "Unknown"
            vid_formats.append({
                'format_id': f['format_id'],
                'label': f"{label} ({size_text})",
                'filesize': filesize,
                'height': height
            })
    # Also audio only
    audio_formats = []
    for f in formats:
        if f.get('acodec') != 'none' and f.get('vcodec') == 'none':
            audio_formats.append({
                'format_id': f['format_id'],
                'label': f"🎵 {f.get('format_note', 'Audio')} ({humanbytes(f.get('filesize') or 0)})"
            })

    # Generate unique key
    key = str(uuid.uuid4())[:8]
    FORMAT_CACHE[key] = {
        'url': url,
        'info': info,
        'vid_formats': vid_formats,
        'audio_formats': audio_formats
    }

    buttons = []
    # Add video formats (limit to unique heights)
    seen = set()
    for fmt in vid_formats:
        if fmt['height'] in seen:
            continue
        seen.add(fmt['height'])
        buttons.append([
            InlineKeyboardButton(
                f"📹 {fmt['label']}",
                callback_data=f"dl|{key}|{fmt['format_id']}|video"
            )
        ])
    # Audio
    for fmt in audio_formats:
        buttons.append([
            InlineKeyboardButton(
                fmt['label'],
                callback_data=f"dl|{key}|{fmt['format_id']}|audio"
            )
        ])
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
    info = data['info']

    # We'll add to queue
    queue: DownloadQueue = client.queue
    await callback_query.message.edit_text("⏳ Adding to queue...")

    # We need a callback for progress updates
    async def progress_callback_func(text):
        try:
            await callback_query.message.edit_text(text)
        except:
            pass

    # Check user limits
    user_id = callback_query.from_user.id
    user = await get_user(user_id)
    if not user:
        await progress_callback_func("❌ User not found.")
        return
    if user.get("banned"):
        await progress_callback_func("❌ You are banned.")
        return

    # Check daily limit
    today_downloads = await get_user_downloads_today(user_id)
    daily_limit = user.get("daily_limit", Config.FREE_DAILY_LIMIT)
    if today_downloads >= daily_limit:
        await progress_callback_func(f"⚠️ Daily limit reached ({daily_limit}).")
        return

    # Check file size limit
    size_limit_mb = user.get("size_limit_mb", Config.FREE_FILE_SIZE_MB)
    # We'll check after download

    # Add to queue
    await queue.add_task(user_id, url, format_id, mode, progress_callback_func)

@Client.on_callback_query(filters.regex(r"^pl\|"))
async def playlist_item(client, callback_query):
    _, url, video_id, idx = callback_query.data.split("|")
    # We'll create a single video URL
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
    url = callback_query.data.split("|")[1]
    # Extract all entries and add to queue
    ydl_opts = {"quiet": True, "cookiefile": Config.COOKIE_FILE, "extract_flat": "in_playlist"}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        entries = info.get('entries', [])
        if not entries:
            await callback_query.answer("No entries found.", show_alert=True)
            return
        # For each, we'll add to queue
        # We need to create a queue task for each
        # But we must limit to avoid flooding
        max_dl = 10  # limit to 10 for safety
        await callback_query.message.edit_text(f"Adding up to {min(max_dl, len(entries))} videos to queue...")
        queue: DownloadQueue = client.queue
        user_id = callback_query.from_user.id
        user = await get_user(user_id)
        if not user or user.get("banned"):
            await callback_query.message.edit_text("❌ You are banned.")
            return
        # Check limits
        today_downloads = await get_user_downloads_today(user_id)
        daily_limit = user.get("daily_limit", Config.FREE_DAILY_LIMIT)
        if today_downloads >= daily_limit:
            await callback_query.message.edit_text(f"⚠️ Daily limit reached ({daily_limit}).")
            return
        remaining = daily_limit - today_downloads
        count = 0
        for entry in entries[:max_dl]:
            if count >= remaining:
                break
            video_url = f"https://youtu.be/{entry['id']}"
            # We'll add to queue with best quality
            # For simplicity, use best video+audio
            await queue.add_task(user_id, video_url, "bestvideo+bestaudio", "video", lambda t: None)
            count += 1
        await callback_query.message.edit_text(f"Added {count} videos to queue. Check progress in private chat.")
        # We'll not use progress callbacks for each, user will see notifications.

@Client.on_callback_query(filters.regex("cancel"))
async def cancel_callback(client, callback_query):
    await callback_query.message.delete()

# The actual download function (called by queue worker)
async def perform_download(user_id, url, format_id, mode, progress_callback):
    # progress_callback is a coroutine that sends updates
    await progress_callback("⬇️ Downloading...")

    # Prepare output template
    uid = str(uuid.uuid4())[:8]
    os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)
    output = f"{Config.DOWNLOAD_DIR}/{uid}.%(ext)s"

    if mode == "audio":
        ydl_opts = {
            "format": format_id if format_id != "bestaudio" else "bestaudio/best",
            "outtmpl": output,
            "quiet": True,
            "cookiefile": Config.COOKIE_FILE,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
            "progress_hooks": [lambda d: print(d)],
        }
    else:
        ydl_opts = {
            "format": f"{format_id}+bestaudio/best" if format_id != "bestvideo" else "bestvideo+bestaudio",
            "outtmpl": output,
            "quiet": True,
            "cookiefile": Config.COOKIE_FILE,
            "merge_output_format": "mp4",
            "progress_hooks": [],
        }

    # Add proxy if set
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

        # Determine file path
        if mode == "audio":
            ext = "mp3"
        else:
            ext = "mp4"
        file_path = f"{Config.DOWNLOAD_DIR}/{uid}.{ext}"

        # If not exists, try find the file
        if not os.path.exists(file_path):
            # yt-dlp may output different extension; search
            for f in os.listdir(Config.DOWNLOAD_DIR):
                if f.startswith(uid):
                    file_path = os.path.join(Config.DOWNLOAD_DIR, f)
                    break

        # Download thumbnail
        thumb_path = None
        if thumb_url:
            async with aiohttp.ClientSession() as session:
                async with session.get(thumb_url) as resp:
                    if resp.status == 200:
                        thumb_path = f"{Config.DOWNLOAD_DIR}/{uid}_thumb.jpg"
                        async with aiofiles.open(thumb_path, "wb") as f:
                            await f.write(await resp.read())

        # Fix thumbnail (optional, from your fix_thumb)
        if thumb_path:
            from fix_thumb import fix_thumb
            thumb_path = await fix_thumb(thumb_path)

        result = {
            "file_path": file_path,
            "thumb": thumb_path,
            "title": title,
            "duration": duration,
            "width": width,
            "height": height,
            "size": filesize
        }
        return result

# Upload function (called by queue worker)
async def upload_file(user_id, file_path, thumb, title, duration, width, height, mode, callback):
    # Get user settings
    settings = await get_user_settings(user_id)
    chat_id = settings.get("upload_chat_id")
    if not chat_id:
        chat_id = user_id  # send to private

    caption = settings.get("caption", "")
    if not caption:
        caption = f"📹 **{title}**\n📦 Size: {humanbytes(os.path.getsize(file_path))}"
    thumb_file_id = settings.get("thumb_file_id")

    # If user set a custom thumb, use that instead
    if thumb_file_id:
        thumb = thumb_file_id

    try:
        if mode == "audio":
            await client.send_audio(
                chat_id=chat_id,
                audio=file_path,
                caption=caption,
                duration=duration,
                thumb=thumb if thumb else None,
                progress=progress_callback,
                progress_args=(callback, time.time())
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
                supports_streaming=True,
                progress=progress_callback,
                progress_args=(callback, time.time())
            )
    finally:
        # Cleanup
        if os.path.exists(file_path):
            os.remove(file_path)
        if thumb and os.path.exists(thumb):
            os.remove(thumb)
