import asyncio
import logging
from pyrogram import Client
from config import Config
from database import connect_db
from queue_manager import DownloadQueue

# Set up logging
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
    # Connect to DB
    await connect_db()
    logger.info("Connected to MongoDB")

    # Start queue with 2 workers to reduce memory/CPU load
    queue = DownloadQueue(max_workers=2)
    await queue.start()
    logger.info("Download queue started")

    # Create client
    app = Client(
        "yt_bot",
        api_id=Config.API_ID,
        api_hash=Config.API_HASH,
        bot_token=Config.BOT_TOKEN,
        plugins=dict(root="handlers")
    )
    app.queue = queue  # make queue accessible in handlers

    logger.info("Bot starting...")
    await app.start()
    logger.info("Bot started!")

    # Keep running
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
