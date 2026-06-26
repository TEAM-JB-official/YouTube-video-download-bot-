from pyrogram import Client, filters

@Client.on_message(filters.private & filters.command("cancel"))
async def cancel_command(client, message):
    user_id = message.from_user.id
    queue = client.queue
    if queue.cancel_user(user_id):
        await message.reply_text("✅ Your pending downloads have been cancelled.")
    else:
        await message.reply_text("❌ You have no active downloads to cancel.")
