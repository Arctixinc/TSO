import asyncio
from pyrogram import Client
from pyrogram.errors import FloodWait
from Backend.config import Telegram
from Backend.logger import LOGGER
from Backend import db
from Backend.helper.metadata import metadata
from Backend.helper.pyro import clean_filename


class ScrapperService:
    user_client: Client = None

    # ==================================================
    # USER CLIENT
    # ==================================================

    @classmethod
    async def start_user_client(cls):
        if not Telegram.USER_SESSION_STRING:
            LOGGER.warning("⚠️ No USER_SESSION_STRING found. Scrapper disabled.")
            return

        try:
            cls.user_client = Client(
                "scrapper_session",
                api_id=Telegram.API_ID,
                api_hash=Telegram.API_HASH,
                session_string=Telegram.USER_SESSION_STRING,
                no_updates=True,
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

    # ==================================================
    # MAIN SCAN
    # ==================================================

    @classmethod
    async def scan_sources(cls, status_msg=None):
        if not cls.user_client:
            LOGGER.error("❌ User Client not initialized.")
            if status_msg:
                await status_msg.edit_text("❌ User Client not initialized.")
            return

        db_sources = await db.get_source_channels()
        all_sources = list(set(db_sources + Telegram.SOURCE_CHANNELS))

        if not all_sources:
            LOGGER.info("ℹ️ No source channels configured.")
            if status_msg:
                await status_msg.edit_text("ℹ️ No source channels configured.")
            return

        # 🔐 ENSURE 2nd AUTH CHANNEL EXISTS
        if not Telegram.AUTH_CHANNEL or len(Telegram.AUTH_CHANNEL) < 2:
            LOGGER.error("❌ Second AUTH_CHANNEL not configured.")
            if status_msg:
                await status_msg.edit_text("❌ Second AUTH_CHANNEL not configured.")
            return

        # ✅ USE ONLY 2nd AUTH CHANNEL
        dest_chat_id = int(Telegram.AUTH_CHANNEL[1])

        total_scanned = 0
        total_copied = 0

        LOGGER.info(f"🚀 Starting Scrapper Scan on {len(all_sources)} channels...")
        if status_msg:
            await status_msg.edit_text(f"🚀 Starting Scrapper Scan on {len(all_sources)} channels...")

        for channel_id in all_sources:
            try:
                if status_msg:
                    await status_msg.edit_text(
                        f"🔄 **Processing Channel:** `{channel_id}`\n"
                        f"📊 Scanned: {total_scanned}\n"
                        f"📤 Copied: {total_copied}"
                    )

                scanned, copied = await cls.process_channel(
                    channel_id,
                    dest_chat_id,
                    status_msg,
                    total_scanned,
                    total_copied
                )

                total_scanned += scanned
                total_copied += copied

            except Exception as e:
                LOGGER.error(f"❌ Error processing channel {channel_id}: {e}")

        if status_msg:
            await status_msg.edit_text(
                f"✅ **Scrapper Scan Completed**\n\n"
                f"📊 Total Scanned: {total_scanned}\n"
                f"📤 Total Copied: {total_copied}"
            )

    # ==================================================
    # PROCESS CHANNEL
    # ==================================================

    @classmethod
    async def process_channel(cls, channel_id, dest_chat_id, status_msg, global_scanned, global_copied):
        last_id = await db.get_scrapper_cursor(channel_id)
        local_scanned = 0
        local_copied = 0

        if last_id == 0:
            async for m in cls.user_client.get_chat_history(channel_id, limit=1):
                last_id = max(1, m.id - 2000)
                break

        current_id = last_id

        async for top in cls.user_client.get_chat_history(channel_id, limit=1):
            top_id = top.id
            break
        else:
            return 0, 0

        batch_size = 200

        while current_id < top_id:
            ids = list(range(current_id + 1, min(current_id + batch_size + 1, top_id + 1)))

            try:
                messages = await cls.user_client.get_messages(channel_id, ids)
            except FloodWait as e:
                await asyncio.sleep(e.value + 2)
                continue

            current_id = ids[-1]

            for msg in messages:
                if not msg or msg.empty:
                    continue

                if await cls.process_message(msg, channel_id, dest_chat_id):
                    local_copied += 1

                local_scanned += 1

            await db.update_scrapper_cursor(channel_id, current_id)
            await asyncio.sleep(2)

        return local_scanned, local_copied

    # ==================================================
    # PROCESS MESSAGE
    # ==================================================

    @classmethod
    async def process_message(cls, msg, source_channel_id, dest_chat_id):
        if not (msg.video or (msg.document and msg.document.mime_type and msg.document.mime_type.startswith("video/"))):
            return False

        file = msg.video or msg.document
        title = msg.caption or file.file_name or ""
        cleaned_title = clean_filename(title)

        meta = await metadata(cleaned_title, source_channel_id, msg.id)
        if not meta:
            return False

        tmdb_id = meta.get("tmdb_id")
        media_type = meta.get("media_type")
        new_quality = meta.get("quality")
        season = meta.get("season_number")
        episode = meta.get("episode_number")

        if not tmdb_id:
            return False

        found_doc = None
        for i in range(1, len(db.dbs)):
            d = await db.get_document(media_type, int(tmdb_id), i)
            if d:
                found_doc = d
                break

        should_copy = not found_doc

        if found_doc and media_type == "movie":
            qualities = [q.get("quality") for q in found_doc.get("telegram", [])]
            should_copy = new_quality not in qualities

        if found_doc and media_type == "tv":
            for s in found_doc.get("seasons", []):
                if s.get("season_number") == season:
                    for e in s.get("episodes", []):
                        if e.get("episode_number") == episode:
                            qualities = [q.get("quality") for q in e.get("telegram", [])]
                            should_copy = new_quality not in qualities
                            break

        if should_copy:
            try:
                await cls.user_client.copy_message(
                    chat_id=dest_chat_id,
                    from_chat_id=source_channel_id,
                    message_id=msg.id
                )
                await asyncio.sleep(1)
                return True
            except FloodWait as e:
                await asyncio.sleep(e.value + 2)

        return False
