import asyncio
from collections import defaultdict
from config import Config
import logging

from database import get_user, get_user_downloads_today, add_download_history
from handlers.download import perform_download, upload_file

logger = logging.getLogger(__name__)

class DownloadQueue:
    def __init__(self, max_workers=3):
        self.queue = asyncio.Queue()
        self.active = defaultdict(int)          # user_id -> active tasks count
        self.cancel_events = {}                 # user_id -> asyncio.Event
        self.max_workers = max_workers
        self.workers = []
        self.running = False
        self.client = None

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
        callback = task["callback"]

        # Check if user has cancelled
        if self._is_cancelled(user_id):
            self._clear_cancel(user_id)
            await callback("⏹️ Cancelled by user.")
            return

        user = await get_user(user_id)
        if not user or user.get("banned"):
            await callback("❌ You are banned.")
            return

        # Check daily limit
        today_downloads = await get_user_downloads_today(user_id)
        daily_limit = user.get("daily_limit", Config.FREE_DAILY_LIMIT)
        if today_downloads >= daily_limit:
            await callback(f"⚠️ Daily limit reached ({daily_limit}).")
            return

        await callback("⬇️ Download started...")

        try:
            result = await perform_download(
                user_id=user_id,
                url=task["url"],
                format_id=task["format_id"],
                mode=task["mode"],
                progress_callback=callback
            )

            # Check cancellation again after download
            if self._is_cancelled(user_id):
                self._clear_cancel(user_id)
                # Cleanup downloaded files
                if result and result.get("file_path"):
                    try: os.remove(result["file_path"])
                    except: pass
                    if result.get("thumb"):
                        try: os.remove(result["thumb"])
                        except: pass
                await callback("⏹️ Download cancelled after completion (file not uploaded).")
                return

            if result and result.get("file_path"):
                await callback("📤 Uploading...")
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
                await add_download_history(user_id, task["url"], task["format_id"], result.get("size", 0))
                await callback("✅ Download and upload completed!")
            else:
                await callback("❌ Download failed – no file produced.")
        except Exception as e:
            logger.exception(f"Download error for user {user_id}: {e}")
            await callback(f"❌ Error: {e}")

    def _is_cancelled(self, user_id):
        """Return True if the user has an active cancellation event."""
        event = self.cancel_events.get(user_id)
        return event is not None and event.is_set()

    def _clear_cancel(self, user_id):
        """Remove and clear cancellation event for user."""
        if user_id in self.cancel_events:
            self.cancel_events[user_id].clear()
            del self.cancel_events[user_id]

    def cancel_user(self, user_id):
        """
        Cancel all pending and active tasks for the user.
        Returns True if any task was cancelled.
        """
        # Set cancellation event
        if user_id not in self.cancel_events:
            self.cancel_events[user_id] = asyncio.Event()
        self.cancel_events[user_id].set()

        # Remove queued tasks for this user (they will be skipped when popped)
        # We cannot remove from queue easily, but we'll just let them be skipped
        # because _is_cancelled will return True.
        # Return True if there was at least one task (active or queued)
        has_active = self.active.get(user_id, 0) > 0
        has_queued = any(t["user_id"] == user_id for t in self.queue._queue)
        return has_active or has_queued

    async def add_task(self, user_id, url, format_id, mode, callback):
        # Clear any stale cancellation for this user
        if user_id in self.cancel_events:
            self.cancel_events[user_id].clear()
            del self.cancel_events[user_id]

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
