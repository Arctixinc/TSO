import asyncio
import traceback
import PTN
import re
from functools import lru_cache, wraps
from re import compile, IGNORECASE
from Backend.helper.imdb import get_detail, get_season, search_title
from Backend.helper.pyro import extract_tmdb_id
from themoviedb import aioTMDb
from Backend.config import Telegram
import Backend
from Backend.logger import LOGGER
from Backend.helper.encrypt import encode_string

# ----------------- Configuration -----------------
DELAY = 1
tmdb = aioTMDb(key=Telegram.TMDB_API, language="en-US", region="US")

def async_lru_cache(maxsize=128):
    def decorator(fn):
        @lru_cache(maxsize)
        def cached_fn(*args, **kwargs):
            coro = fn(*args, **kwargs)
            return asyncio.ensure_future(coro)
        return cached_fn
    return decorator

# ----------------- Helpers -----------------
def format_tmdb_image(path: str, size="w500") -> str:
    return f"https://image.tmdb.org/t/p/{size}{path}" if path else ""

@async_lru_cache(maxsize=128)
async def safe_tmdb_search(title: str, type_: str, year=None):
    """Safely search TMDb title."""
    for i in range(3):
        try:
            if type_ == "movie":
                results = await tmdb.search().movies(query=title, year=year)
            else:
                results = await tmdb.search().tv(query=title)
            return results[0] if results else None
        except Exception as e:
            LOGGER.error(f"TMDb search failed for '{title}' [{type_}]: {e}, attempt {i+1}")
            await asyncio.sleep(2**i)
    return None

async def metadata(filename: str, channel: int, msg_id: int) -> dict | None:
    """Parses filename and fetches metadata for TV or Movie content."""
    try:
        parsed = PTN.parse(filename)
        title = parsed.get("title")
        if not title:
            LOGGER.info(f"No title parsed from: {filename} (parsed={parsed})")
            return None

        season = parsed.get("season")
        episode = parsed.get("episode")
        year = parsed.get("year")
        quality = parsed.get("resolution")

        if not quality:
            LOGGER.warning(f"Skipping {filename}: No resolution found.")
            return None

        data = {"chat_id": channel, "msg_id": msg_id}
        encoded_string = await encode_string(data)

        if season and episode:
            LOGGER.info(f"Fetching TV metadata: {title} S{season}E{episode} [{quality}]")
            return await fetch_tv_metadata(title, season, episode, encoded_string, year, quality)
        else:
            LOGGER.info(f"Fetching Movie metadata: {title} ({year}) [{quality}]")
            return await fetch_movie_metadata(title, encoded_string, year, quality)

    except Exception as e:
        LOGGER.error(f"Error while fetching metadata for {filename}: {e}\n{traceback.format_exc()}")
        return None

# ----------------- TV Metadata -----------------
async def fetch_tv_metadata(title, season, episode, encoded_string, year=None, quality=None) -> dict | None:
    tmdb_result = await safe_tmdb_search(title, "tv", year)
    if not tmdb_result:
        LOGGER.warning(f"No TMDb result for '{title}'")
        return None
    
    tv_id = tmdb_result.id
    try:
        tv_details = await tmdb.tv(tv_id).details(append_to_response="external_ids")
        ep_details = await tmdb.episode(tv_id, season, episode).details()
    except Exception as e:
        LOGGER.warning(f"TMDb details fetch failed for {title} S{season}E{episode}: {e}")
        return None

    return {
        "tmdb_id": tv_details.id,
        "imdb_id": tv_details.external_ids.imdb_id,
        "title": tv_details.name,
        "year": getattr(tv_details.first_air_date, "year", 0),
        "rate": getattr(tv_details, "vote_average", 0) or 0,
        "description": tv_details.overview or "",
        "poster": format_tmdb_image(tv_details.poster_path),
        "backdrop": format_tmdb_image(tv_details.backdrop_path, "original"),
        "genres": [g.name for g in (tv_details.genres or [])],
        "media_type": "tv",
        "season_number": season,
        "episode_number": episode,
        "episode_title": ep_details.name if ep_details else f"S{season}E{episode}",
        "episode_backdrop": format_tmdb_image(ep_details.still_path, "original") if ep_details else "",
        "quality": quality,
        "encoded_string": encoded_string,
    }

# ----------------- Movie Metadata -----------------
async def fetch_movie_metadata(title, encoded_string, year=None, quality=None) -> dict | None:
    tmdb_result = await safe_tmdb_search(title, "movie", year)
    if not tmdb_result:
        LOGGER.warning(f"No TMDb movie found for '{title}'")
        return None
    
    try:
        movie_details = await tmdb.movie(tmdb_result.id).details(append_to_response="external_ids")
    except Exception as e:
        LOGGER.warning(f"TMDb movie details failed for {title}: {e}")
        return None

    return {
        "tmdb_id": movie_details.id,
        "imdb_id": movie_details.external_ids.imdb_id,
        "title": movie_details.title,
        "year": getattr(movie_details.release_date, "year", 0),
        "rate": getattr(movie_details, "vote_average", 0) or 0,
        "description": movie_details.overview or "",
        "poster": format_tmdb_image(movie_details.poster_path),
        "backdrop": format_tmdb_image(movie_details.backdrop_path, "original"),
        "media_type": "movie",
        "genres": [g.name for g in (movie_details.genres or [])],
        "quality": quality,
        "encoded_string": encoded_string,
    }
