from pyrogram import Client, filters
from pyrogram.types import Message, BotCommand
from Backend.helper.custom_filter import CustomFilters
import httpx
import os

COMMANDS = [
    BotCommand("webshot", "📸 Screenshot a website")
]

@Client.on_message(filters.command("webshot") & CustomFilters.owner)
async def webshot_command(client: Client, message: Message):
    if len(message.command) < 2:
        await message.reply_text("❗ **Usage:** `/webshot <url>`")
        return

    url = message.command[1]
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    status_msg = await message.reply_text("📸 **Taking screenshot...**")

    # Using a reliable public screenshot API wrapper (e.g. thum.io or screenshotapi.net demo)
    # screenshot-layer or similar often require keys.
    # thum.io is simplest for public use without key for small scale.
    # Format: https://image.thum.io/get/width/1280/crop/600/<url>

    api_url = f"https://image.thum.io/get/width/1280/crop/800/noanimate/{url}"

    try:
        async with httpx.AsyncClient() as http_client:
            response = await http_client.get(api_url, timeout=20)

            if response.status_code == 200:
                # Save to temp file
                file_path = f"webshot_{message.id}.jpg"
                with open(file_path, "wb") as f:
                    f.write(response.content)

                await message.reply_photo(
                    photo=file_path,
                    caption=f"📸 **Screenshot of:** `{url}`"
                )

                await status_msg.delete()
                os.remove(file_path)
            else:
                await status_msg.edit("❌ Failed to capture screenshot.")

    except Exception as e:
        await status_msg.edit(f"❌ Error: {e}")
