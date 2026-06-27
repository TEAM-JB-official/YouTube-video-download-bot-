import math
import time
from datetime import datetime
import pytz

def humanbytes(size):
    """
    Convert bytes to human-readable format (KiB, MiB, GiB, etc.)
    """
    if not size:
        return "0 B"
    power = 2 ** 10
    n = 0
    units = {0: '', 1: 'Ki', 2: 'Mi', 3: 'Gi', 4: 'Ti'}
    while size > power:
        size /= power
        n += 1
    return f"{round(size, 2)} {units[n]}B"

def get_duration(seconds):
    """
    Convert seconds to human-readable duration (e.g., 1h 30m 45s)
    """
    if not seconds:
        return "N/A"
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{int(hours)}h {int(minutes)}m {int(seconds)}s"
    elif minutes:
        return f"{int(minutes)}m {int(seconds)}s"
    else:
        return f"{int(seconds)}s"

def progress_bar(percentage, length=20):
    """
    Generate a styled progress bar.
    Example: ████████░░░░░░░░░░ 40.0%
    """
    filled = int(length * percentage / 100)
    bar = "█" * filled + "░" * (length - filled)
    return bar

async def progress_callback(current, total, message, start_time):
    """
    Legacy progress callback – used by older functions.
    Kept for compatibility.
    """
    if total == 0:
        return
    percent = current * 100 / total
    elapsed = time.time() - start_time
    speed = current / elapsed if elapsed else 0
    eta = (total - current) / speed if speed else 0
    eta_str = get_duration(eta)
    progress_text = (
        f"📥 **Downloading...**\n"
        f"📦 {humanbytes(current)} / {humanbytes(total)}\n"
        f"⚡ {humanbytes(speed)}/s\n"
        f"⏳ ETA: {eta_str}\n"
        f"🔄 {percent:.1f}%"
    )
    if int(elapsed) % 2 == 0:
        try:
            await message.edit_text(progress_text)
        except:
            pass

def get_ist_time():
    """
    Return current time in IST (Indian Standard Time) as a formatted string.
    """
    return datetime.now(pytz.timezone('Asia/Kolkata')).strftime("%Y-%m-%d %H:%M:%S")
