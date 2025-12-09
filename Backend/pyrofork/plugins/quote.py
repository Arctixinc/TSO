from pyrogram import Client, filters
from pyrogram.types import Message, BotCommand
from Backend.helper.custom_filter import CustomFilters
import httpx

COMMANDS = [
    BotCommand("quote", "💬 Get a random quote")
]

@Client.on_message(filters.command("quote") & CustomFilters.owner)
async def quote_command(client: Client, message: Message):
    url = "https://api.quotable.io/random"

    try:
        async with httpx.AsyncClient() as http_client:
            # Quotable API can be flaky with SSL sometimes, verify=False is a fallback if needed but try standard first
            response = await http_client.get(url, timeout=10)

            if response.status_code != 200:
                await message.reply_text("❌ Failed to fetch quote.")
                return

            data = response.json()
            content = data.get("content")
            author = data.get("author")
            tags = ", ".join(data.get("tags", []))

            text = (
                f"❝ **{content}** ❞\n\n"
                f"✍️ — *{author}*\n"
                f"🏷️ `{tags}`"
            )

            await message.reply_text(text)

    except Exception as e:
        await message.reply_text(f"❌ Error: {e}")
