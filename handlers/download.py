import os, uuid, yt_dlp, aiohttp, aiofiles, asyncio, logging
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import Config
from database import get_user, get_user_settings, get_user_downloads_today, add_download_history
from helpers import humanbytes, get_duration
from handlers.force_sub import force_subscribe

logger = logging.getLogger(__name__)
FORMAT_CACHE, PLAYLIST_CACHE = {}, {}
YOUTUBE_REGEX = r'^(https?://)?(www\.)?(youtube\.com|youtu\.be)/(watch\?v=|playlist\?list=|shorts/|embed/|v/|.+\?v=)?([^&?#]+)'

def build_ydl_opts(fmt, cookiefile=None, proxy=None, clients=None):
    opts = {
        "quiet": True, "no_warnings": False, "format": fmt,
        "outtmpl": "%(id)s.%(ext)s", "merge_output_format": "mp4",
        "cookiefile": cookiefile or Config.COOKIE_FILE,
        "extractor_args": {"youtube": {"player_client": clients or ["android", "ios", "web"],
                                       "skip": ["hls", "dash"], "player_skip": ["webpage", "configs"]}},
        "ignoreerrors": True, "no_check_certificate": True, "prefer_insecure": True
    }
    if proxy: opts["proxy"] = proxy
    if "audio" in fmt or fmt.startswith("bestaudio"):
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
    return opts

async def extract_info(url, fmt="best", cookiefile=None, proxy=None):
    """Run yt-dlp in a thread to avoid blocking the event loop."""
    def _extract():
        opts = build_ydl_opts(fmt, cookiefile, proxy, ["android", "ios", "web"])
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    try:
        return await asyncio.to_thread(_extract)
    except Exception as e:
        logger.warning(f"Extraction failed: {e}")
        return None

async def get_video_info(url, cookiefile=None, proxy=None):
    """Try multiple clients; return info or None."""
    for clients in (["android", "ios"], ["web"]):
        try:
            opts = build_ydl_opts("best", cookiefile, proxy, clients)
            def _extract():
                with yt_dlp.YoutubeDL(opts) as ydl:
                    return ydl.extract_info(url, download=False)
            info = await asyncio.to_thread(_extract)
            if info: return info
        except Exception as e:
            logger.warning(f"Client {clients} failed: {e}")
            if "Sign in" in str(e) or "cookies" in str(e).lower():
                # If we get a login error, we'll try without cookies for android/ios
                if clients != ["web"]:
                    continue
                else:
                    # With web and still needs login, return None
                    pass
    return None

@Client.on_message(filters.private & filters.regex(YOUTUBE_REGEX))
async def youtube_handler(client, message):
    if Config.CHANNEL_ID and await force_subscribe(client, message): return
    url, user_id = message.text.strip(), message.from_user.id
    user = await get_user(user_id)
    if not user: return await message.reply_text("Please /start first.")
    if user.get("banned"): return await message.reply_text("You are banned.")
    processing = await message.reply_text("🔍 Fetching video info...")
    try:
        info = await get_video_info(url, Config.COOKIE_FILE, Config.HTTP_PROXY)
        if not info:
            await processing.edit_text(
                "❌ Could not fetch video info.\n"
                "This may be due to YouTube bot protection.\n"
                "Please ensure `cookies.txt` is valid (exported from a logged-in browser)."
            )
            return
        if info.get('_type') == 'playlist':
            entries = info.get('entries', [])
            if not entries: return await processing.edit_text("❌ Playlist empty.")
            pl_key = str(uuid.uuid4())[:8]
            PLAYLIST_CACHE[pl_key] = {'entries': entries, 'url': url, 'title': info.get('title', 'Playlist')}
            buttons = []
            for idx, entry in enumerate(entries[:10]):
                if entry is None: continue
                title = entry.get('title', f'Video {idx+1}')
                buttons.append([InlineKeyboardButton(f"{idx+1}. {title[:35]}...", callback_data=f"pl|{pl_key}|{idx}")])
            buttons.append([InlineKeyboardButton("📥 Download All (Video)", callback_data=f"pl_all_video|{pl_key}")])
            buttons.append([InlineKeyboardButton("🎵 Download All (Audio)", callback_data=f"pl_all_audio|{pl_key}")])
            buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
            await processing.edit_text("**Playlist Detected**\n\nSelect video or download all.",
                                       reply_markup=InlineKeyboardMarkup(buttons))
            return
        await show_formats(client, processing, url, info)
    except Exception as e:
        logger.exception("Error processing URL")
        await processing.edit_text(f"❌ Error: {e}")

async def show_formats(client, message, url, info):
    formats = info.get('formats', [])
    vid_formats, audio_formats = [], []
    for f in formats:
        if f.get('vcodec') != 'none' and f.get('acodec') != 'none':
            height = f.get('height')
            label = f"{height}p" if height else f.get('format_note', 'Unknown')
            size = humanbytes(f.get('filesize') or f.get('filesize_approx') or 0)
            vid_formats.append({'format_id': f['format_id'], 'label': f"{label} ({size})", 'height': height})
        elif f.get('acodec') != 'none' and f.get('vcodec') == 'none':
            size = humanbytes(f.get('filesize') or 0)
            audio_formats.append({'format_id': f['format_id'], 'label': f"🎵 {f.get('format_note', 'Audio')} ({size})"})
    key = str(uuid.uuid4())[:8]
    FORMAT_CACHE[key] = {'url': url, 'info': info, 'vid_formats': vid_formats, 'audio_formats': audio_formats}
    buttons = []
    seen = set()
    for fmt in vid_formats:
        if fmt['height'] not in seen:
            seen.add(fmt['height'])
            buttons.append([InlineKeyboardButton(f"📹 {fmt['label']}", callback_data=f"dl|{key}|{fmt['format_id']}|video")])
    for fmt in audio_formats:
        buttons.append([InlineKeyboardButton(fmt['label'], callback_data=f"dl|{key}|{fmt['format_id']}|audio")])
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
    title = info.get('title', 'Video').replace('<', '(').replace('>', ')')
    dur = get_duration(info.get('duration')) if info.get('duration') else 'N/A'
    await message.edit_text(f"**📺 {title}**\n⏱️ Duration: {dur}\n\nSelect format:", reply_markup=InlineKeyboardMarkup(buttons))

@Client.on_callback_query(filters.regex(r"^dl\|"))
async def download_callback(client, cq):
    _, key, fmt_id, mode = cq.data.split("|")
    data = FORMAT_CACHE.get(key)
    if not data: return await cq.answer("Session expired. Resend link.", show_alert=True)
    url = data['url']
    await cq.message.edit_text("⏳ Adding to queue...")
    async def prog(text):
        try: await cq.message.edit_text(text)
        except: pass
    user_id = cq.from_user.id
    user = await get_user(user_id)
    if not user or user.get("banned"): return await prog("❌ Banned.")
    today = await get_user_downloads_today(user_id)
    limit = user.get("daily_limit", Config.FREE_DAILY_LIMIT)
    if today >= limit: return await prog(f"⚠️ Daily limit reached ({limit}).")
    await client.queue.add_task(user_id, url, fmt_id, mode, prog)

@Client.on_callback_query(filters.regex(r"^pl\|"))
async def playlist_item(client, cq):
    parts = cq.data.split("|")
    if len(parts) < 3: return await cq.answer("Invalid data.", show_alert=True)
    pl_key, idx = parts[1], int(parts[2])
    pl = PLAYLIST_CACHE.get(pl_key)
    if not pl: return await cq.answer("Playlist expired.", show_alert=True)
    entries = pl['entries']
    if idx >= len(entries): return await cq.answer("Video not found.", show_alert=True)
    entry = entries[idx]
    if entry is None: return await cq.answer("Invalid video entry.", show_alert=True)
    video_id = entry.get('id')
    if not video_id: return await cq.answer("Invalid video.", show_alert=True)
    video_url = f"https://youtu.be/{video_id}"
    info = await get_video_info(video_url, Config.COOKIE_FILE, Config.HTTP_PROXY)
    if info:
        await show_formats(client, cq.message, video_url, info)
        await cq.message.delete()
    else:
        await cq.answer("Could not fetch video info.", show_alert=True)

@Client.on_callback_query(filters.regex(r"^pl_all_video\|"))
async def playlist_all_video(client, cq): await playlist_all(client, cq, "video")
@Client.on_callback_query(filters.regex(r"^pl_all_audio\|"))
async def playlist_all_audio(client, cq): await playlist_all(client, cq, "audio")

async def playlist_all(client, cq, mode):
    parts = cq.data.split("|")
    if len(parts) < 2: return await cq.answer("Invalid data.", show_alert=True)
    pl_key = parts[1]
    pl = PLAYLIST_CACHE.get(pl_key)
    if not pl: return await cq.answer("Playlist expired.", show_alert=True)
    entries = pl['entries']
    if not entries: return await cq.answer("No entries.", show_alert=True)
    user_id = cq.from_user.id
    user = await get_user(user_id)
    if not user or user.get("banned"): return await cq.message.edit_text("❌ Banned.")
    today = await get_user_downloads_today(user_id)
    limit = user.get("daily_limit", Config.FREE_DAILY_LIMIT)
    rem = limit - today
    if rem <= 0: return await cq.message.edit_text(f"⚠️ Daily limit reached ({limit}).")
    max_dl = min(len(entries), 10, rem)
    total = max_dl
    status_msg = await cq.message.edit_text(
        f"⚡ **Batch started**\n🎯 Mode: {'Video' if mode=='video' else 'Audio'}\n📦 Total: {total}\n⏳ Processing: 0/{total}\n\nPowered by Team JB ❤️"
    )
    queue = client.queue
    completed = failed = 0
    lock = asyncio.Lock()
    current_text = status_msg.text

    async def update(inc_c=0, inc_f=0):
        nonlocal completed, failed, current_text
        async with lock:
            completed += inc_c; failed += inc_f
            cur = completed + failed
            if cur <= total:
                new_text = f"⚡ **Batch started**\n🎯 Mode: {'Video' if mode=='video' else 'Audio'}\n📦 Total: {total}\n⏳ Processing: {cur}/{total}\n✅ Completed: {completed}\n❌ Failed: {failed}\n\nPowered by Team JB ❤️"
            if cur == total:
                new_text = f"✅ **Batch Complete!**\n🎯 Mode: {'Video' if mode=='video' else 'Audio'}\n📦 Total: {total}\n✅ Success: {completed}\n❌ Failed: {failed}\n\nPowered by Team JB ❤️"
            if new_text != current_text:
                try:
                    await status_msg.edit_text(new_text)
                    current_text = new_text
                except:
                    pass

    for idx, entry in enumerate(entries[:max_dl]):
        if entry is None: continue
        video_id = entry.get('id')
        if not video_id: continue
        video_url = f"https://youtu.be/{video_id}"
        async def cb(text, index=idx):
            await client.send_message(user_id, f"🎬 Video {index+1}/{total}: {text}")
            if "✅" in text or "completed" in text.lower():
                await update(inc_c=1)
            elif "❌" in text or "error" in text.lower() or "failed" in text.lower():
                await update(inc_f=1)
        await queue.add_task(user_id, video_url, "bestvideo+bestaudio" if mode=="video" else "bestaudio", mode, cb)
    if Config.CHANNEL_ID:
        try:
            link = await client.create_chat_invite_link(Config.CHANNEL_ID)
            await client.send_message(user_id, "🔔 **Join our channel for updates!**",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📢 Join Channel", url=link.invite_link)]]))
        except: pass

@Client.on_callback_query(filters.regex("cancel"))
async def cancel_callback(client, cq): await cq.message.delete()

# ---- Download & Upload helpers ----
async def perform_download(user_id, url, fmt_id, mode, prog):
    await prog("⬇️ Downloading...")
    uid = str(uuid.uuid4())[:8]
    os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)
    out = f"{Config.DOWNLOAD_DIR}/{uid}.%(ext)s"
    base_fmt = (f"{fmt_id}+bestaudio/best" if mode=="video" and fmt_id not in ["bestvideo+bestaudio","bestvideo"]
                else (fmt_id if mode=="audio" else "bestvideo+bestaudio"))
    formats = [base_fmt, "bestvideo+bestaudio/best" if mode=="video" else "bestaudio/best", "best"]
    info = None
    last_err = None
    for fmt in formats:
        for clients in (["android", "ios"], ["web"]):
            try:
                opts = build_ydl_opts(fmt, Config.COOKIE_FILE, Config.HTTP_PROXY, clients)
                opts["outtmpl"] = out
                if mode=="audio":
                    opts["postprocessors"] = [{"key":"FFmpegExtractAudio","preferredcodec":"mp3","preferredquality":"192"}]
                def _dl():
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        return ydl.extract_info(url, download=True)
                info = await asyncio.to_thread(_dl)
                break
            except Exception as e:
                last_err = e
                await prog("⚠️ Retrying...")
                continue
        if info: break
    if not info:
        err = str(last_err)
        if "Sign in" in err or "cookies" in err.lower():
            await prog("❌ YouTube requires login (bot detection).\nPlease provide a valid `cookies.txt`.")
        else:
            await prog(f"❌ All formats failed: {err}")
        raise last_err
    title = info.get('title', 'Video')
    duration = info.get('duration')
    width, height = info.get('width'), info.get('height')
    thumb_url = info.get('thumbnail')
    filesize = info.get('filesize') or info.get('filesize_approx')
    ext = "mp3" if mode=="audio" else "mp4"
    file_path = f"{Config.DOWNLOAD_DIR}/{uid}.{ext}"
    if not os.path.exists(file_path):
        for f in os.listdir(Config.DOWNLOAD_DIR):
            if f.startswith(uid):
                file_path = os.path.join(Config.DOWNLOAD_DIR, f)
                break
    thumb_path = None
    if thumb_url:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(thumb_url) as resp:
                if resp.status == 200:
                    thumb_path = f"{Config.DOWNLOAD_DIR}/{uid}_thumb.jpg"
                    async with aiofiles.open(thumb_path, "wb") as f:
                        await f.write(await resp.read())
    if thumb_path:
        from fix_thumb import fix_thumb
        thumb_path = await fix_thumb(thumb_path)
    return {"file_path": file_path, "thumb": thumb_path, "title": title, "duration": duration,
            "width": width, "height": height, "size": filesize}

async def upload_file(client, user_id, file_path, thumb, title, duration, width, height, mode, cb):
    settings = await get_user_settings(user_id)
    chat_id = settings.get("upload_chat_id") or user_id
    caption = (settings.get("caption") or f"📹 {title}\n📦 Size: {humanbytes(os.path.getsize(file_path))}").replace('<','(').replace('>',')')
    thumb = settings.get("thumb_file_id") or thumb
    try:
        if mode == "audio":
            await client.send_audio(chat_id=chat_id, audio=file_path, caption=caption, duration=duration, thumb=thumb if thumb else None)
        else:
            await client.send_video(chat_id=chat_id, video=file_path, caption=caption, duration=duration,
                                    width=width, height=height, thumb=thumb if thumb else None, supports_streaming=True)
        await cb("✅ Upload completed!")
    finally:
        if os.path.exists(file_path): os.remove(file_path)
        if thumb and os.path.exists(thumb): os.remove(thumb)
