import asyncio
from typing import Callable, Awaitable, List, Dict
from pyrogram import Client
from pyrogram.errors import FloodWait
from Backend.config import Telegram
from Backend.logger import LOGGER
from Backend import db
from Backend.helper.metadata import metadata
from Backend.helper.pyro import clean_filename


# Global task tracker: {user_id: asyncio.Task}
SCR_RUNNING_TASKS: Dict[int, asyncio.Task] = {}


async def interruptible_sleep(seconds: float, cancel_event: asyncio.Event):
    """
    Sleeps for `seconds` but wakes up immediately if `cancel_event` is set.
    """
    if seconds <= 0:
        return

    # Check initially
    if cancel_event.is_set():
        raise asyncio.CancelledError

    # Create a sleep task and an event waiter
    sleep_task = asyncio.create_task(asyncio.sleep(seconds))
    wait_event = asyncio.create_task(cancel_event.wait())

    done, pending = await asyncio.wait(
        [sleep_task, wait_event],
        return_when=asyncio.FIRST_COMPLETED
    )

    # Cancel pending tasks to clean up
    for task in pending:
        task.cancel()

    # If the event was set, raise CancelledError
    if cancel_event.is_set():
        raise asyncio.CancelledError


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
    async def scan_sources(
        cls,
        user_id: int,
        target_channels: List[int] = None,
        progress_callback: Callable[[dict], Awaitable[None]] = None,
        cancel_event: asyncio.Event = None
    ):
        """
        Orchestrates the scanning process.
        :param user_id: ID of the user starting the scan (used for task tracking)
        :param target_channels: List of channel IDs to scan. If None, scans all from DB.
        :param progress_callback: Async function to report progress.
        :param cancel_event: Event to signal cancellation.
        """
        if not cls.user_client:
            LOGGER.error("❌ User Client not initialized.")
            if progress_callback:
                await progress_callback({"status": "error", "message": "❌ User Client not initialized."})
            return

        # Clean up existing task for this user if any (logic handled by caller usually, but good safeguard)
        if user_id in SCR_RUNNING_TASKS:
            task = SCR_RUNNING_TASKS[user_id]
            if not task.done():
                task.cancel()
            del SCR_RUNNING_TASKS[user_id]

        # Register current task
        SCR_RUNNING_TASKS[user_id] = asyncio.current_task()

        try:
            # Determine channels
            if target_channels is None:
                db_sources = await db.get_source_channels()
                all_sources = list(set(db_sources + Telegram.SOURCE_CHANNELS))
            else:
                all_sources = target_channels

            if not all_sources:
                if progress_callback:
                    await progress_callback({"status": "error", "message": "ℹ️ No source channels configured."})
                return

            # 🔐 ENSURE 2nd AUTH CHANNEL EXISTS
            if not Telegram.AUTH_CHANNEL or len(Telegram.AUTH_CHANNEL) < 2:
                if progress_callback:
                    await progress_callback({"status": "error", "message": "❌ Second AUTH_CHANNEL not configured."})
                return

            dest_chat_id = int(Telegram.AUTH_CHANNEL[1])

            total_scanned = 0
            total_copied = 0

            if progress_callback:
                await progress_callback({
                    "status": "starting",
                    "channel_count": len(all_sources)
                })

            for channel_id in all_sources:
                # Check cancellation before starting next channel
                if cancel_event and cancel_event.is_set():
                    raise asyncio.CancelledError

                try:
                    scanned, copied = await cls.process_channel(
                        channel_id,
                        dest_chat_id,
                        progress_callback,
                        total_scanned,
                        total_copied,
                        cancel_event
                    )
                    total_scanned += scanned
                    total_copied += copied
                except asyncio.CancelledError:
                    raise  # Propagate up
                except Exception as e:
                    LOGGER.error(f"❌ Error processing channel {channel_id}: {e}")

            if progress_callback:
                await progress_callback({
                    "status": "completed",
                    "total_scanned": total_scanned,
                    "total_copied": total_copied
                })

        except asyncio.CancelledError:
            LOGGER.info(f"🚫 Scan cancelled for user {user_id}")
            if progress_callback:
                await progress_callback({"status": "cancelled"})
        finally:
            # Cleanup task registration
            if user_id in SCR_RUNNING_TASKS:
                del SCR_RUNNING_TASKS[user_id]

    # ==================================================
    # PROCESS CHANNEL
    # ==================================================

    @classmethod
    async def process_channel(
        cls,
        channel_id: int,
        dest_chat_id: int,
        progress_callback,
        global_scanned,
        global_copied,
        cancel_event: asyncio.Event
    ):
        last_id = await db.get_scrapper_cursor(channel_id)
        local_scanned = 0
        local_copied = 0

        # Resolve channel name if possible (best effort)
        channel_name = str(channel_id)
        try:
            chat = await cls.user_client.get_chat(channel_id)
            if chat.title:
                channel_name = chat.title
        except Exception:
            pass

        # Fetch top ID once
        top_id = 0
        try:
            async for m in cls.user_client.get_chat_history(channel_id, limit=1):
                top_id = m.id
                break
        except Exception as e:
            LOGGER.error(f"Failed to fetch history for {channel_id}: {e}")
            return 0, 0

        # If cursor is 0 (first run), start from top - 2000 roughly or just start
        if last_id == 0:
            last_id = max(1, top_id - 2000)

        current_id = last_id
        batch_size = 200

        while current_id < top_id:
            # Check cancel
            if cancel_event and cancel_event.is_set():
                raise asyncio.CancelledError

            ids = list(range(current_id + 1, min(current_id + batch_size + 1, top_id + 1)))
            if not ids:
                break

            # Calculate remaining based on BACKLOG
            remaining = max(0, top_id - current_id)

            # Report Progress
            if progress_callback:
                await progress_callback({
                    "status": "running",
                    "channel_name": channel_name,
                    "scanned": global_scanned + local_scanned,
                    "copied": global_copied + local_copied,
                    "remaining": remaining
                })

            try:
                messages = await cls.user_client.get_messages(channel_id, ids)
            except FloodWait as e:
                LOGGER.warning(f"⏳ FloodWait {e.value}s in get_messages")
                if progress_callback:
                    await progress_callback({
                        "status": "floodwait",
                        "wait_time": e.value
                    })
                await interruptible_sleep(e.value + 2, cancel_event)
                continue
            except Exception as e:
                LOGGER.error(f"Error getting messages: {e}")
                await interruptible_sleep(5, cancel_event)
                continue

            current_id = ids[-1] # Advance tentatively

            for msg in messages:
                if not msg or msg.empty:
                    continue

                processed = False
                try:
                    processed = await cls.process_message(msg, channel_id, dest_chat_id, cancel_event)
                except FloodWait as e:
                    LOGGER.warning(f"⏳ FloodWait {e.value}s in copy_message")
                    if progress_callback:
                        await progress_callback({
                            "status": "floodwait",
                            "wait_time": e.value
                        })
                    await interruptible_sleep(e.value + 2, cancel_event)
                    # Retry logic is complex inside loop; simplistic approach: skip or simple retry?
                    # The requirement says "Handles FloodWait in... copy_message".
                    # process_message handles the copy. If it fails there, we might miss one.
                    # Ideally process_message should handle its own retry or we loop here.
                    # For now, if process_message raises FloodWait, we catch it here and sleep.
                    # The message is technically "scanned" but not "copied".

                if processed:
                    local_copied += 1

                local_scanned += 1

            # Update cursor after batch
            await db.update_scrapper_cursor(channel_id, current_id)

            # Rate limit sleep
            await interruptible_sleep(2, cancel_event)

        return local_scanned, local_copied

    # ==================================================
    # PROCESS MESSAGE
    # ==================================================

    @classmethod
    async def process_message(cls, msg, source_channel_id, dest_chat_id, cancel_event):
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
            while True:
                # Check cancel before copy attempt
                if cancel_event and cancel_event.is_set():
                    raise asyncio.CancelledError

                try:
                    await cls.user_client.copy_message(
                        chat_id=dest_chat_id,
                        from_chat_id=source_channel_id,
                        message_id=msg.id
                    )
                    await interruptible_sleep(1, cancel_event)
                    return True
                except FloodWait as e:
                    LOGGER.warning(f"⏳ FloodWait {e.value}s in copy retry")
                    await interruptible_sleep(e.value + 2, cancel_event)
                    continue # Retry loop
                except Exception as e:
                    LOGGER.error(f"Copy failed: {e}")
                    return False

        return False
