from logging import FileHandler, StreamHandler, INFO, ERROR, Formatter, basicConfig, error as log_error, info as log_info
from os import path as ospath, environ
from pathlib import Path
from subprocess import run as srun, PIPE
from dotenv import load_dotenv
from datetime import datetime
import pytz
import requests
from io import StringIO

IST = pytz.timezone("Asia/Kolkata")

class ISTFormatter(Formatter):
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, IST)
        return dt.strftime(datefmt or "%d-%b-%y %I:%M:%S %p")

# ---------- Logging Setup ----------
log_file = "log.txt"
if ospath.exists(log_file):
    with open(log_file, "w") as f:
        f.truncate(0)

file_handler = FileHandler(log_file)
stream_handler = StreamHandler()

formatter = ISTFormatter("[%(asctime)s] [%(levelname)s] - %(message)s", "%d-%b-%y %I:%M:%S %p")
file_handler.setFormatter(formatter)
stream_handler.setFormatter(formatter)

basicConfig(handlers=[file_handler, stream_handler], level=INFO)

# ---------- Load .env ----------
CONF_GIST_URL = environ.get("CONF_GIST_URL", "").strip()  # optional: set this in Heroku or host
if CONF_GIST_URL:
    try:
        resp = requests.get(CONF_GIST_URL, timeout=10)
        if resp.status_code == 200:
            env_content = StringIO(resp.text)
            load_dotenv(stream=env_content)
            log_info("Loaded .env from Gist URL")
        else:
            log_error(f"❌ Failed to fetch env from Gist | Status code: {resp.status_code}")
            log_info("⚠️ Falling back to local config.env")
            load_dotenv("config.env")
    except requests.RequestException as e:
        log_error(f"❌ Exception while fetching env from Gist: {e}")
        log_info("⚠️ Falling back to local config.env")
        load_dotenv("config.env")
else:
    load_dotenv("config.env")
    log_info("⚠️ Loaded local config.env (Gist URL not provided)")

# ---------- Git Upstream Update ----------
UPSTREAM_REPO = environ.get("UPSTREAM_REPO", "").strip() or None
UPSTREAM_BRANCH = environ.get("UPSTREAM_BRANCH", "").strip() or "master"

if UPSTREAM_REPO:
    if Path(".git").exists():
        srun(["rm", "-rf", ".git"])

    update_cmd = (
        f"git init -q && "
        f"git config --global user.email 'doc.adhikari@gmail.com' && "
        f"git config --global user.name 'weebzone' && "
        f"git add . && git commit -sm 'update' -q && "
        f"git remote add origin {UPSTREAM_REPO} && "
        f"git fetch origin -q && "
        f"git reset --hard origin/{UPSTREAM_BRANCH} -q"
    )

    update = srun(update_cmd, shell=True)
    repo = UPSTREAM_REPO.strip("/").split("/")
    repo_url = f"https://github.com/{repo[-2]}/{repo[-1]}"
    log_info(f"UPSTREAM_REPO: {repo_url} | UPSTREAM_BRANCH: {UPSTREAM_BRANCH}")

    if update.returncode == 0:
        log_info("Successfully updated with latest commits!!")
    else:
        log_error("❌ Update failed! Retry or ask for support.")
