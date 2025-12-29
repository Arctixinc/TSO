import os
import mimetypes
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates

from Backend.fastapi.security.credentials import (
    require_admin,
    get_current_user,
    get_current_user_role,
)
from Backend.fastapi.themes import get_theme, get_all_themes

router = APIRouter()
templates = Jinja2Templates(directory="Backend/fastapi/templates")

# ============================================================
# PROJECT ROOT (Repo Root)
# ============================================================
PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../../..")
)

# ============================================================
# UTIL
# ============================================================
def get_readable_size(size_in_bytes: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_in_bytes < 1024.0:
            return f"{size_in_bytes:.2f} {unit}"
        size_in_bytes /= 1024.0
    return f"{size_in_bytes:.2f} PB"


# ============================================================
# EXPLORER ROUTE
# ============================================================
@router.get("/explorer", response_class=HTMLResponse)
async def explorer(
    request: Request,
    path: str = Query("", description="Relative path from PROJECT_ROOT"),
    _: bool = Depends(require_admin),
):
    # --------------------------------------------------------
    # SANITIZE PATH
    # --------------------------------------------------------
    clean_path = path.lstrip("/\\")
    full_path = os.path.abspath(os.path.join(PROJECT_ROOT, clean_path))

    # --------------------------------------------------------
    # PATH TRAVERSAL PROTECTION
    # --------------------------------------------------------
    try:
        common = os.path.commonpath([full_path, PROJECT_ROOT])
    except ValueError:
        common = ""

    if common != PROJECT_ROOT:
        raise HTTPException(status_code=403, detail="Access denied")

    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="Path not found")

    # --------------------------------------------------------
    # FILE DOWNLOAD (FIXED FILENAME ISSUE)
    # --------------------------------------------------------
    if os.path.isfile(full_path):
        mime_type, _ = mimetypes.guess_type(full_path)

        return FileResponse(
            path=full_path,
            filename=os.path.basename(full_path),  # ✅ FIX
            media_type=mime_type or "application/octet-stream",
        )

    # --------------------------------------------------------
    # DIRECTORY LISTING
    # --------------------------------------------------------
    items = []
    try:
        with os.scandir(full_path) as it:
            for entry in it:
                is_dir = entry.is_dir()
                size = "-"

                if not is_dir:
                    try:
                        size = get_readable_size(entry.stat().st_size)
                    except OSError:
                        pass

                rel_path = os.path.relpath(entry.path, PROJECT_ROOT)
                rel_path = rel_path.replace("\\", "/")

                items.append(
                    {
                        "name": entry.name,
                        "is_dir": is_dir,
                        "path": rel_path,
                        "size": size,
                    }
                )
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")

    # --------------------------------------------------------
    # SORT: DIRS FIRST
    # --------------------------------------------------------
    items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))

    # --------------------------------------------------------
    # PARENT PATH
    # --------------------------------------------------------
    parent_path = None
    if full_path != PROJECT_ROOT:
        parent_dir = os.path.dirname(full_path)
        if parent_dir.startswith(PROJECT_ROOT):
            rel_parent = os.path.relpath(parent_dir, PROJECT_ROOT)
            parent_path = "" if rel_parent == "." else rel_parent.replace("\\", "/")

    # --------------------------------------------------------
    # BREADCRUMBS
    # --------------------------------------------------------
    path_parts = []
    if clean_path and clean_path != ".":
        parts = clean_path.replace("\\", "/").split("/")
        current = ""
        for part in parts:
            if not part:
                continue
            current = f"{current}/{part}" if current else part
            path_parts.append({"name": part, "full_path": current})

    # --------------------------------------------------------
    # THEME + USER CONTEXT
    # --------------------------------------------------------
    theme_name = request.session.get("theme", "purple_gradient")
    theme = get_theme(theme_name)

    return templates.TemplateResponse(
        "explorer.html",
        {
            "request": request,
            "theme": theme,
            "themes": get_all_themes(),
            "current_theme": theme_name,
            "current_user": get_current_user(request),
            "user_role": get_current_user_role(request),
            "current_path": clean_path if clean_path else "/",
            "items": items,
            "parent_path": parent_path,
            "path_parts": path_parts,
        },
    )
