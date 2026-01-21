from Backend import db
from Backend.logger import LOGGER
from pyrogram.types import Message
from datetime import datetime

class DefaulterManager:
    @staticmethod
    async def add_defaulter(message_id: int, file_name: str, file_unique_id: str, chat_id: int):
        """Adds a file to the defaulter list."""
        try:
            await db.dbs["tracking"]["defaulters"].update_one(
                {"file_unique_id": file_unique_id},
                {
                    "$set": {
                        "message_id": message_id,
                        "file_name": file_name,
                        "chat_id": chat_id,
                        "added_on": datetime.utcnow()
                    }
                },
                upsert=True
            )
        except Exception as e:
            LOGGER.error(f"Failed to add defaulter: {e}")

    @staticmethod
    async def remove_defaulter(file_unique_id: str):
        """Removes a file from the defaulter list."""
        try:
            await db.dbs["tracking"]["defaulters"].delete_one({"file_unique_id": file_unique_id})
        except Exception as e:
            LOGGER.error(f"Failed to remove defaulter: {e}")

    @staticmethod
    async def get_defaulters(page: int = 0, page_size: int = 16):
        """Get paginated defaulters."""
        skip = page * page_size
        cursor = db.dbs["tracking"]["defaulters"].find({}).skip(skip).limit(page_size)
        return await cursor.to_list(None)

    @staticmethod
    async def get_defaulter_count():
        return await db.dbs["tracking"]["defaulters"].count_documents({})

    @staticmethod
    async def get_defaulter_by_id(file_unique_id: str):
         return await db.dbs["tracking"]["defaulters"].find_one({"file_unique_id": file_unique_id})
