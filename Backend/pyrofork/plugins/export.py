from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums.parse_mode import ParseMode
from Backend.helper.custom_filter import CustomFilters
from Backend.logger import LOGGER
from Backend import db
import csv
import io

@Client.on_message(filters.command('exportdb') & filters.private & CustomFilters.owner)
async def exportdb_command(client: Client, message: Message):
    """
    Exports the entire media library to a CSV file.
    """
    try:
        status_msg = await message.reply_text("- `Exporting database...`", parse_mode=ParseMode.MARKDOWN)

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Type', 'Title', 'Year', 'TMDb ID', 'IMDb ID', 'Qualities', 'Seasons'])

        total_storage_dbs = len(db.dbs) - 1
        for db_index in range(1, total_storage_dbs + 1):
            db_key = f"storage_{db_index}"

            movies = await db.dbs[db_key]["movie"].find({}).to_list(None)
            for movie in movies:
                qualities = ", ".join([q['quality'] for q in movie.get('telegram', [])])
                writer.writerow(['Movie', movie['title'], movie['release_year'], movie['tmdb_id'], movie.get('imdb_id', 'N/A'), qualities, 'N/A'])

            tv_shows = await db.dbs[db_key]["tv"].find({}).to_list(None)
            for tv_show in tv_shows:
                seasons_info = []
                for season in tv_show.get('seasons', []):
                    seasons_info.append(f"S{season['season_number']}")
                writer.writerow(['TV Show', tv_show['title'], tv_show['release_year'], tv_show['tmdb_id'], tv_show.get('imdb_id', 'N/A'), 'N/A', ", ".join(seasons_info)])

        output.seek(0)

        await client.send_document(
            chat_id=message.chat.id,
            document=io.BytesIO(output.getvalue().encode()),
            file_name="media_library.csv",
            caption="Here is the exported media library."
        )
        await status_msg.delete()

    except Exception as e:
        LOGGER.error(f"Error in exportdb command: {e}")
        await message.reply_text(f"❌ **Error:** {e}", parse_mode=ParseMode.MARKDOWN)
