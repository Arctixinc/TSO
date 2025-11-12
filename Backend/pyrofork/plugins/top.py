from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums.parse_mode import ParseMode
from Backend.helper.custom_filter import CustomFilters
from Backend.logger import LOGGER
from Backend import db

@Client.on_message(filters.command('top') & filters.private & CustomFilters.owner)
async def top_command(client: Client, message: Message):
    """
    Fetches the top-rated movies or TV shows from the database.
    Usage: /top <movies|tv>
    """
    args = message.text.split(maxsplit=1)

    if len(args) > 1:
        if args[1].lower() == 'movies':
            status_msg = await message.reply_text("- `Fetching top movies...`", parse_mode=ParseMode.MARKDOWN)
            top_movies = await db.sort_movies([("rating", "desc")], 1, 10)
            response_text = "**- Top 10 Movies**\n\n"
            for movie in top_movies['movies']:
                response_text += f"- `{movie['title']}` - ⭐ {movie['rating']}\n"
            await status_msg.edit_text(response_text, parse_mode=ParseMode.MARKDOWN)
        elif args[1].lower() == 'tv':
            status_msg = await message.reply_text("- `Fetching top TV shows...`", parse_mode=ParseMode.MARKDOWN)
            top_tv_shows = await db.sort_tv_shows([("rating", "desc")], 1, 10)
            response_text = "**- Top 10 TV Shows**\n\n"
            for tv_show in top_tv_shows['tv_shows']:
                response_text += f"- `{tv_show['title']}` - ⭐ {tv_show['rating']}\n"
            await status_msg.edit_text(response_text, parse_mode=ParseMode.MARKDOWN)
        else:
            await message.reply_text("❗**Usage:** `/top <movies|tv>`", parse_mode=ParseMode.MARKDOWN)
    else:
        await message.reply_text("❗**Usage:** `/top <movies|tv>`", parse_mode=ParseMode.MARKDOWN)
