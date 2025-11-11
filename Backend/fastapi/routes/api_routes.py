from fastapi import APIRouter, Depends, HTTPException, Query, Request
from Backend.fastapi.security.credentials import require_auth
from Backend import db
from pydantic import BaseModel, Field
from typing import List, Optional

class TelegramItem(BaseModel):
    quality: str
    id: str
    name: str
    size: str

class MovieUpdate(BaseModel):
    title: str
    release_year: int
    rating: float
    telegram: List[TelegramItem]

class EpisodeUpdate(BaseModel):
    episode_number: int
    title: str
    episode_backdrop: Optional[str] = None
    telegram: List[TelegramItem]

class SeasonUpdate(BaseModel):
    season_number: int
    episodes: List[EpisodeUpdate]

class TVShowUpdate(BaseModel):
    title: str
    release_year: int
    rating: float
    seasons: List[SeasonUpdate]

router = APIRouter()

@router.get("/media/list")
async def list_media(
    media_type: str = Query("movie", enum=["movie", "tv"]),
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
    search: str = Query("", alias="search"),
    _: bool = Depends(require_auth)
):
    try:
        media_items, total_items = await db.get_media_with_pagination(
            media_type=media_type,
            page=page,
            page_size=page_size,
            search_query=search
        )

        total_pages = (total_items + page_size - 1) // page_size

        return {
            f"{media_type}s": media_items,
            "total_count": total_items,
            "total_pages": total_pages,
            "current_page": page,
            "databases_checked": [db.current_db_index]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/media/delete")
async def delete_media(
    tmdb_id: int,
    db_index: int,
    media_type: str,
    _: bool = Depends(require_auth)
):
    try:
        success = await db.delete_document(media_type, tmdb_id, db_index)
        if success:
            return {"status": "success", "message": f"{media_type.title()} deleted successfully."}
        else:
            raise HTTPException(status_code=404, detail=f"{media_type.title()} not found.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/media/update")
async def update_media_api(request: Request, tmdb_id: int, db_index: int, media_type: str, _: bool = Depends(require_auth)):
    try:
        data = await request.json()
        if media_type == "movie":
            update_data = MovieUpdate(**data)
        else:
            update_data = TVShowUpdate(**data)
            
        success = await db.update_document(media_type, tmdb_id, db_index, update_data.dict())
        if success:
            return {"status": "success", "message": "Media updated successfully."}
        else:
            raise HTTPException(status_code=404, detail="Media not found.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/media/delete-quality")
async def delete_movie_quality_api(tmdb_id: int, db_index: int, quality: str, _: bool = Depends(require_auth)):
    try:
        success = await db.delete_movie_quality(tmdb_id, db_index, quality)
        if success:
            return {"status": "success", "message": "Quality deleted successfully."}
        else:
            raise HTTPException(status_code=404, detail="Quality not found.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/media/delete-tv-quality")
async def delete_tv_quality_api(tmdb_id: int, db_index: int, season: int, episode: int, quality: str, _: bool = Depends(require_auth)):
    try:
        success = await db.delete_tv_quality(tmdb_id, db_index, season, episode, quality)
        if success:
            return {"status": "success", "message": "Quality deleted successfully."}
        else:
            raise HTTPException(status_code=404, detail="Quality not found.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/media/delete-tv-episode")
async def delete_tv_episode_api(tmdb_id: int, db_index: int, season: int, episode: int, _: bool = Depends(require_auth)):
    try:
        success = await db.delete_tv_episode(tmdb_id, db_index, season, episode)
        if success:
            return {"status": "success", "message": "Episode deleted successfully."}
        else:
            raise HTTPException(status_code=404, detail="Episode not found.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/media/delete-tv-season")
async def delete_tv_season_api(tmdb_id: int, db_index: int, season: int, _: bool = Depends(require_auth)):
    try:
        success = await db.delete_tv_season(tmdb_id, db_index, season)
        if success:
            return {"status": "success", "message": "Season deleted successfully."}
        else:
            raise HTTPException(status_code=404, detail="Season not found.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/system/workloads")
async def get_workloads(_: bool = Depends(require_auth)):
    try:
        from Backend.pyrofork.bot import work_loads
        return {
            "loads": {
                f"bot{c + 1}": l
                for c, (_, l) in enumerate(
                    sorted(work_loads.items(), key=lambda x: x[1], reverse=True)
                )
            } if work_loads else {}
        }
    except Exception as e:
        return {"loads": {}}
