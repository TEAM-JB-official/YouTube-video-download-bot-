import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Telegram
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    API_ID = int(os.getenv("API_ID", "25331263"))
    API_HASH = os.getenv("API_HASH", "cab85305bf85125a2ac053210bcd1030")

    # MongoDB
    MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://rs92573993688:pVf4EeDuRi2o92ex@cluster0.9u29q.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")
    DB_NAME = os.getenv("DB_NAME", "yt_bot")
    
    # Force Subscribe Channel ID (must be integer, e.g., -100123456)
    CHANNEL_ID = os.getenv("CHANNEL_ID")
    if CHANNEL_ID:
        CHANNEL_ID = int(CHANNEL_ID)

    # Free limits
    FREE_DAILY_LIMIT = int(os.getenv("FREE_DAILY_LIMIT", 5))
    FREE_FILE_SIZE_MB = int(os.getenv("FREE_FILE_SIZE_MB", 1900))
    FREE_QUEUE_LIMIT = int(os.getenv("FREE_QUEUE_LIMIT", 3))
    
    # Premium plans (size in MB, daily limit, queue limit)
    PREMIUM_PLANS = {
        "monthly": {"size_mb": 2048, "daily_limit": 100, "queue_limit": 5},
        "yearly":  {"size_mb": 4096, "daily_limit": 1000, "queue_limit": 10}
    }

    # Directories
    DOWNLOAD_DIR = "downloads/"
    COOKIE_FILE = "cookies.txt"

    # Proxy (optional)
    HTTP_PROXY = os.getenv("HTTP_PROXY")
