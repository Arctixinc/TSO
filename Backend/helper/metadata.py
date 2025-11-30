import asyncio
import traceback
import PTN
import re
from re import compile, IGNORECASE
from Backend.helper.imdb import get_detail, get_season, search_title
from Backend.helper.pyro import extract_tmdb_id
from themoviedb import aioTMDb
from Backend.config import Telegram
import Backend
from Backend.logger import LOGGER
from Backend.helper.encrypt import encode_string

# ----------------- Configuration -----------------
DELAY = 2
tmdb = aioTMDb(key=Telegram.TMDB_API, language="en-US", region="US")

# ----------------- Helpers -----------------
def format_tmdb_image(path: str, size="w500") -> str:
    return f"https://image.tmdb.org/t/p/{size}{path}"

def format_imdb_images(imdb_id: str) -> dict:
    return {
        "poster": f"https://images.metahub.space/poster/small/{imdb_id}/img",
        "backdrop": f"https://images.metahub.space/background/medium/{imdb_id}/img",
        "logo": f"https://images.metahub.space/logo/medium/{imdb_id}/img",
    }

async def safe_imdb_search(title: str, type_: str) -> str | None:
    """Safely search IMDb title and return its ID."""
    try:
        result = await search_title(query=title, type=type_)
        return result["id"] if result else None
    except Exception as e:
        LOGGER.warning(f"IMDb search failed for '{title}' [{type_}]: {e}")
        return None

async def safe_tmdb_search(title: str, type_: str, year=None):
    """Safely search TMDb title."""
    try:
        if type_ == "movie":
            if year:
                results = await tmdb.search().movies(query=title, year=year)
            else:
                results = await tmdb.search().movies(query=title)
        else:
            results = await tmdb.search().tv(query=title)
        return results[0] if results else None
    except Exception as e:
        LOGGER.error(f"TMDb search failed for '{title}' [{type_}]: {e}")
        return None


import re
import traceback
from re import compile, IGNORECASE

async def metadata(filename: str, channel: int, msg_id: int) -> dict | None:
    """Parses filename and fetches metadata for TV or Movie content."""

    try:
        parsed = PTN.parse(filename)
    except Exception as e:
        LOGGER.error(f"PTN parsing failed for {filename}: {e}\n{traceback.format_exc()}")
        return None

    # 🧠 Extract fields
    title = parsed.get("title")
    season = parsed.get("season")
    episode = parsed.get("episode")
    year = parsed.get("year")
    quality = parsed.get("resolution")
    excess = parsed.get("excess", [])

    # 🧩 Combined presence indicator
    if excess and any("combined" in item.lower() for item in excess):
        LOGGER.info(f"Detected combined release keyword in parsed excess: {filename}")

    # 🧹 Skip split/multipart files (CD1, part2, etc.)
    multipart_pattern = compile(r"(?:part|cd|disc|disk)[s._-]*\d+(?=\.\w+$)", IGNORECASE)
    if multipart_pattern.search(filename):
        LOGGER.info(f"Skipping {filename}: seems to be a split/multipart file")
        return None

    combined_note = None

    # --- 🔍 Detect combined episode ranges like E01-09, EP02 to 10, etc. ---
    range_match = re.search(r"(?:E|EP)[\s_]*0*(\d{1,5})\s*(?:[-–~to]+)\s*0*(\d{1,5})", filename, re.IGNORECASE)
    if range_match:
        start_ep, end_ep = map(int, range_match.groups())
        combined_note = f"Episodes {start_ep}-{end_ep}"

        if not season:
            s_match = re.search(r"S(\d{1,2})", filename, re.IGNORECASE)
            if s_match:
                season = int(s_match.group(1))

        episode = start_ep
        LOGGER.info(f"📦 Combined Range Detected: {title or filename} S{season or 'X'}E{start_ep}-{end_ep}")

    # --- 🎞 Detect combined by natural language / keywords ---
    # Handles names like “S02 09-16 COMBINED” or “Season 03 COMPLETE”
    if not episode and any(word in filename.upper() for word in ["COMBINED", "COMPLETE", "FULL SEASON", "ALL EPISODES"]):
        if not season:
            s_match = re.search(r"S(?:EASON)?[\s_]*(\d{1,2})", filename, re.IGNORECASE)
            if s_match:
                season = int(s_match.group(1))

        episode = 1
        combined_note = combined_note or "Full Season Combined"
        LOGGER.info(f"📦 Full Season Combined Detected: {title or filename} S{season or 'X'}")

    # --- 🧩 Handle cases like “S02 09-16” without explicit E keywords ---
    if not combined_note and not episode:
        range_only = re.search(r"S(\d{1,2})[\s_.-]+0*(\d{1,2})\s*[-–~to]+\s*0*(\d{1,2})", filename, re.IGNORECASE)
        if range_only:
            season = season or int(range_only.group(1))
            start_ep, end_ep = int(range_only.group(2)), int(range_only.group(3))
            episode = start_ep
            combined_note = f"Episodes {start_ep}-{end_ep}"
            LOGGER.info(f"📦 Hybrid Combined Range: {title or filename} S{season}E{start_ep}-{end_ep}")

    # --- ⚠️ Validation ---
    if not quality:
        LOGGER.warning(f"Skipping {filename}: No resolution (parsed={parsed})")
        return None
        
    # 🧩 Accept PTN multi-episode lists and treat as single episode
    if isinstance(episode, list):
        if episode:
            episode = episode[0]  # take first episode only
            combined_note = combined_note or f"Combined ({parsed['episode'][0]}-{parsed['episode'][-1]})"
            LOGGER.info(f"📦 Combined Episode Range Detected — assuming single episode: S{season}E{episode}")
        else:
            LOGGER.warning(f"Empty episode list for {filename}: {parsed}")
            return None

    if isinstance(season, list):
        season = season[0] if season else None
    

    if season and not episode:
        combined_note = combined_note or "Full Season"
        episode = 1
        LOGGER.info(f"📦 Season-only file assumed as full season: {title or filename} S{season} ({combined_note})")

    if not title:
        LOGGER.info(f"No title parsed from: {filename} (parsed={parsed})")
        return None

    # --- 🔗 Extract TMDb/IMDb ID ---
    default_id = None
    try:
        default_id = extract_tmdb_id(Backend.USE_DEFAULT_ID)
    except Exception:
        pass

    if not default_id:
        try:
            default_id = extract_tmdb_id(filename)
        except Exception:
            pass

    # --- 🔗 Encode job data ---
    data = {"chat_id": channel, "msg_id": msg_id}
    encoded_string = await encode_string(data)

    # --- 🎬 Fetch metadata ---
    try:
        if season and episode:
            LOGGER.info(f"Fetching TV metadata: {title} S{season}E{episode} [{quality}] {combined_note or ''}")
            return await fetch_tv_metadata(title, season, episode, encoded_string, year, quality, default_id)
        else:
            LOGGER.info(f"Fetching Movie metadata: {title} ({year}) [{quality}]")
            return await fetch_movie_metadata(title, encoded_string, year, quality, default_id)

    except Exception as e:
        LOGGER.error(f"Error while fetching metadata for {filename}: {e}\n{traceback.format_exc()}")
        return None

        
# ----------------- TV Metadata -----------------
async def fetch_tv_metadata(title, season, episode, encoded_string, year=None, quality=None, default_id=None) -> dict | None:
    imdb_id = default_id if default_id and default_id.startswith("tt") else await safe_imdb_search(title, "tvSeries")
    tv_details, ep_details, use_tmdb = None, None, False
    
    # Try IMDb first
    if imdb_id:
        try:
            await asyncio.sleep(DELAY)
            tv_details = await get_detail(imdb_id=imdb_id)
            await asyncio.sleep(DELAY)
            ep_details = await get_season(imdb_id=imdb_id, season_id=season, episode_id=episode)
        except Exception as e:
            LOGGER.warning(f"IMDb TV fetch failed [{imdb_id}]: {e}")
    
    # IMDb failed → fallback to TMDb
    if not tv_details and not ep_details:
        use_tmdb = True
        tmdb_result = await safe_tmdb_search(title, "tv")
        if not tmdb_result:
            LOGGER.warning(f"No TMDb result for '{title}'")
            return None
        
        tv_id = tmdb_result.id
        try:
            tv_details = await tmdb.tv(tv_id).details(append_to_response="external_ids,credits")
        except Exception as e:
            LOGGER.warning(f"TMDb TV details failed for {title}: {e}")
            return None
        
        # Fetch episode safely
        try:
            ep_details = await tmdb.episode(tv_id, season, episode).details()
        except Exception as e:
            LOGGER.warning(f"TMDb episode not found for {title} S{season}E{episode}: {e}")
            ep_details = None
    
    # Return TMDb-based data
    if use_tmdb and tv_details:
        cast = [p.name for p in tv_details.credits.cast[:10]] if hasattr(tv_details, 'credits') and hasattr(tv_details.credits, 'cast') else []
        runtime = f"{tv_details.episode_run_time[0]} min" if hasattr(tv_details, 'episode_run_time') and tv_details.episode_run_time else None

        return {
            "tmdb_id": tv_details.id,
            "imdb_id": tv_details.external_ids.imdb_id,
            "title": tv_details.name,
            "year": getattr(tv_details.first_air_date, "year", 0),
            "rate": getattr(tv_details, "vote_average", 0) or 0,
            "description": tv_details.overview or "",
            "poster": format_tmdb_image(tv_details.poster_path),
            "backdrop": format_tmdb_image(tv_details.backdrop_path, "original"),
            "logo": "",
            "genres": [g.name for g in (tv_details.genres or [])],
            "media_type": "tv",
            "cast": cast,
            "runtime": runtime,
            "season_number": season,
            "episode_number": episode,
            "episode_title": getattr(ep_details, "name", f"S{season}E{episode}") if ep_details else f"{tv_details.name} S{season}E{episode}",
            "episode_backdrop": format_tmdb_image(getattr(ep_details, "still_path", None), "original") if ep_details else "",
            "episode_overview": getattr(ep_details, "overview", "") if ep_details else "",
            "episode_released": str(getattr(ep_details, "air_date", "")) if ep_details and getattr(ep_details, "air_date", None) else "",
            "quality": quality,
            "encoded_string": encoded_string,
        }
    
    # IMDb-based data
    if not tv_details:
        LOGGER.warning(f"No valid IMDb data for {title}")
        return None
    
    imdb_id = tv_details.get("id", "")
    images = format_imdb_images(imdb_id)
    
    cast = tv_details.get("cast", [])
    if cast and isinstance(cast[0], dict):
        cast = [c.get("name") for c in cast if c.get("name")]

    return {
        "tmdb_id": imdb_id.replace("tt", ""),
        "imdb_id": imdb_id,
        "title": tv_details.get("title", title),
        "year": tv_details.get("releaseDetailed", {}).get("year", 0),
        "rate": tv_details.get("rating", {}).get("star", 0),
        "description": tv_details.get("plot", ""),
        "poster": images["poster"],
        "backdrop": images["backdrop"],
        "logo": images["logo"],
        "genres": tv_details.get("genre", []),
        "media_type": "tv",
        "cast": cast,
        "runtime": tv_details.get("runtime", ""),
        "season_number": season,
        "episode_number": episode,
        "episode_title": ep_details.get("title", f"S{season}E{episode}") if ep_details else f"{tv_details.get('title', title)} S{season}E{episode}",
        "episode_backdrop": ep_details.get("image", "") if ep_details else "",
        "episode_overview": ep_details.get("plot", "") if ep_details else "",
        "episode_released": str(ep_details.get("released", "")) if ep_details and ep_details.get("released") else "",
        "quality": quality,
        "encoded_string": encoded_string,
    }

# ----------------- Movie Metadata -----------------
async def fetch_movie_metadata(title, encoded_string, year=None, quality=None, default_id=None) -> dict | None:
    imdb_id = default_id if default_id and default_id.startswith("tt") else await safe_imdb_search(f"{title} {year}" if year else title, "movie")
    movie_details, use_tmdb = None, False
    
    # Try IMDb first
    if imdb_id:
        try:
            movie_details = await get_detail(imdb_id=imdb_id)
        except Exception as e:
            LOGGER.warning(f"IMDb movie fetch failed [{title}]: {e}")
    
    # IMDb failed → fallback to TMDb
    if not movie_details:
        use_tmdb = True
        tmdb_result = await safe_tmdb_search(title, "movie", year)
        if not tmdb_result:
            LOGGER.warning(f"No TMDb movie found for '{title}'")
            return None
        
        try:
            movie_details = await tmdb.movie(tmdb_result.id).details(append_to_response="external_ids,credits")
        except Exception as e:
            LOGGER.warning(f"TMDb movie details failed for {title}: {e}")
            return None
    
    # TMDb result
    if use_tmdb and movie_details:
        cast = [p.name for p in movie_details.credits.cast[:10]] if hasattr(movie_details, 'credits') and hasattr(movie_details.credits, 'cast') else []
        runtime = f"{movie_details.runtime} min" if hasattr(movie_details, 'runtime') and movie_details.runtime else None

        return {
            "tmdb_id": movie_details.id,
            "imdb_id": movie_details.external_ids.imdb_id,
            "title": movie_details.title,
            "year": getattr(movie_details.release_date, "year", 0),
            "rate": getattr(movie_details, "vote_average", 0) or 0,
            "description": movie_details.overview or "",
            "poster": format_tmdb_image(movie_details.poster_path),
            "backdrop": format_tmdb_image(movie_details.backdrop_path, "original"),
            "logo": "",
            "media_type": "movie",
            "genres": [g.name for g in (movie_details.genres or [])],
            "cast": cast,
            "runtime": runtime,
            "quality": quality,
            "encoded_string": encoded_string,
        }
    
    # IMDb result
    imdb_id = movie_details.get("id", "")
    images = format_imdb_images(imdb_id)
    
    cast = movie_details.get("cast", [])
    if cast and isinstance(cast[0], dict):
        cast = [c.get("name") for c in cast if c.get("name")]

    return {
        "tmdb_id": imdb_id.replace("tt", ""),
        "imdb_id": imdb_id,
        "title": movie_details.get("title", title),
        "year": movie_details.get("releaseDetailed", {}).get("year", 0),
        "rate": movie_details.get("rating", {}).get("star", 0),
        "description": movie_details.get("plot", ""),
        "poster": images["poster"],
        "backdrop": images["backdrop"],
        "logo": images["logo"],
        "media_type": "movie",
        "genres": movie_details.get("genre", []),
        "cast": cast,
        "runtime": movie_details.get("runtime", ""),
        "quality": quality,
        "encoded_string": encoded_string,
    }
