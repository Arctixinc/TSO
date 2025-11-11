from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums.parse_mode import ParseMode
from Backend.helper.custom_filter import CustomFilters
from Backend.logger import LOGGER
from Backend import db

@Client.on_message(filters.command('genres') & filters.private & CustomFilters.owner)
async def genres_command(client: Client, message: Message):
    """
    Lists all unique genres available in the media library.
    """
    try:
        status_msg = await message.reply_text("- `Fetching genres...`", parse_mode=ParseMode.MARKDOWN)

        genres = set()
        total_storage_dbs = len(db.dbs) - 1
        for db_index in range(1, total_storage_dbs + 1):
            db_key = f"storage_{db_index}"

            movies = await db.dbs[db_key]["movie"].find({}, {"genres": 1}).to_list(None)
            for movie in movies:
                for genre in movie.get('genres', []):
                    genres.add(genre)

            tv_shows = await db.dbs[db_key]["tv"].find({}, {"genres": 1}).to_list(None)
            for tv_show in tv_shows:
                for genre in tv_show.get('genres', []):
                    genres.add(genre)

        if not genres:
            await status_msg.edit_text("🤷 No genres found in the database.", parse_mode=ParseMode.MARKDOWN)
            return

        response_text = "**- Available Genres**\n\n" + "\n".join(sorted(list(genres)))

        await status_msg.edit_text(response_text, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        LOGGER.error(f"Error in genres command: {e}")
        await message.reply_text(f"❌ **Error:** {e}", parse_mode=ParseMode.MARKDOWN)
