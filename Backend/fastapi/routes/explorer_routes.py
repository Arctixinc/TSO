import os
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates
from Backend.fastapi.security.credentials import require_admin, get_current_user, get_current_user_role
from Backend.fastapi.themes import get_theme, get_all_themes

router = APIRouter()
templates = Jinja2Templates(directory="Backend/fastapi/templates")

# Define PROJECT_ROOT (Repo Root)
# explorer_routes.py is in Backend/fastapi/routes/ -> ../../../ is Root
PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../../..")
)

def get_readable_size(size_in_bytes):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_in_bytes < 1024.0:
            return f"{size_in_bytes:.2f} {unit}"
        size_in_bytes /= 1024.0
    return f"{size_in_bytes:.2f} PB"

@router.get("/explorer", response_class=HTMLResponse)
async def explorer(
    request: Request,
    path: str = Query("", description="Relative path from PROJECT_ROOT"),
    _: bool = Depends(require_admin)
):
    # Sanitize path to ensure it is relative
    clean_path = path.lstrip("/\\")

    # Resolve absolute path
    full_path = os.path.abspath(os.path.join(PROJECT_ROOT, clean_path))

    # Path Traversal Protection
    # Use os.path.commonpath to safely verify full_path is inside PROJECT_ROOT
    try:
        common = os.path.commonpath([full_path, PROJECT_ROOT])
    except ValueError:
        # Can happen on Windows if paths are on different drives
        common = ""

    if common != PROJECT_ROOT:
        raise HTTPException(status_code=403, detail="Access denied: Path traversal detected")

    if not os.path.exists(full_path):
         raise HTTPException(status_code=404, detail="Path not found")

    # If it is a file, serve it
    if os.path.isfile(full_path):
        return FileResponse(full_path)

    # If it is a directory, list contents
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

                # Calculate relative path for the link
                rel_path = os.path.relpath(entry.path, PROJECT_ROOT)
                # Normalize slashes for URL
                rel_path = rel_path.replace("\\", "/")

                items.append({
                    "name": entry.name,
                    "is_dir": is_dir,
                    "path": rel_path,
                    "size": size
                })
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied accessing directory")

    # Sort: Directories first, then alphabetical by name
    items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))

    # Calculate parent path
    parent_path = None
    if full_path != PROJECT_ROOT:
        # Get parent directory
        parent_dir = os.path.dirname(full_path)
        # Verify parent is still within root (should be, but good to be safe)
        if parent_dir.startswith(PROJECT_ROOT):
            rel_parent = os.path.relpath(parent_dir, PROJECT_ROOT)
            if rel_parent == ".":
                parent_path = ""
            else:
                parent_path = rel_parent.replace("\\", "/")

    # Generate breadcrumbs
    path_parts = []
    if clean_path and clean_path != ".":
        parts = clean_path.replace("\\", "/").split("/")
        current_build = ""
        for p in parts:
            if not p: continue
            current_build = f"{current_build}/{p}" if current_build else p
            path_parts.append({"name": p, "full_path": current_build})

    # Get Theme & User Context for base.html
    theme_name = request.session.get("theme", "purple_gradient")
    theme = get_theme(theme_name)
    current_user = get_current_user(request)
    user_role = get_current_user_role(request)

    return templates.TemplateResponse("explorer.html", {
        "request": request,
        "theme": theme,
        "themes": get_all_themes(),
        "current_theme": theme_name,
        "current_user": current_user,
        "user_role": user_role,
        "current_path": clean_path if clean_path else "/",
        "items": items,
        "parent_path": parent_path,
        "path_parts": path_parts
    })
