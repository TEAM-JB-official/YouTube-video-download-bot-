import asyncio
from collections import defaultdict
from config import Config
import logging

# Database functions
from database import get_user, get_user_downloads_today, add_download_history

# Download/upload functions from handlers.download
from handlers.download import perform_download, upload_file

logger = logging.getLogger(__name__)

class DownloadQueue:
    def __init__(self, max_workers=3):
        self.queue = asyncio.Queue()
        self.active = defaultdict(int)  # user_id -> active tasks count
        self.max_workers = max_workers
        self.workers = []
        self.running = False
        self.client = None  # will be set from main.py

    async def start(self):
        self.running = True
        self.workers = [asyncio.create_task(self._worker()) for _ in range(self.max_workers)]

    async def _worker(self):
        while self.running:
            task = await self.queue.get()
            user_id = task["user_id"]
            try:
                await self._process_task(task)
            except Exception as e:
                logger.exception(f"Worker error for user {user_id}: {e}")
                await task["callback"](f"❌ Error: {e}")
            finally:
                self.queue.task_done()
                self.active[user_id] -= 1

    async def _process_task(self, task):
        user_id = task["user_id"]
        user = await get_user(user_id)
        if not user or user.get("banned"):
            await task["callback"]("❌ You are banned.")
            return

        # Check daily limit
        today_downloads = await get_user_downloads_today(user_id)
        daily_limit = user.get("daily_limit", Config.FREE_DAILY_LIMIT)
        if today_downloads >= daily_limit:
            await task["callback"](f"⚠️ You've reached your daily limit of {daily_limit} downloads.")
            return

        callback = task["callback"]
        await callback("⬇️ Download started...")

        try:
            result = await perform_download(
                user_id=user_id,
                url=task["url"],
                format_id=task["format_id"],
                mode=task["mode"],
                progress_callback=callback
            )

            if result and result.get("file_path"):
                await callback("📤 Uploading...")
                # Use the stored client for sending files
                await upload_file(
                    client=self.client,
                    user_id=user_id,
                    file_path=result["file_path"],
                    thumb=result.get("thumb"),
                    title=result.get("title"),
                    duration=result.get("duration"),
                    width=result.get("width"),
                    height=result.get("height"),
                    mode=task["mode"],
                    callback=callback
                )
                # Record download history
                await add_download_history(user_id, task["url"], task["format_id"], result.get("size", 0))
                await callback("✅ Download and upload completed!")
            else:
                await callback("❌ Download failed – no file produced.")
        except Exception as e:
            logger.exception(f"Download error for user {user_id}: {e}")
            await callback(f"❌ Error: {e}")

    async def add_task(self, user_id, url, format_id, mode, callback):
        user = await get_user(user_id)
        if not user:
            await callback("❌ User not found. Please /start first.")
            return
        queue_limit = user.get("queue_limit", Config.FREE_QUEUE_LIMIT)
        if self.active[user_id] >= queue_limit:
            await callback(f"⚠️ You have reached your queue limit ({queue_limit}). Wait for existing downloads.")
            return
        self.active[user_id] += 1
        await self.queue.put({
            "user_id": user_id,
            "url": url,
            "format_id": format_id,
            "mode": mode,
            "callback": callback
        })
