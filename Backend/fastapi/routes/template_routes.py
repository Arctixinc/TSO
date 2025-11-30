from fastapi import Request, Form, HTTPException, Depends
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from Backend.fastapi.security.credentials import verify_credentials, require_auth, require_admin, is_authenticated, get_current_user, get_current_user_role
from Backend.fastapi.themes import get_theme, get_all_themes
from Backend import db
from Backend.pyrofork.bot import work_loads, multi_clients, StreamBot
from Backend.helper.pyro import get_readable_time
from Backend import StartTime, __version__
from time import time
from datetime import datetime
import pytz

templates = Jinja2Templates(directory="Backend/fastapi/templates")

# --- Date Formatting Helper ---
def format_date_ist(date_str):
    """
    Converts a UTC date string (YYYY-MM-DD) to IST (DD-MM-YYYY).
    Assuming the input string is just a date, effectively treating it as UTC midnight or agnostic.
    For standard YYYY-MM-DD strings stored in DB.
    """
    if not date_str:
        return ""
    try:
        # Parse YYYY-MM-DD
        dt_obj = datetime.strptime(str(date_str), "%Y-%m-%d")
        # IST is UTC+5:30. Since we only have date, shifting timezone might shift the day depending on time.
        # But here 'date' usually implies the release day in original country or UTC.
        # Simple formatting DD-MM-YYYY is usually sufficient for "Indian Format".
        # If strict timezone conversion is needed from a datetime object:
        # utc_dt = dt_obj.replace(tzinfo=pytz.utc)
        # ist_tz = pytz.timezone("Asia/Kolkata")
        # ist_dt = utc_dt.astimezone(ist_tz)
        # return ist_dt.strftime("%d-%m-%Y")

        # Just reformatting string is safer if input is just date without time.
        return dt_obj.strftime("%d-%m-%Y")
    except ValueError:
        return str(date_str)

# Register filter
templates.env.filters["date_ist"] = format_date_ist


async def login_page(request: Request):
    if is_authenticated(request):
        return RedirectResponse(url="/", status_code=302)
    
    theme_name = request.session.get("theme", "purple_gradient")
    theme = get_theme(theme_name)
    
    return templates.TemplateResponse("login.html", {
        "request": request,
        "theme": theme,
        "themes": get_all_themes(),
        "current_theme": theme_name
    })

async def login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    role = await verify_credentials(username, password)
    if role:
        request.session["authenticated"] = True
        request.session["username"] = username
        request.session["role"] = role

        # Redirect based on role
        if role == "admin":
            return RedirectResponse(url="/", status_code=302)
        else:
            return RedirectResponse(url="/library", status_code=302)
    else:
        theme_name = request.session.get("theme", "purple_gradient")
        theme = get_theme(theme_name)
        return templates.TemplateResponse("login.html", {
            "request": request,
            "theme": theme,
            "themes": get_all_themes(),
            "current_theme": theme_name,
            "error": "Invalid credentials"
        })

async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)

async def set_theme(request: Request, theme: str = Form(...)):
    if theme in get_all_themes():
        request.session["theme"] = theme
    return RedirectResponse(url=request.headers.get("referer", "/"), status_code=302)

async def dashboard_page(request: Request, _: bool = Depends(require_admin)):
    theme_name = request.session.get("theme", "purple_gradient")
    theme = get_theme(theme_name)
    current_user = get_current_user(request)
    user_role = get_current_user_role(request)
    
    try:
        db_stats = await db.get_database_stats()
        total_movies = sum(stat.get("movie_count", 0) for stat in db_stats)
        total_tv_shows = sum(stat.get("tv_count", 0) for stat in db_stats)
        
        system_stats = {
            "server_status": "running",
            "uptime": get_readable_time(time() - StartTime),
            "telegram_bot": f"@{StreamBot.username}" if StreamBot and StreamBot.username else "@StreamBot",
            "connected_bots": len(multi_clients),
            "loads": {
                f"bot{c + 1}": l
                for c, (_, l) in enumerate(
                    sorted(work_loads.items(), key=lambda x: x[1], reverse=True)
                )
            } if work_loads else {},
            "version": __version__,
            "movies": total_movies,
            "tv_shows": total_tv_shows,
            "databases": db_stats,
            "total_databases": len(db_stats),
            "current_db_index": db.current_db_index
        }
    except Exception as e:
        print(f"Dashboard error: {e}")
        system_stats = {
            "server_status": "error", 
            "error": str(e),
            "uptime": "N/A",
            "telegram_bot": "@StreamBot",
            "connected_bots": 0,
            "loads": {},
            "version": "1.0.0",
            "movies": 0,
            "tv_shows": 0,
            "databases": [],
            "total_databases": 0,
            "current_db_index": 1
        }
    
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "theme": theme,
        "themes": get_all_themes(),
        "current_theme": theme_name,
        "current_user": current_user,
        "user_role": user_role,
        "system_stats": system_stats
    })
    

async def media_management_page(request: Request, media_type: str = "movie", _: bool = Depends(require_auth)):
    theme_name = request.session.get("theme", "purple_gradient")
    theme = get_theme(theme_name)
    current_user = get_current_user(request)
    user_role = get_current_user_role(request)
    
    return templates.TemplateResponse("media_management.html", {
        "request": request,
        "theme": theme,
        "themes": get_all_themes(),
        "current_theme": theme_name,
        "current_user": current_user,
        "user_role": user_role,
        "media_type": media_type
    })

async def edit_media_page(request: Request, tmdb_id: int, db_index: int, media_type: str, _: bool = Depends(require_admin)):
    theme_name = request.session.get("theme", "purple_gradient")
    theme = get_theme(theme_name)
    current_user = get_current_user(request)
    user_role = get_current_user_role(request)
    
    try:
        media_details = await db.get_document(media_type, tmdb_id, db_index)
        if not media_details:
            raise HTTPException(status_code=404, detail="Media not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
    return templates.TemplateResponse("media_edit.html", {
        "request": request,
        "theme": theme,
        "themes": get_all_themes(),
        "current_theme": theme_name,
        "current_user": current_user,
        "user_role": user_role,
        "tmdb_id": tmdb_id,
        "db_index": db_index,
        "media_type": media_type,
        "media_details": media_details
    })

async def media_view_page(request: Request, tmdb_id: int, db_index: int, media_type: str, _: bool = Depends(require_auth)):
    theme_name = request.session.get("theme", "purple_gradient")
    theme = get_theme(theme_name)
    current_user = get_current_user(request)
    user_role = get_current_user_role(request)

    # Note: User explicitly asked to update UI for user role. Admin can also view this page if link is clicked manually or redirected.

    try:
        media_details = await db.get_document(media_type, tmdb_id, db_index)
        if not media_details:
            raise HTTPException(status_code=404, detail="Media not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return templates.TemplateResponse("media_view.html", {
        "request": request,
        "theme": theme,
        "themes": get_all_themes(),
        "current_theme": theme_name,
        "current_user": current_user,
        "user_role": user_role,
        "tmdb_id": tmdb_id,
        "db_index": db_index,
        "media_type": media_type,
        "media_details": media_details
    })

async def public_status_page(request: Request):
    theme_name = request.session.get("theme", "purple_gradient")
    theme = get_theme(theme_name)
    current_user = get_current_user(request)
    user_role = get_current_user_role(request)
    
    try:
        db_stats = await db.get_database_stats()
        total_movies = sum(stat.get("movie_count", 0) for stat in db_stats)
        total_tv_shows = sum(stat.get("tv_count", 0) for stat in db_stats)
        
        public_stats = {
            "status": "operational",
            "uptime": "99.9%",
            "total_content": total_movies + total_tv_shows,
            "databases_online": len(db_stats)
        }
    except Exception:
        public_stats = {
            "status": "maintenance",
            "uptime": "N/A",
            "total_content": 0,
            "databases_online": 0
        }
    
    return templates.TemplateResponse("public_status.html", {
        "request": request,
        "theme": theme,
        "themes": get_all_themes(),
        "current_theme": theme_name,
        "stats": public_stats,
        "is_authenticated": is_authenticated(request),
        "current_user": current_user,
        "user_role": user_role
    })

async def stremio_guide_page(request: Request):
    theme_name = request.session.get("theme", "purple_gradient")
    theme = get_theme(theme_name)
    current_user = get_current_user(request)
    user_role = get_current_user_role(request)
    
    return templates.TemplateResponse("stremio_guide.html", {
        "request": request,
        "theme": theme,
        "themes": get_all_themes(),
        "current_theme": theme_name,
        "is_authenticated": is_authenticated(request),
        "current_user": current_user,
        "user_role": user_role
    })
