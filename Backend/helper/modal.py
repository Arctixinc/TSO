from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field

class QualityDetail(BaseModel):
    """A Pydantic model for quality details."""
    quality: str 
    id: str 
    name: str 
    size: str 

class Episode(BaseModel):
    """A Pydantic model for an episode."""
    episode_number: int
    title: str 
    episode_backdrop: Optional[str] = None 
    telegram: Optional[List[QualityDetail]] 

class Season(BaseModel):
    """A Pydantic model for a season."""
    season_number: int 
    episodes: List[Episode] 

class TVShowSchema(BaseModel):
    """A Pydantic model for a TV show."""
    tmdb_id: int 
    imdb_id: Optional[str] = None
    db_index: int 
    title: str 
    genres: Optional[List[str]] = None
    description: Optional[str] = None 
    rating: Optional[float] = None
    release_year: Optional[int] = None
    poster: Optional[str] = None 
    backdrop: Optional[str] = None 
    logo: Optional[str] = None
    media_type: str
    updated_on: datetime = Field(default_factory=datetime.utcnow)
    seasons: List[Season] 

class MovieSchema(BaseModel):
    """A Pydantic model for a movie."""
    tmdb_id: int
    imdb_id: Optional[str] = None
    db_index: int 
    title: str 
    genres: Optional[List[str]] = None
    description: Optional[str] = None
    rating: Optional[float] = None
    release_year: Optional[int] = None
    poster: Optional[str] = None 
    backdrop: Optional[str] = None
    logo: Optional[str] = None
    media_type: str
    updated_on: datetime = Field(default_factory=datetime.utcnow) 
    telegram: Optional[List[QualityDetail]]
