from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime, timedelta
from config import Config
import pytz

client = None
db = None

async def connect_db():
    global client, db
    client = AsyncIOMotorClient(Config.MONGO_URI)
    db = client[Config.DB_NAME]
    # Create indexes
    await db.users.create_index("user_id", unique=True)

async def get_user(user_id: int):
    return await db.users.find_one({"user_id": user_id})

async def update_user(user_id: int, data: dict, upsert=True):
    return await db.users.update_one(
        {"user_id": user_id},
        {"$set": data},
        upsert=upsert
    )

async def add_download_history(user_id: int, url: str, format_: str, size: int):
    await db.download_history.insert_one({
        "user_id": user_id,
        "url": url,
        "format": format_,
        "size": size,
        "timestamp": datetime.now(pytz.UTC)
    })

async def get_user_downloads_today(user_id: int):
    today_start = datetime.now(pytz.UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return await db.download_history.count_documents({
        "user_id": user_id,
        "timestamp": {"$gte": today_start}
    })

async def get_all_users():
    return await db.users.find().to_list(length=None)

async def count_users():
    return await db.users.count_documents({})

async def count_premium_users():
    now = datetime.now(pytz.UTC)
    return await db.users.count_documents({
        "is_premium": True,
        "plan_expiry": {"$gt": now}
    })

async def get_premium_users():
    now = datetime.now(pytz.UTC)
    cursor = db.users.find({"is_premium": True, "plan_expiry": {"$gt": now}})
    return await cursor.to_list(length=None)

async def add_premium(user_id: int, plan: str, days: int, added_by: int):
    expiry = datetime.now(pytz.UTC) + timedelta(days=days)
    plan_data = Config.PREMIUM_PLANS.get(plan, Config.PREMIUM_PLANS["monthly"])
    await update_user(user_id, {
        "is_premium": True,
        "plan": plan,
        "plan_expiry": expiry,
        "size_limit_mb": plan_data["size_mb"],
        "daily_limit": plan_data["daily_limit"],
        "queue_limit": plan_data["queue_limit"],
        "premium_added_by": added_by,
        "premium_added_on": datetime.now(pytz.UTC)
    })

async def remove_premium(user_id: int):
    await update_user(user_id, {
        "is_premium": False,
        "plan_expiry": None,
        "size_limit_mb": Config.FREE_FILE_SIZE_MB,
        "daily_limit": Config.FREE_DAILY_LIMIT,
        "queue_limit": Config.FREE_QUEUE_LIMIT
    })

async def get_user_settings(user_id: int):
    user = await get_user(user_id)
    if not user:
        return {}
    return user.get("settings", {})

async def update_user_settings(user_id: int, settings: dict):
    await update_user(user_id, {"settings": settings})

async def ban_user(user_id: int):
    await update_user(user_id, {"banned": True})

async def unban_user(user_id: int):
    await update_user(user_id, {"banned": False})
