import asyncio
import traceback
from PTT import parse_title
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
    if not path:
        return ""
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
        search = tmdb.search()
        if type_ == "movie":
            # Search with Year strict first
            results = await search.movies(
                query=title,
                year=year,
                include_adult=False
            )
            # Fallback 1: Try without year if strict failed
            if not results and year:
                 results = await search.movies(query=title, include_adult=False)

        else:
            # TV Show Search
            # Search with Year strict first (first_air_date_year)
            results = await search.tv(
                query=title,
                first_air_date_year=year,
                include_adult=False
            )
            # Fallback 1: Try without year if strict failed
            if not results and year:
                results = await search.tv(query=title, include_adult=False)

        if not results:
             return None

        # Filter: If we provided a year, and got results, prefer the one close to that year
        # because fallback might return a show from 2025 when we wanted 2023.
        if year:
            target_year = int(year)
            for res in results:
                # Check release date
                res_year = 0
                if type_ == "movie":
                    res_year = getattr(res.release_date, "year", 0)
                else:
                    res_year = getattr(res.first_air_date, "year", 0)

                # Allow +/- 1 year tolerance or exact match
                if res_year and abs(res_year - target_year) <= 1:
                    return res

        # If no result matched strictly within tolerance, or no year provided, return first
        return results[0]

    except Exception as e:
        LOGGER.error(f"TMDb search failed for '{title}' [{type_}]: {e}")
        return None


async def metadata(filename: str, channel: int, msg_id: int) -> dict | None:
    """Parse filename using PTT and fetch metadata for movie/TV content."""

    try:
        parsed = parse_title(filename)
    except Exception as e:
        LOGGER.error(f"PTT parsing failed for {filename}: {e}\n{traceback.format_exc()}")
        return None

    # --------------------------
    # 🔍 Extract basic fields
    # --------------------------
    title = parsed.get("title")
    seasons = parsed.get("seasons", [])
    episodes = parsed.get("episodes", [])
    year = parsed.get("year")
    quality = parsed.get("resolution")  # same as old: resolution
    complete = parsed.get("complete", False)

    # Standardize values
    season = seasons[0] if seasons else None
    episode = episodes[0] if episodes else None   # first episode if multi

    # ----------------------------------------
    # ❌ Skip multipart files (CD1, part2, etc.)
    # ----------------------------------------
    multipart_pattern = compile(r"(?:part|cd|disc|disk)[s._-]*\d+(?=\.\w+$)", IGNORECASE)
    if multipart_pattern.search(filename):
        LOGGER.info(f"Skipping {filename}: multipart file detected")
        return None

    # ----------------------------------------------------
    # 🧠 Episode-range detection (PTT gives list directly)
    # ----------------------------------------------------
    combined_note = None

    if len(episodes) > 1:
        combined_note = f"Episodes {episodes[0]}-{episodes[-1]}"
        episode = episodes[0]
        LOGGER.info(f"📦 PTT Combined Range: {title} S{season}E{episodes[0]}-{episodes[-1]}")

    # ----------------------------------------------------
    # 🎬 Full season detection (PTT gives complete=True)
    # ----------------------------------------------------
    if complete:
        combined_note = combined_note or "Full Season"
        episode = 1
        LOGGER.info(f"📦 Full Season Detected via PTT: {title} S{season}")

    # ----------------------------------------------------
    # ⚠️ Skip files with no resolution (bad candidate)
    # ----------------------------------------------------
    if not quality:
        LOGGER.warning(f"Skipping {filename}: No resolution (parsed={parsed})")
        return None

    # ----------------------------------------------------
    # ⚠️ If no title → cannot proceed
    # ----------------------------------------------------
    if not title:
        LOGGER.info(f"No title parsed from: {filename} (parsed={parsed})")
        return None

    # ----------------------------------------------------
    # 🔗 Extract TMDb/IMDb ID from filename or default
    # ----------------------------------------------------
    default_id = None
    try:
        default_id = extract_tmdb_id(Backend.USE_DEFAULT_ID)
    except:
        pass

    if not default_id:
        try:
            default_id = extract_tmdb_id(filename)
        except:
            pass

    # ----------------------------------------------------
    # 🔗 Encode job data
    # ----------------------------------------------------
    data = {"chat_id": channel, "msg_id": msg_id}
    encoded_string = await encode_string(data)

    # ----------------------------------------------------
    # 🎬 Determine if it's TV or Movie
    # ----------------------------------------------------
    is_tv = season is not None

    if is_tv:
        LOGGER.info(f"Fetching TV metadata: {title} S{season}E{episode} [{quality}] {combined_note or ''}")
        return await fetch_tv_metadata(
            title=title,
            season=season,
            episode=episode,
            encoded_string=encoded_string,
            year=year,
            quality=quality,
            default_id=default_id
        )

    else:
        LOGGER.info(f"Fetching Movie metadata: {title} ({year}) [{quality}]")
        return await fetch_movie_metadata(
            title=title,
            encoded_string=encoded_string,
            year=year,
            quality=quality,
            default_id=default_id
        )

        
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
        tmdb_result = await safe_tmdb_search(title, "tv", year)
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
