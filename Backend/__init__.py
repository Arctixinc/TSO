from Backend.helper.database import Database
from time import time
from datetime import datetime
import pytz

timezone = pytz.timezone("Asia/Kolkata")
now = datetime.now(timezone)
StartTime = time()


USE_DEFAULT_ID: str = None

import os
if os.path.exists("dbname.txt"):
    with open("dbname.txt", "r") as f:
        db_name = f.read().strip()
else:
    db_name = "dbFyvio"

db = Database(db_name=db_name)

__version__ = "2.5.0"
