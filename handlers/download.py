import os, uuid, yt_dlp, aiohttp, aiofiles, asyncio, logging, random, subprocess, time
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import Config
from database import get_user, get_user_settings, get_user_downloads_today, add_download_history
from helpers import humanbytes, get_duration, progress_bar
from handlers.force_sub import force_subscribe

logger = logging.getLogger(__name__)
FORMAT_CACHE, PLAYLIST_CACHE = {}, {}
YOUTUBE_REGEX = r'^(https?://)?(www\.)?(youtube\.com|youtu\.be)/(watch\?v=|playlist\?list=|shorts/|embed/|v/|.+\?v=)?([^&?#]+)'

PROXY = os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY") or Config.HTTP_PROXY or None

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/115.0",
]

def build_ydl_opts(fmt, cookiefile=None, proxy=None, clients=None, extra_args=None):
    opts = {
        "quiet": True,
        "no_warnings": False,
        "format": fmt,
        "outtmpl": "%(id)s.%(ext)s",
        "merge_output_format": "mp4",
        "extractor_args": {
            "youtube": {
                "player_client": clients or ["web", "android", "ios"],
                "skip": ["hls", "dash"],
                "player_skip": ["webpage", "configs"],
            }
        },
        "ignoreerrors": True,
        "no_check_certificate": True,
        "prefer_insecure": True,
        "user_agent": random.choice(USER_AGENTS),
    }
    if cookiefile:
        opts["cookiefile"] = cookiefile
    if extra_args:
        opts["extractor_args"]["youtube"].update(extra_args)
    if proxy:
        opts["proxy"] = proxy
    if "audio" in fmt or fmt.startswith("bestaudio"):
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
    return opts

async def extract_with_clients(url, clients, cookiefile=None, proxy=None, extra_args=None):
    try:
        opts = build_ydl_opts("best", cookiefile, proxy, clients, extra_args)
        def _extract():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)
        return await asyncio.to_thread(_extract)
    except Exception as e:
        logger.warning(f"Extraction with {clients} failed: {e}")
        return None

async def get_video_info(url, cookiefile=None, proxy=None):
    strategies = [
        (["web"], cookiefile, None),
        (["android", "ios"], None, None),
        (["android"], cookiefile, {"skip": ["webpage", "configs", "hls", "dash"], "player_skip": ["webpage", "configs"]}),
        (["ios"], cookiefile, {"skip": ["webpage", "configs", "hls", "dash"], "player_skip": ["webpage", "configs"]}),
        (["web"], cookiefile, {"skip": ["webpage", "configs", "hls", "dash"], "player_skip": ["webpage", "configs"]}),
    ]
    for clients, cf, extra in strategies:
        info = await extract_with_clients(url, clients, cf, proxy, extra)
        if info:
            return info
    return None

# ==================== PROGRESS BAR CALLBACKS ====================

async def download_progress_callback(current, total, message, start_time, status_msg):
    if total == 0:
        return
    elapsed = time.time() - start_time
    speed = current / elapsed if elapsed > 0 else 0
    eta = (total - current) / speed if speed > 0 else 0
    percent = (current / total) * 100
    if speed > 10*1024*1024: status = "🚀 Fast"
    elif speed > 2*1024*1024: status = "👍 Good"
    elif speed > 500*1024: status = "🐢 Slow"
    else: status = "🐌 Very Slow"
    bar = progress_bar(percent)
    text = (
        f"╭──────────────╮\n│ 📥 Downloading...\n├──────────────\n│\n│ {bar} {percent:.1f}%\n│\n│ 📦 {humanbytes(current)} / {humanbytes(total)}\n"
        f"│ ⚡ {humanbytes(speed)}/s\n│ ⏱️ {get_duration(eta)}\n│ 🔗 {status}\n│\n╰──────────────╯"
    )
    try: await status_msg.edit_text(text)
    except: pass

async def upload_progress_callback(current, total, message, start_time, status_msg):
    if total == 0: return
    elapsed = time.time() - start_time
    speed = current / elapsed if elapsed > 0 else 0
    eta = (total - current) / speed if speed > 0 else 0
    percent = (current / total) * 100
    if speed > 10*1024*1024: status = "🚀 Fast"
    elif speed > 2*1024*1024: status = "👍 Good"
    elif speed > 500*1024: status = "🐢 Slow"
    else: status = "🐌 Very Slow"
    bar = progress_bar(percent)
    text = (
        f"╭──────────────╮\n│ 📤 Uploading...\n├──────────────\n│\n│ {bar} {percent:.1f}%\n│\n│ 📦 {humanbytes(current)} / {humanbytes(total)}\n"
        f"│ ⚡ {humanbytes(speed)}/s\n│ ⏱️ {get_duration(eta)}\n│ 🔗 {status}\n│\n╰──────────────╯"
    )
    try: await status_msg.edit_text(text)
    except: pass

# ==================== HANDLERS ====================

@Client.on_message(filters.private & filters.regex(YOUTUBE_REGEX))
async def youtube_handler(client, message):
    if Config.CHANNEL_ID and await force_subscribe(client, message):
        return
    url, user_id = message.text.strip(), message.from_user.id
    user = await get_user(user_id)
    if not user:
        return await message.reply_text("Please /start first.")
    if user.get("banned"):
        return await message.reply_text("You are banned.")
    processing = await message.reply_text("🔍 Fetching video info...")
    try:
        info = await get_video_info(url, Config.COOKIE_FILE, PROXY)
        if not info:
            await processing.edit_text("❌ Could not fetch video info. Try refreshing `cookies.txt` or using a proxy.")
            return
        if info.get('_type') == 'playlist':
            entries = info.get('entries', [])
            if not entries:
                return await processing.edit_text("❌ Playlist empty.")
            pl_key = str(uuid.uuid4())[:8]
            PLAYLIST_CACHE[pl_key] = {'entries': entries, 'url': url, 'title': info.get('title', 'Playlist')}
            buttons = []
            for idx, entry in enumerate(entries[:20]):
                if entry is None: continue
                title = entry.get('title', f'Video {idx+1}')
                buttons.append([InlineKeyboardButton(f"{idx+1}. {title[:35]}...", callback_data=f"pl|{pl_key}|{idx}")])
            buttons.append([InlineKeyboardButton("📥 Download All (Video)", callback_data=f"pl_all_video|{pl_key}")])
            buttons.append([InlineKeyboardButton("🎵 Download All (Audio)", callback_data=f"pl_all_audio|{pl_key}")])
            buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
            await processing.edit_text(
                "**Playlist Detected**\n\nSelect a video or download all.",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            return
        # Single video – show Video/Audio buttons only
        await show_single_buttons(client, processing, url, info)
    except Exception as e:
        logger.exception("Error processing URL")
        await processing.edit_text(f"❌ Error: {e}")

async def show_single_buttons(client, message, url, info):
    """Show two buttons: Video Download (best quality) and Audio Download."""
    # Get best video format
    formats = info.get('formats', [])
    best_video_fmt = None
    best_audio_fmt = None
    for f in formats:
        if f.get('vcodec') != 'none' and f.get('acodec') != 'none':
            if not best_video_fmt or f.get('height', 0) > best_video_fmt.get('height', 0):
                best_video_fmt = f
        elif f.get('acodec') != 'none' and f.get('vcodec') == 'none':
            if not best_audio_fmt or f.get('filesize', 0) > best_audio_fmt.get('filesize', 0):
                best_audio_fmt = f
    # Generate key for cache
    key = str(uuid.uuid4())[:8]
    FORMAT_CACHE[key] = {'url': url, 'info': info}
    buttons = []
    if best_video_fmt:
        size = humanbytes(best_video_fmt.get('filesize') or best_video_fmt.get('filesize_approx') or 0)
        buttons.append([InlineKeyboardButton(f"📹 Video ({size})", callback_data=f"dl|{key}|{best_video_fmt['format_id']}|video")])
    else:
        # fallback: use bestvideo+bestaudio
        buttons.append([InlineKeyboardButton("📹 Video (Best)", callback_data=f"dl|{key}|bestvideo+bestaudio|video")])
    if best_audio_fmt:
        size = humanbytes(best_audio_fmt.get('filesize') or 0)
        buttons.append([InlineKeyboardButton(f"🎵 Audio ({size})", callback_data=f"dl|{key}|{best_audio_fmt['format_id']}|audio")])
    else:
        buttons.append([InlineKeyboardButton("🎵 Audio (Best)", callback_data=f"dl|{key}|bestaudio|audio")])
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
    title = info.get('title', 'Video').replace('<','(').replace('>',')')
    dur = get_duration(info.get('duration')) if info.get('duration') else 'N/A'
    await message.edit_text(f"**📺 {title}**\n⏱️ Duration: {dur}\n\nChoose:", reply_markup=InlineKeyboardMarkup(buttons))

# ==================== CALLBACKS ====================

@Client.on_callback_query(filters.regex(r"^dl\|"))
async def download_callback(client, cq):
    _, key, fmt_id, mode = cq.data.split("|")
    data = FORMAT_CACHE.get(key)
    if not data:
        return await cq.answer("Session expired. Resend link.", show_alert=True)
    url = data['url']
    status_msg = await cq.message.edit_text("⏳ Adding to queue...")
    async def prog(text):
        try: await status_msg.edit_text(text)
        except: pass
    user_id = cq.from_user.id
    user = await get_user(user_id)
    if not user or user.get("banned"):
        return await prog("❌ Banned.")
    today = await get_user_downloads_today(user_id)
    limit = user.get("daily_limit", Config.FREE_DAILY_LIMIT)
    if today >= limit:
        return await prog(f"⚠️ Daily limit reached ({limit}).")
    await client.queue.add_task(user_id, url, fmt_id, mode, prog, status_msg, cq.message)

@Client.on_callback_query(filters.regex(r"^pl\|"))
async def playlist_item(client, cq):
    parts = cq.data.split("|")
    if len(parts) < 3:
        return await cq.answer("Invalid data.", show_alert=True)
    pl_key, idx = parts[1], int(parts[2])
    pl = PLAYLIST_CACHE.get(pl_key)
    if not pl:
        return await cq.answer("Playlist expired.", show_alert=True)
    entries = pl['entries']
    if idx >= len(entries):
        return await cq.answer("Video not found.", show_alert=True)
    entry = entries[idx]
    if entry is None:
        return await cq.answer("Invalid video entry.", show_alert=True)
    video_id = entry.get('id')
    if not video_id:
        return await cq.answer("Invalid video.", show_alert=True)
    video_url = f"https://youtu.be/{video_id}"
    info_msg = await cq.message.reply_text("🔍 Fetching video info...")
    info = await get_video_info(video_url, Config.COOKIE_FILE, PROXY)
    if info:
        await show_single_buttons(client, info_msg, video_url, info)
        await cq.message.delete()
    else:
        await info_msg.edit_text("❌ Could not fetch video info.")

@Client.on_callback_query(filters.regex(r"^pl_all_video\|"))
async def playlist_all_video(client, cq): await playlist_all(client, cq, "video")
@Client.on_callback_query(filters.regex(r"^pl_all_audio\|"))
async def playlist_all_audio(client, cq): await playlist_all(client, cq, "audio")

async def playlist_all(client, cq, mode):
    parts = cq.data.split("|")
    if len(parts) < 2:
        return await cq.answer("Invalid data.", show_alert=True)
    pl_key = parts[1]
    pl = PLAYLIST_CACHE.get(pl_key)
    if not pl:
        return await cq.answer("Playlist expired.", show_alert=True)
    entries = pl['entries']
    if not entries:
        return await cq.answer("No entries.", show_alert=True)
    user_id = cq.from_user.id
    user = await get_user(user_id)
    if not user or user.get("banned"):
        return await cq.message.edit_text("❌ Banned.")
    today = await get_user_downloads_today(user_id)
    limit = user.get("daily_limit", Config.FREE_DAILY_LIMIT)
    rem = limit - today
    if rem <= 0:
        return await cq.message.edit_text(f"⚠️ Daily limit reached ({limit}).")
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
                except: pass
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
        await queue.add_task(user_id, video_url, "bestvideo+bestaudio" if mode=="video" else "bestaudio", mode, cb, None, None)
    if Config.CHANNEL_ID:
        try:
            link = await client.create_chat_invite_link(Config.CHANNEL_ID)
            await client.send_message(user_id, "🔔 **Join our channel for updates!**",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📢 Join Channel", url=link.invite_link)]]))
        except: pass

@Client.on_callback_query(filters.regex("cancel"))
async def cancel_callback(client, cq): await cq.message.delete()

# ==================== DOWNLOAD & UPLOAD HELPERS ====================

async def perform_download(user_id, url, fmt_id, mode, prog, status_msg=None, original_msg=None):
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
        for clients, use_cookies in [(["web"], True), (["android","ios"], False), (["android"], True)]:
            try:
                extra = None
                if use_cookies and clients == ["android"]:
                    extra = {"skip": ["webpage","configs","hls","dash"], "player_skip": ["webpage","configs"]}
                opts = build_ydl_opts(fmt, Config.COOKIE_FILE if use_cookies else None, PROXY, clients, extra)
                opts["outtmpl"] = out
                if mode=="audio":
                    opts["postprocessors"] = [{"key":"FFmpegExtractAudio","preferredcodec":"mp3","preferredquality":"192"}]
                if status_msg and original_msg:
                    start_time = time.time()
                    def progress_hook(d):
                        if d['status'] == 'downloading':
                            asyncio.create_task(download_progress_callback(
                                d.get('downloaded_bytes', 0),
                                d.get('total_bytes', d.get('total_bytes_estimate', 0)),
                                original_msg, start_time, status_msg
                            ))
                    opts["progress_hooks"] = [progress_hook]
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
        await prog(f"❌ All formats failed: {err}")
        raise Exception(err)
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
        _, _, thumb_path = await fix_thumb(thumb_path)
    return {"file_path": file_path, "thumb": thumb_path, "title": title, "duration": duration,
            "width": width, "height": height, "size": filesize}

async def upload_file(client, user_id, file_path, thumb, title, duration, width, height, mode, callback, status_msg=None, original_msg=None):
    settings = await get_user_settings(user_id)
    chat_id = settings.get("upload_chat_id") or user_id
    caption = (settings.get("caption") or f"📹 {title}\n📦 {humanbytes(os.path.getsize(file_path))}").replace('<','(').replace('>',')')
    thumb = settings.get("thumb_file_id") or thumb
    max_size = 2 * 1024 * 1024 * 1024
    file_size = os.path.getsize(file_path)
    try:
        if file_size > max_size:
            await callback("📦 File >2GB. Splitting...")
            base_name = os.path.splitext(file_path)[0]
            part_path = f"{base_name}.part."
            subprocess.run(["split", "-b", "2G", file_path, part_path], check=True)
            parts = sorted([f for f in os.listdir(Config.DOWNLOAD_DIR) if f.startswith(os.path.basename(part_path))])
            total_parts = len(parts)
            for idx, part_file in enumerate(parts, start=1):
                part_full = os.path.join(Config.DOWNLOAD_DIR, part_file)
                part_caption = f"📹 {title} (Part {idx}/{total_parts})\n📦 {humanbytes(os.path.getsize(part_full))}"
                if idx < total_parts: part_caption += "\n⬇️ Remaining parts..."
                else: part_caption += "\n✅ All parts sent."
                if mode == "audio":
                    await client.send_audio(chat_id, audio=part_full, caption=part_caption, duration=duration, thumb=thumb if thumb else None)
                else:
                    await client.send_video(chat_id, video=part_full, caption=part_caption, duration=duration,
                                            width=width, height=height, thumb=thumb if thumb else None, supports_streaming=True)
                os.remove(part_full)
        else:
            if status_msg and original_msg:
                start_time = time.time()
                progress_func = lambda c, t: asyncio.create_task(
                    upload_progress_callback(c, t, original_msg, start_time, status_msg)
                )
            else:
                progress_func = None
            if mode == "audio":
                await client.send_audio(chat_id, audio=file_path, caption=caption, duration=duration,
                                        thumb=thumb if thumb else None, progress=progress_func)
            else:
                await client.send_video(chat_id, video=file_path, caption=caption, duration=duration,
                                        width=width, height=height, thumb=thumb if thumb else None,
                                        supports_streaming=True, progress=progress_func)
        await callback("✅ Upload completed!")
    except Exception as e:
        await callback(f"❌ Upload failed: {e}")
        raise
    finally:
        if os.path.exists(file_path): os.remove(file_path)
        if thumb and isinstance(thumb, str) and os.path.exists(thumb): os.remove(thumb)
