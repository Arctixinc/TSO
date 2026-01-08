import asyncio
from pyrogram import Client
from pyrogram.errors import FloodWait, SessionPasswordNeeded, PhoneCodeExpired
from Backend.config import Telegram
from Backend.logger import LOGGER
from Backend import db
from Backend.helper.metadata import metadata
from Backend.helper.pyro import clean_filename

class ScrapperService:
    user_client: Client = None

    @classmethod
    async def start_user_client(cls):
        """Initializes the User Client using the session string."""
        if not Telegram.USER_SESSION_STRING:
            LOGGER.warning("⚠️ No USER_SESSION_STRING found. Scrapper functionality will be disabled.")
            return

        try:
            cls.user_client = Client(
                "scrapper_session",
                api_id=Telegram.API_ID,
                api_hash=Telegram.API_HASH,
                session_string=Telegram.USER_SESSION_STRING,
                no_updates=True,  # We only need to fetch messages, not receive updates
                in_memory=True
            )
            await cls.user_client.start()
            me = await cls.user_client.get_me()
            LOGGER.info(f"✅ Scrapper User Client Started: {me.first_name} (@{me.username})")
        except Exception as e:
            LOGGER.error(f"❌ Failed to start Scrapper User Client: {e}")
            cls.user_client = None

    @classmethod
    async def stop_user_client(cls):
        if cls.user_client:
            await cls.user_client.stop()

    @classmethod
    async def scan_sources(cls, status_msg=None):
        """
        Scans all configured source channels for new media.
        """
        from pyrogram.types import Message

        if not cls.user_client:
            LOGGER.error("❌ User Client not initialized. Cannot scan.")
            if status_msg: await status_msg.edit_text("❌ User Client not initialized.")
            return

        # Fetch sources from DB
        db_sources = await db.get_source_channels()
        # Merge with Config sources (unique)
        all_sources = list(set(db_sources + Telegram.SOURCE_CHANNELS))

        if not all_sources:
            LOGGER.info("ℹ️ No source channels configured.")
            if status_msg: await status_msg.edit_text("ℹ️ No source channels configured.")
            return

        LOGGER.info(f"🚀 Starting Scrapper Scan on {len(all_sources)} channels...")
        if status_msg: await status_msg.edit_text(f"🚀 Starting Scrapper Scan on {len(all_sources)} channels...")

        # Ensure we have a valid auth channel destination
        if not Telegram.AUTH_CHANNEL:
             LOGGER.error("❌ No AUTH_CHANNEL configured in environment.")
             if status_msg: await status_msg.edit_text("❌ No AUTH_CHANNEL configured.")
             return

        dest_chat_id = int(Telegram.AUTH_CHANNEL[0])

        total_scanned = 0
        total_copied = 0

        import time
        last_update_time = time.time()

        for channel_id in all_sources:
            try:
                # Update status for channel switch
                if status_msg:
                    await status_msg.edit_text(
                        f"🔄 **Processing Channel:** `{channel_id}`\n"
                        f"📊 Scanned: {total_scanned}\n"
                        f"📤 Copied: {total_copied}"
                    )

                scanned, copied = await cls.process_channel(channel_id, dest_chat_id, status_msg, total_scanned, total_copied)
                total_scanned += scanned
                total_copied += copied

            except Exception as e:
                LOGGER.error(f"❌ Error processing channel {channel_id}: {e}")

        LOGGER.info("✅ Scrapper Scan Completed.")
        if status_msg:
            await status_msg.edit_text(
                f"✅ **Scrapper Scan Completed**\n\n"
                f"📊 Total Scanned: {total_scanned}\n"
                f"📤 Total Copied: {total_copied}"
            )

    @classmethod
    async def process_channel(cls, channel_id: int, dest_chat_id: int, status_msg, global_scanned, global_copied):
        last_id = await db.get_scrapper_cursor(channel_id)

        import time
        last_update = time.time()

        local_scanned = 0
        local_copied = 0

        # If no cursor, start from latest - 2000 (approx)
        if last_id == 0:
            try:
                async for m in cls.user_client.get_chat_history(channel_id, limit=1):
                    latest_id = m.id
                    break
                else:
                    latest_id = 0

                last_id = max(1, latest_id - 2000)
                LOGGER.info(f"⚠️ No cursor for {channel_id}. Starting from {last_id}")
            except Exception as e:
                LOGGER.error(f"Failed to get history for {channel_id}: {e}")
                return 0, 0

        current_id = last_id

        batch_size = 200
        # Determine strict limit first to prevent runaway cursor
        top_id = 0
        try:
            async for m in cls.user_client.get_chat_history(channel_id, limit=1):
                top_id = m.id
                break
        except Exception as e:
            LOGGER.error(f"Failed to get top_id for {channel_id}: {e}")
            return 0, 0

        while current_id < top_id:
            start = current_id + 1
            end = start + batch_size

            # Clamp to top_id
            if end > top_id + 1:
                end = top_id + 1

            ids = list(range(start, end))
            if not ids:
                break

            try:
                messages = await cls.user_client.get_messages(channel_id, ids)
            except FloodWait as e:
                LOGGER.warning(f"FloodWait in scrapper: {e.value}s")
                await asyncio.sleep(e.value + 2)
                continue
            except Exception as e:
                LOGGER.error(f"Error fetching batch {start}-{end} for {channel_id}: {e}")
                break

            found_messages = [m for m in messages if m and not m.empty]

            # Update cursor to the end of this batch regardless of emptiness
            # because we know top_id > current_id, so these IDs are 'checked' (even if deleted)
            current_id = ids[-1]

            for msg in messages:
                if not msg or msg.empty:
                    continue

                # Logic: Filter & Copy
                if await cls.process_message(msg, channel_id, dest_chat_id):
                    local_copied += 1

                local_scanned += 1

            # Progress Update (Every 5 seconds)
            if status_msg and (time.time() - last_update > 5):
                try:
                    await status_msg.edit_text(
                        f"🔄 **Processing Channel:** `{channel_id}`\n"
                        f"📨 Batch: {start}-{end}\n"
                        f"📊 Total Scanned: {global_scanned + local_scanned}\n"
                        f"📤 Total Copied: {global_copied + local_copied}"
                    )
                    last_update = time.time()
                except Exception:
                    pass

            await db.update_scrapper_cursor(channel_id, current_id)
            await asyncio.sleep(2)

        return local_scanned, local_copied

    @classmethod
    async def process_message(cls, msg, source_channel_id, dest_chat_id):
        # 1. Filter Non-Media
        if not (msg.video or (msg.document and msg.document.mime_type and msg.document.mime_type.startswith("video/"))):
            return

        file = msg.video or msg.document
        # Use caption or filename
        raw_filename = file.file_name or ""
        # Sometimes file_name is None, try caption?
        # Metadata parser uses clean_filename(title).
        title = msg.caption or raw_filename

        if not title:
            return

        cleaned_title = clean_filename(title)

        # 2. Parse & Validate
        # We need metadata to check duplicates and quality
        meta = await metadata(cleaned_title, source_channel_id, msg.id)

        if not meta:
            # Parsing failed or invalid
            return

        tmdb_id = meta.get('tmdb_id')
        media_type = meta.get('media_type')
        new_quality = meta.get('quality')
        season = meta.get('season_number')
        episode = meta.get('episode_number')

        if not tmdb_id:
            return

        # 3. Duplicate Check
        # Get existing document
        doc = await db.get_document(media_type, int(tmdb_id), db_index=1) # Need to check all DBs?
        # db.get_document checks specific index.
        # db.get_media checks ALL dbs. Use get_media-like logic or search_documents logic?
        # db.get_document uses "storage_index", we don't know which index it is in.
        # But `db.get_media` checks duplicate by FILE ID (telegram unique id). We are forbidden from using that.
        # We must find by TMDB ID.

        # We need a method to find document by TMDB ID across all DBs.
        # `db.get_document` takes db_index.
        # We can reuse logic from `update_movie`/`update_tv_show` which searches all DBs.
        # Let's write a quick helper here or just iterate.

        found_doc = None
        total_dbs = len(db.dbs) - 1 # excluding tracking

        for i in range(1, total_dbs + 1):
             d = await db.get_document(media_type, int(tmdb_id), i)
             if d:
                 found_doc = d
                 break

        should_copy = False

        if not found_doc:
            # New Content -> Copy
            should_copy = True
            LOGGER.info(f"✅ [Scrapper] New Content: {cleaned_title} (TMDB: {tmdb_id})")
        else:
            # Exists -> Check Quality
            if media_type == 'movie':
                existing_qualities = [q.get('quality') for q in found_doc.get('telegram', [])]
                if new_quality not in existing_qualities:
                    should_copy = True
                    LOGGER.info(f"✅ [Scrapper] New Quality for Movie: {cleaned_title} ({new_quality})")
                else:
                    LOGGER.info(f"⏭️ [Scrapper] Duplicate Quality for Movie: {cleaned_title} ({new_quality})")

            elif media_type == 'tv':
                # Navigate to specific episode
                # Structure: doc['seasons'] -> list
                seasons = found_doc.get('seasons', [])
                target_season = next((s for s in seasons if s.get('season_number') == season), None)

                if target_season:
                    episodes = target_season.get('episodes', [])
                    target_ep = next((e for e in episodes if e.get('episode_number') == episode), None)

                    if target_ep:
                        existing_qualities = [q.get('quality') for q in target_ep.get('telegram', [])]
                        if new_quality not in existing_qualities:
                            should_copy = True
                            LOGGER.info(f"✅ [Scrapper] New Quality for TV: {cleaned_title} S{season}E{episode} ({new_quality})")
                        else:
                            LOGGER.info(f"⏭️ [Scrapper] Duplicate Quality for TV: {cleaned_title} S{season}E{episode} ({new_quality})")
                    else:
                        # New Episode in existing season -> Copy
                        should_copy = True
                        LOGGER.info(f"✅ [Scrapper] New Episode: {cleaned_title} S{season}E{episode}")
                else:
                    # New Season -> Copy
                    should_copy = True
                    LOGGER.info(f"✅ [Scrapper] New Season: {cleaned_title} S{season}")

        if should_copy:
            try:
                await cls.user_client.copy_message(
                    chat_id=dest_chat_id,
                    from_chat_id=source_channel_id,
                    message_id=msg.id
                )
                LOGGER.info(f"📤 Copied message {msg.id} from {source_channel_id} to Auth Channel")
                await asyncio.sleep(1) # Rate limit
                return True
            except FloodWait as e:
                LOGGER.warning(f"FloodWait copying message: {e.value}s")
                await asyncio.sleep(e.value + 2)
                # Retry once?
                try:
                    await cls.user_client.copy_message(
                        chat_id=dest_chat_id,
                        from_chat_id=source_channel_id,
                        message_id=msg.id
                    )
                    return True
                except:
                    LOGGER.error(f"Failed to copy message {msg.id} after retry")
            except Exception as e:
                LOGGER.error(f"Failed to copy message {msg.id}: {e}")
        return False
