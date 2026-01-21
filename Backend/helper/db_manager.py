import os
import json
from Backend.logger import LOGGER

DB_LIST_FILE = "databases.json"

def load_db_list():
    if not os.path.exists(DB_LIST_FILE):
        return ["dbFyvio"] # Default
    try:
        with open(DB_LIST_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        LOGGER.error(f"Failed to load DB list: {e}")
        return ["dbFyvio"]

def save_db_list(db_list):
    try:
        with open(DB_LIST_FILE, "w") as f:
            json.dump(db_list, f)
    except Exception as e:
        LOGGER.error(f"Failed to save DB list: {e}")

def add_db_to_list(db_name):
    dbs = load_db_list()
    if db_name not in dbs:
        dbs.append(db_name)
        save_db_list(dbs)
        return True
    return False

def remove_db_from_list(db_name):
    dbs = load_db_list()
    if db_name in dbs:
        dbs.remove(db_name)
        save_db_list(dbs)
        return True
    return False
