from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums.parse_mode import ParseMode
from Backend.helper.custom_filter import CustomFilters
from Backend.logger import LOGGER
from Backend import db
import asyncio

duplicates_to_delete = {}

@Client.on_message(filters.command('dbcleanup') & filters.private & CustomFilters.owner)
async def dbcleanup_command(client: Client, message: Message):
    """
    Finds and removes duplicate entries from the database.
    Usage: /dbcleanup
           /dbcleanup confirm
    """
    global duplicates_to_delete
    args = message.text.split(maxsplit=1)

    if len(args) > 1 and args[1].lower() == 'confirm':
        if not duplicates_to_delete:
            await message.reply_text("🤷 No duplicates found to delete. Run `/dbcleanup` first.", parse_mode=ParseMode.MARKDOWN)
            return

        status_msg = await message.reply_text(f"🗑️ Deleting {len(duplicates_to_delete)} duplicate entries...", parse_mode=ParseMode.MARKDOWN)
        deleted_count = 0
        for (db_key, media_type, tmdb_id), duplicates in duplicates_to_delete.items():
            for duplicate in duplicates:
                if media_type == 'movie':
                    await db.dbs[db_key]['movie'].delete_one({'_id': duplicate['_id']})
                else:
                    await db.dbs[db_key]['tv'].delete_one({'_id': duplicate['_id']})
                deleted_count += 1

        await status_msg.edit_text(f"✅ Successfully deleted {deleted_count} duplicate entries.", parse_mode=ParseMode.MARKDOWN)
        duplicates_to_delete = {}
        return

    status_msg = await message.reply_text("🔍 Finding duplicate entries...", parse_mode=ParseMode.MARKDOWN)

    duplicates_found = {}
    total_storage_dbs = len(db.dbs) - 1

    for db_index in range(1, total_storage_dbs + 1):
        db_key = f"storage_{db_index}"

        for media_type in ['movie', 'tv']:
            pipeline = [
                {"$group": {"_id": "$tmdb_id", "count": {"$sum": 1}, "docs": {"$push": "$_id"}}},
                {"$match": {"count": {"$gt": 1}}}
            ]
            async for duplicate in db.dbs[db_key][media_type].aggregate(pipeline):
                tmdb_id = duplicate['_id']
                docs_to_delete = duplicate['docs'][1:]

                if (db_key, media_type, tmdb_id) not in duplicates_found:
                    duplicates_found[(db_key, media_type, tmdb_id)] = []

                async for doc in db.dbs[db_key][media_type].find({'_id': {'$in': docs_to_delete}}):
                     duplicates_found[(db_key, media_type, tmdb_id)].append(doc)

    if not duplicates_found:
        await status_msg.edit_text("👍 No duplicate entries found in the database.", parse_mode=ParseMode.MARKDOWN)
        return

    duplicates_to_delete = duplicates_found
    response_text = f"**🔍 Found {len(duplicates_to_delete)} duplicate entries.**\n\n"

    for (db_key, media_type, tmdb_id), duplicates in duplicates_to_delete.items():
        response_text += f"**{media_type.title()}:** {duplicates[0]['title']} (TMDb ID: {tmdb_id}) - {len(duplicates)} duplicates\n"

    response_text += "\nTo delete these entries, please run `/dbcleanup confirm`."

    if len(response_text) > 4096:
        await status_msg.edit_text("📝 Output is too long. Sending as a file.", parse_mode=ParseMode.MARKDOWN)
        with open("dbcleanup_results.txt", "w") as f:
            f.write(response_text)
        await message.reply_document("dbcleanup_results.txt")
    else:
        await status_msg.edit_text(response_text, parse_mode=ParseMode.MARKDOWN)
