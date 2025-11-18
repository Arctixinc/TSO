# imdb.py

import httpx
import re
import asyncio
from typing import Optional, Dict, Any

BASE_URL = "https://v3-cinemeta.strem.io"

_client: Optional[httpx.AsyncClient] = None
_client_lock = asyncio.Lock()


async def _get_client() -> httpx.AsyncClient:
    global _client
    async with _client_lock:
        if _client is None or _client.is_closed:
            _client = httpx.AsyncClient(timeout=20.0)
        return _client


def extract_first_year(value) -> int:
    """Extract first 4-digit year from any string."""
    if not value:
        return 0
    match = re.search(r"(\d{4})", str(value))
    return int(match.group(1)) if match else 0


# ---------------------------------------------------------
# SEARCH
# ---------------------------------------------------------
async def search_title(query: str, type: str) -> Optional[Dict[str, Any]]:
    """
    Search title using Cinemeta.
    type: 'movie' or 'tvSeries'
    """

    client = await _get_client()

    # Cinemeta uses "series" instead of tvSeries
    cinemeta_type = "series" if type == "tvSeries" else "movie"

    url = f"{BASE_URL}/catalog/{cinemeta_type}/imdb/search={query}.json"

    try:
        resp = await client.get(url)
        if resp.status_code != 200:
            return None

        data = resp.json()
        metas = data.get("metas", [])
        if not metas:
            return None

        m = metas[0]

        return {
            "id": m.get("imdb_id") or m.get("id", ""),
            "type": type,
            "title": m.get("name", ""),
            "year": extract_first_year(m.get("releaseInfo")),
            "poster": m.get("poster", "")
        }
    except Exception:
        return None


# ---------------------------------------------------------
# GET DETAIL
# ---------------------------------------------------------
async def get_detail(imdb_id: str) -> Optional[Dict[str, Any]]:
    """
    Returns detailed meta for IMDB ID.
    Tries both movie + series automatically.
    """

    client = await _get_client()

    for media in ["movie", "series"]:
        url = f"{BASE_URL}/meta/{media}/{imdb_id}.json"

        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                continue

            data = resp.json()
            meta = data.get("meta")
            if not meta:
                continue

            # YEAR
            year = 0
            for f in ["year", "releaseInfo", "released"]:
                if meta.get(f):
                    year = extract_first_year(meta[f])
                    if year:
                        break

            return {
                "id": meta.get("imdb_id") or meta.get("id"),
                "moviedb_id": meta.get("moviedb_id"),
                "type": meta.get("type") or media,
                "title": meta.get("name", ""),
                "plot": meta.get("description", ""),
                "genre": meta.get("genres") or meta.get("genre", []),
                "releaseDetailed": {"year": year},
                "rating": {
                    "star": float(meta.get("imdbRating") or 0)
                },
                "poster": meta.get("poster", ""),
                "background": meta.get("background", ""),
                "logo": meta.get("logo", ""),
                "runtime": meta.get("runtime", ""),
                "director": meta.get("director", []),
                "cast": meta.get("cast", []),
                "videos": meta.get("videos", [])
            }

        except Exception:
            continue

    return None


# ---------------------------------------------------------
# GET EPISODE
# ---------------------------------------------------------
async def get_season(imdb_id: str, season_id: int, episode_id: int) -> Optional[Dict[str, Any]]:
    """
    Returns specific season/episode meta from Cinemeta.
    """

    client = await _get_client()

    url = f"{BASE_URL}/meta/series/{imdb_id}.json"

    try:
        resp = await client.get(url)
        if resp.status_code != 200:
            return None

        data = resp.json()
        meta = data.get("meta", {})
        videos = meta.get("videos", [])

        for v in videos:
            if str(v.get("season")) == str(season_id) and str(v.get("episode")) == str(episode_id):
                return {
                    "title": v.get("title") or f"Episode {episode_id}",
                    "no": str(episode_id),
                    "season": str(season_id),
                    "image": v.get("thumbnail", ""),
                    "plot": v.get("overview", ""),
                    "released": v.get("released", "")
                }

        return None

    except Exception:
        return None
