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
async def fetch_tv_metadata(title, season, episode, encoded_string, year=None, quality=None, default_id=None, tmdb_id=None) -> dict | None:
    tv_details = None
    ep_details = None
    imdb_id = None
    use_tmdb = False

    # 1. Optimistic Fetch by TMDb ID if provided
    if tmdb_id:
        try:
            tv_details = await tmdb.tv(tmdb_id).details(append_to_response="external_ids,credits")
            if tv_details and tv_details.external_ids:
                imdb_id = tv_details.external_ids.imdb_id
        except Exception as e:
            LOGGER.info(f"TMDb TV fetch by ID {tmdb_id} failed ({e}). Falling back to search.")
            tv_details = None
            tmdb_id = None  # Reset ID to trigger search

    # 2. Setup IMDb ID if not found above
    if not imdb_id:
        imdb_id = default_id if default_id and default_id.startswith("tt") else await safe_imdb_search(title, "tvSeries")
    
    # 3. Try IMDb
    if imdb_id:
        try:
            await asyncio.sleep(DELAY)
            imdb_tv_details = await get_detail(imdb_id=imdb_id)
            if imdb_tv_details:
                # IMDb success path
                images = format_imdb_images(imdb_id)
                cast = imdb_tv_details.get("cast", [])
                if cast and isinstance(cast[0], dict):
                    cast = [c.get("name") for c in cast if c.get("name")]

                await asyncio.sleep(DELAY)
                ep_details = await get_season(imdb_id=imdb_id, season_id=season, episode_id=episode)

                return {
                    "tmdb_id": imdb_id.replace("tt", ""),
                    "imdb_id": imdb_id,
                    "title": imdb_tv_details.get("title", title),
                    "year": imdb_tv_details.get("releaseDetailed", {}).get("year", 0),
                    "rate": imdb_tv_details.get("rating", {}).get("star", 0),
                    "description": imdb_tv_details.get("plot", ""),
                    "poster": images["poster"],
                    "backdrop": images["backdrop"],
                    "logo": images["logo"],
                    "genres": imdb_tv_details.get("genre", []),
                    "media_type": "tv",
                    "cast": cast,
                    "runtime": imdb_tv_details.get("runtime", ""),
                    "season_number": season,
                    "episode_number": episode,
                    "episode_title": ep_details.get("title", f"S{season}E{episode}") if ep_details else f"{imdb_tv_details.get('title', title)} S{season}E{episode}",
                    "episode_backdrop": ep_details.get("image", "") if ep_details else "",
                    "episode_overview": ep_details.get("plot", "") if ep_details else "",
                    "episode_released": str(ep_details.get("released", "")) if ep_details and ep_details.get("released") else "",
                    "quality": quality,
                    "encoded_string": encoded_string,
                }
        except Exception as e:
            LOGGER.warning(f"IMDb TV fetch failed [{imdb_id}]: {e}")
            pass
    
    # 4. Fallback to TMDb (if IMDb failed or wasn't found)
    use_tmdb = True

    # If we didn't get details in step 1 (or failed), fetch now
    if not tv_details:
        if not tmdb_id:
            tmdb_result = await safe_tmdb_search(title, "tv")
            if not tmdb_result:
                LOGGER.warning(f"No TMDb result for '{title}'")
                return None
            tmdb_id = tmdb_result.id
        
        try:
            tv_details = await tmdb.tv(tmdb_id).details(append_to_response="external_ids,credits")
        except Exception as e:
            LOGGER.warning(f"TMDb TV details failed for {title}: {e}")
            return None

    # Fetch episode details from TMDb
    try:
        ep_details = await tmdb.episode(tv_details.id, season, episode).details()
    except Exception as e:
        LOGGER.warning(f"TMDb episode not found for {title} S{season}E{episode}: {e}")
        ep_details = None

    # Return TMDb Data
    if tv_details:
        cast = [p.name for p in tv_details.credits.cast[:10]] if hasattr(tv_details, 'credits') and hasattr(tv_details.credits, 'cast') else []
        runtime = f"{tv_details.episode_run_time[0]} min" if hasattr(tv_details, 'episode_run_time') and tv_details.episode_run_time else None

        return {
            "tmdb_id": tv_details.id,
            "imdb_id": tv_details.external_ids.imdb_id if hasattr(tv_details, "external_ids") else None,
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

    return None

# ----------------- Movie Metadata -----------------
async def fetch_movie_metadata(title, encoded_string, year=None, quality=None, default_id=None, tmdb_id=None) -> dict | None:
    movie_details = None
    imdb_id = None
    use_tmdb = False

    # 1. Optimistic Fetch by TMDb ID if provided
    if tmdb_id:
        try:
            movie_details = await tmdb.movie(tmdb_id).details(append_to_response="external_ids,credits")
            if movie_details and movie_details.external_ids:
                imdb_id = movie_details.external_ids.imdb_id
        except Exception as e:
            LOGGER.info(f"TMDb movie fetch by ID {tmdb_id} failed ({e}). Falling back to search.")
            movie_details = None
            tmdb_id = None # Reset to trigger search

    # 2. Setup IMDb ID if not found above
    if not imdb_id:
        imdb_id = default_id if default_id and default_id.startswith("tt") else await safe_imdb_search(f"{title} {year}" if year else title, "movie")
    
    # 3. Try IMDb
    imdb_data = None
    if imdb_id:
        try:
            imdb_data = await get_detail(imdb_id=imdb_id)
        except Exception as e:
            LOGGER.warning(f"IMDb movie fetch failed [{title}]: {e}")
    
    # Return IMDb data if success
    if imdb_data:
        images = format_imdb_images(imdb_id)
        cast = imdb_data.get("cast", [])
        if cast and isinstance(cast[0], dict):
            cast = [c.get("name") for c in cast if c.get("name")]

        return {
            "tmdb_id": imdb_id.replace("tt", ""),
            "imdb_id": imdb_id,
            "title": imdb_data.get("title", title),
            "year": imdb_data.get("releaseDetailed", {}).get("year", 0),
            "rate": imdb_data.get("rating", {}).get("star", 0),
            "description": imdb_data.get("plot", ""),
            "poster": images["poster"],
            "backdrop": images["backdrop"],
            "logo": images["logo"],
            "media_type": "movie",
            "genres": imdb_data.get("genre", []),
            "cast": cast,
            "runtime": imdb_data.get("runtime", ""),
            "quality": quality,
            "encoded_string": encoded_string,
        }

    # 4. Fallback to TMDb
    use_tmdb = True

    if not movie_details:
        if not tmdb_id:
            tmdb_result = await safe_tmdb_search(title, "movie", year)
            if not tmdb_result:
                LOGGER.warning(f"No TMDb movie found for '{title}'")
                return None
            tmdb_id = tmdb_result.id
        
        try:
            movie_details = await tmdb.movie(tmdb_id).details(append_to_response="external_ids,credits")
        except Exception as e:
            LOGGER.warning(f"TMDb movie details failed for {title}: {e}")
            return None
    
    # Return TMDb result
    if movie_details:
        cast = [p.name for p in movie_details.credits.cast[:10]] if hasattr(movie_details, 'credits') and hasattr(movie_details.credits, 'cast') else []
        runtime = f"{movie_details.runtime} min" if hasattr(movie_details, 'runtime') and movie_details.runtime else None

        return {
            "tmdb_id": movie_details.id,
            "imdb_id": movie_details.external_ids.imdb_id if hasattr(movie_details, "external_ids") else None,
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

    return None
