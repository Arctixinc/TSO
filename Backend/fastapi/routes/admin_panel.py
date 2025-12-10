from fastapi import APIRouter, Request, Form, Depends, HTTPException, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from typing import Optional
from datetime import datetime, timedelta
import secrets

from Backend.fastapi.security.credentials import templates
from Backend.config import Telegram

router = APIRouter()

# Simple session storage (in-memory)
ADMIN_PASSWORD = "Arctix!123"
SESSION_KEY = "unified_admin_session"
SESSION_DURATION = timedelta(hours=6)

@router.get("/admin/unified", response_class=HTMLResponse)
async def unified_search_page(request: Request):
    # Check session
    session_data = request.session.get(SESSION_KEY)
    if not session_data:
        # Render login form
        return templates.TemplateResponse("unified_login.html", {"request": request})

    # Verify expiry
    try:
        expiry = datetime.fromisoformat(session_data.get("expiry"))
        if datetime.now() > expiry:
            request.session.pop(SESSION_KEY, None)
            return templates.TemplateResponse("unified_login.html", {"request": request, "error": "Session expired"})
    except:
        request.session.pop(SESSION_KEY, None)
        return templates.TemplateResponse("unified_login.html", {"request": request})

    return templates.TemplateResponse("unified_search.html", {"request": request})

@router.post("/admin/unified/login", response_class=HTMLResponse)
async def unified_login_post(request: Request, password: str = Form(...)):
    if password == ADMIN_PASSWORD:
        expiry = datetime.now() + SESSION_DURATION
        request.session[SESSION_KEY] = {
            "authenticated": True,
            "expiry": expiry.isoformat()
        }
        return RedirectResponse(url="/admin/unified", status_code=302)
    else:
        return templates.TemplateResponse("unified_login.html", {
            "request": request,
            "error": "Invalid Password"
        })

@router.get("/admin/unified/logout")
async def unified_logout(request: Request):
    request.session.pop(SESSION_KEY, None)
    return RedirectResponse(url="/admin/unified", status_code=302)
