import io
import asyncio
from pyrogram import filters, Client
from pyrogram.types import Message
from pyrogram.enums.parse_mode import ParseMode

from Backend.helper.custom_filter import CustomFilters
from Backend.logger import LOGGER
from Backend import db


async def check_messages_in_batches(client, links_to_check):
    valid_links = []
    broken_links = []

    tasks = []
    for chat_id, messages in links_to_check.items():
        message_ids = [msg['msg_id'] for msg in messages]
        tasks.append(client.get_messages(chat_id, message_ids))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    for (chat_id, messages), result in zip(links_to_check.items(), results):
        if isinstance(result, Exception):
            LOGGER.error(f"Error fetching messages for chat {chat_id}: {result}")
            for msg_info in messages:
                msg_info['error'] = str(result)
                broken_links.append(msg_info)
            continue

        valid_message_ids = {msg.id for msg in result if msg and (msg.video or msg.document)}

        for msg_info in messages:
            if msg_info['msg_id'] in valid_message_ids:
                valid_links.append(msg_info)
            else:
                msg_info['error'] = "Message not found or invalid"
                broken_links.append(msg_info)

    return valid_links, broken_links


@Client.on_message(filters.command('cleanup') & filters.private & CustomFilters.owner, group=10)
async def cleanup_broken_links(client: Client, message: Message):
    try:
        args = message.text.split()
        delete_mode = len(args) > 1 and args[1].lower() == "delete"

        mode_text = "🧹 Cleanup Mode (deleting broken entries...)" if delete_mode else "🔍 Scan Mode (report only)"
        status_msg = await message.reply_text(
            f"{mode_text}\n\n📊 Collecting all database entries...\n⏳ This may take a while...",
            parse_mode=ParseMode.MARKDOWN
        )

        from Backend.helper.encrypt import decode_string
        total_storage_dbs = len(db.dbs) - 1

        links_to_check = {}
        all_media = []
        total_movies = 0
        total_tv = 0

        for db_index in range(1, total_storage_dbs + 1):
            db_key = f"storage_{db_index}"

            movies = await db.dbs[db_key]["movie"].find({}).to_list(None)
            total_movies += len(movies)
            all_media.extend([(db_key, "movie", m) for m in movies])

            shows = await db.dbs[db_key]["tv"].find({}).to_list(None)
            total_tv += len(shows)
            all_media.extend([(db_key, "tv", s) for s in shows])

        for db_key, media_type, media in all_media:
            if media_type == "movie":
                for quality in media.get("telegram", []):
                    decoded = await decode_string(quality["id"])
                    chat_id = int(f"-100{decoded['chat_id']}")
                    msg_id = int(decoded["msg_id"])
                    if chat_id not in links_to_check:
                        links_to_check[chat_id] = []
                    links_to_check[chat_id].append({
                        "id": quality["id"], "db_key": db_key, "media_type": "movie",
                        "tmdb_id": media.get("tmdb_id"), "title": media.get("title"),
                        "quality": quality.get("quality"), "chat_id": chat_id, "msg_id": msg_id
                    })
            else: # TV Show
                for season in media.get("seasons", []):
                    for episode in season.get("episodes", []):
                        for quality in episode.get("telegram", []):
                            decoded = await decode_string(quality["id"])
                            chat_id = int(f"-100{decoded['chat_id']}")
                            msg_id = int(decoded["msg_id"])
                            if chat_id not in links_to_check:
                                links_to_check[chat_id] = []
                            links_to_check[chat_id].append({
                                "id": quality["id"], "db_key": db_key, "media_type": "tv",
                                "tmdb_id": media.get("tmdb_id"), "title": media.get("title"),
                                "season": season.get("season_number"), "episode": episode.get("episode_number"),
                                "quality": quality.get("quality"), "chat_id": chat_id, "msg_id": msg_id
                            })

        await status_msg.edit_text(
            f"{mode_text}\n\n"
            f"📊 Found {sum(len(m) for m in links_to_check.values())} total links to check.\n"
            f"🚀 Starting concurrent check...",
            parse_mode=ParseMode.MARKDOWN
        )

        _, broken_entries = await check_messages_in_batches(client, links_to_check)

        total_deleted = 0
        if delete_mode and broken_entries:
            await status_msg.edit_text(
                f"🧹 Deleting {len(broken_entries)} broken entries...",
                parse_mode=ParseMode.MARKDOWN
            )

            updates = {}
            for entry in broken_entries:
                key = (entry['db_key'], entry['media_type'], entry['tmdb_id'])
                if key not in updates:
                    media_obj = await db.dbs[entry['db_key']][entry['media_type']].find_one({"tmdb_id": entry['tmdb_id']})
                    updates[key] = media_obj

                if updates[key]:
                    if entry['media_type'] == 'movie':
                        updates[key]['telegram'] = [q for q in updates[key].get('telegram', []) if q['id'] != entry['id']]
                    else:
                        for s in updates[key].get('seasons', []):
                            if s['season_number'] == entry['season']:
                                for ep in s.get('episodes', []):
                                    if ep['episode_number'] == entry['episode']:
                                        ep['telegram'] = [q for q in ep.get('telegram', []) if q['id'] != entry['id']]

            for (db_key, media_type, tmdb_id), media in updates.items():
                if media_type == 'movie':
                    if media['telegram']:
                        await db.dbs[db_key]['movie'].update_one({"tmdb_id": tmdb_id}, {"$set": {"telegram": media['telegram']}})
                    else:
                        await db.dbs[db_key]['movie'].delete_one({"tmdb_id": tmdb_id})
                    total_deleted += 1
                else:
                    # Prune empty episodes/seasons
                    for s in media.get('seasons', []):
                        s['episodes'] = [ep for ep in s.get('episodes', []) if ep.get('telegram')]
                    media['seasons'] = [s for s in media.get('seasons', []) if s.get('episodes')]

                    if media['seasons']:
                        await db.dbs[db_key]['tv'].update_one({"tmdb_id": tmdb_id}, {"$set": {"seasons": media['seasons']}})
                    else:
                        await db.dbs[db_key]['tv'].delete_one({"tmdb_id": tmdb_id})
                    total_deleted += 1


        summary_header = "🧹 **Cleanup Completed!**" if delete_mode else "✅ **Scan Completed!**"
        summary = (
            f"{summary_header}\n\n"
            f"📊 Total Checked: `{sum(len(m) for m in links_to_check.values())}`\n"
            f"❌ Broken Links: `{len(broken_entries)}`\n"
            f"🗑️ {'Deleted' if delete_mode else 'Would Delete'} Media Objects: `{total_deleted}`\n"
            f"🎬 Movies: `{total_movies}`\n"
            f"📺 TV Shows: `{total_tv}`\n\n"
        )

        if broken_entries:
            summary += "**Top 10 Broken Entries:**\n"
            for i, entry in enumerate(broken_entries[:10]):
                if entry["media_type"] == "movie":
                    summary += f"{i+1}. 🎬 {entry['title']} ({entry.get('quality','N/A')})\n"
                else:
                    summary += f"{i+1}. 📺 {entry['title']} S{entry.get('season')}E{entry.get('episode')} ({entry.get('quality','N/A')})\n"
            if len(broken_entries) > 10:
                summary += f"\n...and `{len(broken_entries) - 10}` more.\n"

        await status_msg.edit_text(summary, parse_mode=ParseMode.MARKDOWN)

        if broken_entries:
            log_buffer = io.StringIO()
            log_buffer.write(f"{'CLEANUP' if delete_mode else 'SCAN'} REPORT\n{'=' * 60}\n\n")
            for i, entry in enumerate(broken_entries, start=1):
                log_buffer.write(
                    f"{i}. [{'MOVIE' if entry['media_type']=='movie' else 'TV'}] "
                    f"{entry['title']} | {entry.get('quality','N/A')} | "
                    f"DB: {entry['db_key']} | Error: {entry.get('error','-')}\n"
                )
            log_buffer.write(f"\n--- SUMMARY ---\nChecked: {sum(len(m) for m in links_to_check.values())}\nBroken: {len(broken_entries)}\nDeleted: {total_deleted}\n")
            log_buffer.seek(0)

            await client.send_document(
                chat_id=message.chat.id,
                document=io.BytesIO(log_buffer.getvalue().encode()),
                file_name=f"{'cleanup' if delete_mode else 'scan'}_report.txt",
                caption=f"🧾 {'Cleanup' if delete_mode else 'Scan'} Report Log",
            )
            log_buffer.close()

    except Exception as e:
        LOGGER.error(f"Error in cleanup: {e}", exc_info=True)
        await message.reply_text(f"❌ Error: {e}")
