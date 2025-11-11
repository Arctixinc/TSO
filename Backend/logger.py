import pytz
from logging import getLogger, FileHandler, StreamHandler, INFO, ERROR, Formatter, basicConfig
from datetime import datetime

IST = pytz.timezone("Asia/Kolkata")

class ISTFormatter(Formatter):
    """A custom formatter to use the IST timezone."""
    def formatTime(self, record, datefmt=None):
        """Formats the time to IST.

        Args:
            record: The log record.
            datefmt (str, optional): The date format. Defaults to None.

        Returns:
            The formatted time.
        """
        dt = datetime.fromtimestamp(record.created, IST)
        return dt.strftime(datefmt or "%d-%b-%y %I:%M:%S %p")

file_handler = FileHandler("log.txt")
stream_handler = StreamHandler()
formatter = ISTFormatter("[%(asctime)s] [%(levelname)s] - %(message)s", "%d-%b-%y %I:%M:%S %p")
file_handler.setFormatter(formatter)
stream_handler.setFormatter(formatter)

basicConfig(
    handlers=[file_handler, stream_handler],
    level=INFO
)

getLogger("httpx").setLevel(ERROR)
getLogger("pyrogram").setLevel(ERROR)
getLogger("fastapi").setLevel(ERROR)


LOGGER = getLogger(__name__)
LOGGER.setLevel(INFO)

LOGGER.info("Logger initialized with IST timezone.")
