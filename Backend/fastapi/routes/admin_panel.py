from fastapi import APIRouter, Request, Form, Depends, HTTPException, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from typing import Optional
from datetime import datetime, timedelta
import secrets

from Backend.config import Telegram
from Backend.fastapi.themes import get_theme, get_all_themes

router = APIRouter()

# Initialize templates locally to avoid circular imports or incorrect references
templates = Jinja2Templates(directory="Backend/fastapi/templates")

# Simple session storage (in-memory)
ADMIN_PASSWORD = "Arctix!123"
SESSION_KEY = "unified_admin_session"
SESSION_DURATION = timedelta(hours=6)

def get_common_context(request: Request):
    """Helper to inject theme and user data expected by base.html"""
    theme_name = request.session.get("theme", "purple_gradient")
    theme = get_theme(theme_name)

    # Check standard session for user details (if available)
    current_user = request.session.get("username")
    user_role = request.session.get("role")

    return {
        "request": request,
        "theme": theme,
        "themes": get_all_themes(),
        "current_theme": theme_name,
        "current_user": current_user,
        "user_role": user_role
    }

@router.get("/admin/unified", response_class=HTMLResponse)
async def unified_search_page(request: Request):
    # Check unified session
    session_data = request.session.get(SESSION_KEY)

    context = get_common_context(request)

    if not session_data:
        # Render login form
        return templates.TemplateResponse("unified_login.html", context)

    # Verify expiry
    try:
        expiry = datetime.fromisoformat(session_data.get("expiry"))
        if datetime.now() > expiry:
            request.session.pop(SESSION_KEY, None)
            context["error"] = "Session expired"
            return templates.TemplateResponse("unified_login.html", context)
    except:
        request.session.pop(SESSION_KEY, None)
        return templates.TemplateResponse("unified_login.html", context)

    return templates.TemplateResponse("unified_search.html", context)

@router.post("/admin/unified/login", response_class=HTMLResponse)
async def unified_login_post(request: Request, password: str = Form(...)):
    context = get_common_context(request)
    if password == ADMIN_PASSWORD:
        expiry = datetime.now() + SESSION_DURATION
        request.session[SESSION_KEY] = {
            "authenticated": True,
            "expiry": expiry.isoformat()
        }
        return RedirectResponse(url="/admin/unified", status_code=302)
    else:
        context["error"] = "Invalid Password"
        return templates.TemplateResponse("unified_login.html", context)

@router.get("/admin/unified/logout")
async def unified_logout(request: Request):
    request.session.pop(SESSION_KEY, None)
    return RedirectResponse(url="/admin/unified", status_code=302)
