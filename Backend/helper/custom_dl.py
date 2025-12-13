import asyncio
from pyrogram import utils, raw
from pyrogram.errors import AuthBytesInvalid
from pyrogram.file_id import FileId, FileType, ThumbnailSource
from pyrogram.session import Session, Auth
from typing import Dict, Union
from Backend.logger import LOGGER
from Backend.helper.exceptions import FIleNotFound
from Backend.helper.pyro import get_file_ids
from Backend.pyrofork.bot import work_loads
from pyrogram import Client, utils, raw


class ByteStreamer:
    def __init__(self, client: Client):
        self.clean_timer = 30 * 60
        self.client: Client = client
        self.__cached_file_ids: Dict[int, FileId] = {}
        self.__dc_locks: Dict[int, asyncio.Lock] = {}
        asyncio.create_task(self.clean_cache())

    async def get_file_properties(self, chat_id: int, message_id: int) -> FileId:
        if message_id not in self.__cached_file_ids:
            file_id = await get_file_ids(self.client, int(chat_id), int(message_id))
            if not file_id:
                LOGGER.info('Message with ID %s not found!', message_id)
                raise FIleNotFound
            self.__cached_file_ids[message_id] = file_id
        return self.__cached_file_ids[message_id]

    async def yield_file(
        self,
        file_id: FileId,
        index: int,
        offset: int,
        first_part_cut: int,
        last_part_cut: int,
        part_count: int,
        chunk_size: int,
    ) -> Union[str, None]: 
        """Yields chunks of a file in order, handling partial slicing for first/last chunk."""
        client = self.client
        # work_loads[index] += 1  # Removed to handle update in stream_routes.py
        LOGGER.debug(f"Starting to yield file {file_id.unique_id} with client {index}.")

        media_session = await self.generate_media_session(client, file_id)
        if not media_session:
            work_loads[index] -= 1
            LOGGER.error(f"Failed to generate media session for file {file_id.unique_id}")
            return

        location = await self.get_location(file_id)
        current_part = 1
        try:
            while current_part <= part_count:
                r = await media_session.send(
                    raw.functions.upload.GetFile(location=location, offset=offset, limit=chunk_size)
                )
                if not isinstance(r, raw.types.upload.File):
                    break

                chunk = r.bytes
                if not chunk:
                    break

                # Handle first/last chunk slicing
                if part_count == 1:
                    yield chunk[first_part_cut:last_part_cut]
                elif current_part == 1:
                    yield chunk[first_part_cut:]
                elif current_part == part_count:
                    yield chunk[:last_part_cut]
                else:
                    yield chunk

                offset += chunk_size
                current_part += 1

        except (TimeoutError, AttributeError):
            pass
        except Exception as e:
            LOGGER.error(f"Error while streaming file {file_id.unique_id}: {e}")
        finally:
            work_loads[index] -= 1
            LOGGER.debug(f"Finished yielding file {file_id.unique_id} with {current_part - 1} parts.")

    async def generate_media_session(self, client: Client, file_id: FileId) -> Union[Session, None]:
        """Generates or reuses a media session for a specific DC."""
        dc_id = file_id.dc_id
        lock = self.__dc_locks.setdefault(dc_id, asyncio.Lock())

        async with lock:  # ensure only one session per DC is created concurrently
            media_session = client.media_sessions.get(dc_id)
            if media_session:
                LOGGER.debug(f"Using cached media session for DC {dc_id}")
                return media_session

            try:
                if dc_id != await client.storage.dc_id():
                    # Create new media session for different DC
                    media_session = Session(
                        client,
                        dc_id,
                        await Auth(client, dc_id, await client.storage.test_mode()).create(),
                        await client.storage.test_mode(),
                        is_media=True,
                    )
                    await media_session.start()

                    for _ in range(6):
                        exported_auth = await client.invoke(raw.functions.auth.ExportAuthorization(dc_id=dc_id))
                        try:
                            await media_session.send(
                                raw.functions.auth.ImportAuthorization(id=exported_auth.id, bytes=exported_auth.bytes)
                            )
                            break
                        except AuthBytesInvalid:
                            LOGGER.debug(f"Invalid auth bytes for DC {dc_id}, retrying...")
                        except OSError:
                            LOGGER.debug(f"Connection error for DC {dc_id}, retrying...")
                            await asyncio.sleep(2)
                    else:
                        await media_session.stop()
                        LOGGER.error(f"Failed to establish media session for DC {dc_id}")
                        return None
                else:
                    # Use current DC
                    media_session = Session(
                        client,
                        dc_id,
                        await client.storage.auth_key(),
                        await client.storage.test_mode(),
                        is_media=True,
                    )
                    await media_session.start()

                client.media_sessions[dc_id] = media_session
                LOGGER.debug(f"Created media session for DC {dc_id}")
                return media_session

            except Exception as e:
                LOGGER.error(f"Error creating media session for DC {dc_id}: {e}")
                return None

    @staticmethod
    async def get_location(file_id: FileId):
        file_type = file_id.file_type
        if file_type == FileType.CHAT_PHOTO:
            if file_id.chat_id > 0:
                peer = raw.types.InputPeerUser(
                    user_id=file_id.chat_id, access_hash=file_id.chat_access_hash
                )
            else:
                if file_id.chat_access_hash == 0:
                    peer = raw.types.InputPeerChat(chat_id=-file_id.chat_id)
                else:
                    peer = raw.types.InputPeerChannel(
                        channel_id=utils.get_channel_id(file_id.chat_id),
                        access_hash=file_id.chat_access_hash,
                    )
            return raw.types.InputPeerPhotoFileLocation(
                peer=peer,
                volume_id=file_id.volume_id,
                local_id=file_id.local_id,
                big=file_id.thumbnail_source == ThumbnailSource.CHAT_PHOTO_BIG,
            )
        elif file_type == FileType.PHOTO:
            return raw.types.InputPhotoFileLocation(
                id=file_id.media_id,
                access_hash=file_id.access_hash,
                file_reference=file_id.file_reference,
                thumb_size=file_id.thumbnail_size,
            )
        else:
            return raw.types.InputDocumentFileLocation(
                id=file_id.media_id,
                access_hash=file_id.access_hash,
                file_reference=file_id.file_reference,
                thumb_size=file_id.thumbnail_size,
            )

    async def clean_cache(self) -> None:
        while True:
            await asyncio.sleep(self.clean_timer)
            self.__cached_file_ids.clear()
            LOGGER.debug("Cleaned the file ID cache")
