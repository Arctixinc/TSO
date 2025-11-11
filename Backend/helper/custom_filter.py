from pyrogram.filters import create
from Backend.config import Telegram

class CustomFilters:
    """A class for custom filters."""

    @staticmethod
    async def owner_filter(client, message):
        """A filter to check if the message is from the owner.

        Args:
            client: The Pyrogram client.
            message: The message to check.

        Returns:
            bool: True if the message is from the owner, False otherwise.
        """
        user = message.from_user or message.sender_chat
        uid = user.id
        return uid == Telegram.OWNER_ID

    owner = create(owner_filter)