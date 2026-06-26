import asyncio
import logging
from pyrogram import Client
from config import Config
from database import connect_db
from queue_manager import DownloadQueue

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

async def main():
    await connect_db()
    logger.info("Connected to MongoDB")

    # 3 workers = up to 3 parallel downloads
    queue = DownloadQueue(max_workers=3)
    await queue.start()
    logger.info("Download queue started")

    app = Client(
        "yt_bot",
        api_id=Config.API_ID,
        api_hash=Config.API_HASH,
        bot_token=Config.BOT_TOKEN,
        plugins=dict(root="handlers")
    )
    app.queue = queue
    queue.client = app

    logger.info("Bot starting...")
    await app.start()
    logger.info("Bot started!")

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
