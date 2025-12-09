from pyrogram import Client, filters
from pyrogram.types import Message, BotCommand
from Backend.helper.custom_filter import CustomFilters
import httpx

# Define Commands for dynamic registration
COMMANDS = [
    BotCommand("ud", "📖 Urban Dictionary lookup")
]

@Client.on_message(filters.command("ud") & CustomFilters.owner)
async def urbandict_command(client: Client, message: Message):
    if len(message.command) < 2:
        await message.reply_text("❗ **Usage:** `/ud <term>`")
        return

    term = message.text.split(maxsplit=1)[1]
    url = f"https://api.urbandictionary.com/v0/define?term={term}"

    try:
        async with httpx.AsyncClient() as http_client:
            response = await http_client.get(url)

            if response.status_code != 200:
                await message.reply_text("❌ Failed to fetch definition.")
                return

            data = response.json()
            items = data.get("list", [])

            if not items:
                await message.reply_text(f"❌ No definition found for **{term}**.")
                return

            # Get the top result
            result = items[0]
            definition = result.get("definition", "").replace("[", "").replace("]", "")
            example = result.get("example", "").replace("[", "").replace("]", "")
            permalink = result.get("permalink", "")
            author = result.get("author", "Unknown")

            # Truncate if too long
            if len(definition) > 1000:
                definition = definition[:1000] + "..."
            if len(example) > 500:
                example = example[:500] + "..."

            text = (
                f"📖 **Urban Dictionary: {term}**\n\n"
                f"**Definition:**\n{definition}\n\n"
                f"**Example:**\n_{example}_\n\n"
                f"✍️ **Author:** {author}\n"
                f"🔗 [Link]({permalink})"
            )

            await message.reply_text(text, disable_web_page_preview=True)

    except Exception as e:
        await message.reply_text(f"❌ Error: {e}")
