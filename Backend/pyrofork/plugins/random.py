from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums.parse_mode import ParseMode
from Backend.helper.custom_filter import CustomFilters
from Backend.logger import LOGGER
from Backend import db
import random

@Client.on_message(filters.command('random') & filters.private & CustomFilters.owner)
async def random_command(client: Client, message: Message):
    """
    Suggests a random movie or TV show to watch from the database.
    """
    try:
        status_msg = await message.reply_text("- `Picking a random media...`", parse_mode=ParseMode.MARKDOWN)

        total_movies = 0
        total_tv_shows = 0
        total_storage_dbs = len(db.dbs) - 1
        for db_index in range(1, total_storage_dbs + 1):
            db_key = f"storage_{db_index}"
            total_movies += await db.dbs[db_key]["movie"].count_documents({})
            total_tv_shows += await db.dbs[db_key]["tv"].count_documents({})

        media_type = random.choice(["movie", "tv"])
        if media_type == "movie":
            random_index = random.randint(0, total_movies - 1)
            for db_index in range(1, total_storage_dbs + 1):
                db_key = f"storage_{db_index}"
                count = await db.dbs[db_key]["movie"].count_documents({})
                if random_index < count:
                    random_movie = await db.dbs[db_key]["movie"].find().skip(random_index).limit(1).next()
                    response_text = f"**- Your Random Movie Is:**\n- `{random_movie['title']}`"
                    break
                random_index -= count
        else:
            random_index = random.randint(0, total_tv_shows - 1)
            for db_index in range(1, total_storage_dbs + 1):
                db_key = f"storage_{db_index}"
                count = await db.dbs[db_key]["tv"].count_documents({})
                if random_index < count:
                    random_tv_show = await db.dbs[db_key]["tv"].find().skip(random_index).limit(1).next()
                    response_text = f"**- Your Random TV Show Is:**\n- `{random_tv_show['title']}`"
                    break
                random_index -= count

        await status_msg.edit_text(response_text, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        LOGGER.error(f"Error in random command: {e}")
        await message.reply_text(f"❌ **Error:** {e}", parse_mode=ParseMode.MARKDOWN)
