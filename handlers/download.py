import os
import uuid
import yt_dlp
import aiohttp
import aiofiles
import asyncio
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import Config
from database import get_user, get_user_settings, get_user_downloads_today, add_download_history
from helpers import humanbytes, get_duration
from handlers.force_sub import force_subscribe
import logging

logger = logging.getLogger(__name__)

FORMAT_CACHE = {}
PLAYLIST_CACHE = {}

YOUTUBE_REGEX = r'^(https?://)?(www\.)?(youtube\.com|youtu\.be)/(watch\?v=|playlist\?list=|shorts/|embed/|v/|.+\?v=)?([^&?#]+)'

# Base options – we'll override player_client dynamically
BASE_YDL_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "extractor_args": {
        "youtube": {
            "skip": ["hls", "dash"]
        }
    }
}

def build_ydl_opts(format_spec, player_clients=None, cookiefile=None, proxy=None):
    """Build yt-dlp options with given player clients."""
    opts = {
        **BASE_YDL_OPTS,
        "format": format_spec,
        "outtmpl": "%(id)s.%(ext)s",
        "merge_output_format": "mp4",
        "cookiefile": cookiefile or Config.COOKIE_FILE,
        "extractor_args": {
            "youtube": {
                "player_client": player_clients or ["android", "ios"],
                "skip": ["hls", "dash"]
            }
        }
    }
    if proxy:
        opts["proxy"] = proxy
    if format_spec.endswith("audio") or "audio" in format_spec:
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192"
        }]
    return opts

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
        # First try with android+ios, fallback to web if needed
        for clients in (["android", "ios"], ["web"]):
            try:
                ydl_opts = build_ydl_opts("best", player_clients=clients)
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                break
            except Exception as e:
                logger.warning(f"Player clients {clients} failed: {e}")
                if clients == ["web"]:
                    raise
                continue

        if info.get('_type') == 'playlist':
            entries = info.get('entries', [])
            if not entries:
                await processing.edit_text("❌ Playlist is empty.")
                return

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
            buttons.append([InlineKeyboardButton("📥 Download All (Video)", callback_data=f"pl_all_video|{pl_key}")])
            buttons.append([InlineKeyboardButton("🎵 Download All (Audio)", callback_data=f"pl_all_audio|{pl_key}")])
            buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
            await processing.edit_text(
                f"**Playlist Detected**\n\nSelect a video or download all.",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            return

        await show_formats(client, processing, url, info)

    except yt_dlp.utils.DownloadError as e:
        await processing.edit_text(f"❌ YouTube error: {e}")
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

    try:
        for clients in (["android", "ios"], ["web"]):
            try:
                ydl_opts = build_ydl_opts("best", player_clients=clients)
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(video_url, download=False)
                break
            except:
                continue
        await show_formats(client, callback_query.message, video_url, info)
        await callback_query.message.delete()
    except Exception as e:
        await callback_query.answer(f"Error: {e}", show_alert=True)

@Client.on_callback_query(filters.regex(r"^pl_all_video\|"))
async def playlist_all_video(client, callback_query):
    await playlist_all(client, callback_query, mode="video")

@Client.on_callback_query(filters.regex(r"^pl_all_audio\|"))
async def playlist_all_audio(client, callback_query):
    await playlist_all(client, callback_query, mode="audio")

async def playlist_all(client, callback_query, mode):
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
    total = max_dl

    status_msg = await callback_query.message.edit_text(
        f"⚡ **Batch process started**\n"
        f"🎯 Mode: {'Video' if mode=='video' else 'Audio'}\n"
        f"📦 Total: {total}\n"
        f"⏳ Processing: 0/{total}\n\n"
        f"Powered by Team JB ❤️"
    )

    queue = client.queue
    completed = 0
    failed = 0
    lock = asyncio.Lock()

    async def update_progress(inc_completed=0, inc_failed=0):
        nonlocal completed, failed
        async with lock:
            completed += inc_completed
            failed += inc_failed
            current = completed + failed
            if current <= total:
                await status_msg.edit_text(
                    f"⚡ **Batch process started**\n"
                    f"🎯 Mode: {'Video' if mode=='video' else 'Audio'}\n"
                    f"📦 Total: {total}\n"
                    f"⏳ Processing: {current}/{total}\n"
                    f"✅ Completed: {completed}\n"
                    f"❌ Failed: {failed}\n\n"
                    f"Powered by Team JB ❤️"
                )
            if current == total:
                await status_msg.edit_text(
                    f"✅ **Batch Complete!**\n"
                    f"🎯 Mode: {'Video' if mode=='video' else 'Audio'}\n"
                    f"📦 Total: {total}\n"
                    f"✅ Success: {completed}\n"
                    f"❌ Failed: {failed}\n\n"
                    f"Powered by Team JB ❤️"
                )

    for idx, entry in enumerate(entries[:max_dl]):
        video_id = entry.get('id')
        if not video_id:
            continue
        video_url = f"https://youtu.be/{video_id}"

        async def single_callback(text, index=idx):
            await client.send_message(user_id, f"🎬 Video {index+1}/{total}: {text}")
            if "✅" in text or "completed" in text.lower():
                await update_progress(inc_completed=1)
            elif "❌" in text or "error" in text.lower() or "failed" in text.lower():
                await update_progress(inc_failed=1)

        await queue.add_task(user_id, video_url, "bestvideo+bestaudio" if mode == "video" else "bestaudio", mode, single_callback)

    if Config.CHANNEL_ID:
        try:
            invite_link = await client.create_chat_invite_link(Config.CHANNEL_ID)
            await client.send_message(
                user_id,
                "🔔 **Join our channel for updates!**",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📢 Join Channel", url=invite_link.invite_link)]
                ])
            )
        except Exception as e:
            logger.warning(f"Could not send channel invite: {e}")

@Client.on_callback_query(filters.regex("cancel"))
async def cancel_callback(client, callback_query):
    await callback_query.message.delete()

# ---- Queue worker helpers ----

async def perform_download(user_id, url, format_id, mode, progress_callback):
    await progress_callback("⬇️ Downloading...")
    uid = str(uuid.uuid4())[:8]
    os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)
    output = f"{Config.DOWNLOAD_DIR}/{uid}.%(ext)s"

    # Build format string
    if mode == "video":
        base_fmt = f"{format_id}+bestaudio/best" if format_id not in ["bestvideo+bestaudio", "bestvideo"] else "bestvideo+bestaudio"
    else:
        base_fmt = format_id if format_id != "bestaudio" else "bestaudio"

    formats_to_try = [
        base_fmt,
        "bestvideo+bestaudio" if mode == "video" else "bestaudio",
        "best"  # ultimate fallback
    ]

    info = None
    last_error = None
    for fmt in formats_to_try:
        for clients in (["android", "ios"], ["web"]):
            try:
                ydl_opts = build_ydl_opts(fmt, player_clients=clients, cookiefile=Config.COOKIE_FILE)
                ydl_opts["outtmpl"] = output
                if mode == "audio":
                    ydl_opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]

                def _download():
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        return ydl.extract_info(url, download=True)

                info = await asyncio.to_thread(_download)
                break
            except Exception as e:
                last_error = e
                logger.warning(f"Format {fmt} with clients {clients} failed: {e}")
                await progress_callback(f"⚠️ Retrying with another format...")
                continue
        if info:
            break

    if info is None:
        err_msg = str(last_error)
        if "Requested format is not available" in err_msg or "Failed to extract" in err_msg:
            await progress_callback(
                "❌ YouTube is blocking the request.\n"
                "Please ensure `cookies.txt` is valid and up-to-date.\n"
                "Export cookies from a logged-in YouTube session."
            )
        else:
            await progress_callback(f"❌ All formats failed: {err_msg}")
        raise last_error

    # Extract metadata
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
    # Remove HTML to avoid "Unclosed tags" warning
    caption = settings.get("caption") or f"📹 {title}\n📦 Size: {humanbytes(os.path.getsize(file_path))}"
    caption = caption.replace("<", "(").replace(">", ")")
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
        await callback("✅ Upload completed!")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)
        if thumb and os.path.exists(thumb):
            os.remove(thumb)
