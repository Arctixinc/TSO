from fastapi import HTTPException, Request, Depends
from fastapi.security import HTTPBearer
from Backend.config import Telegram
from Backend import db
from typing import Optional
import hashlib

ADMIN_PASSWORD_HASH = hashlib.sha256(Telegram.ADMIN_PASSWORD.encode()).hexdigest()

security = HTTPBearer(auto_error=False)

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password: str) -> bool:
    return hashlib.sha256(password.encode()).hexdigest() == ADMIN_PASSWORD_HASH

from Backend.logger import LOGGER

async def verify_credentials(username: str, password: str) -> Optional[str]:
    """
    Verify credentials against Env Admin or Database Users.
    Returns the role ('admin' or 'user') if successful, else None.
    """
    # 1. Check Env Admin
    if username == Telegram.ADMIN_USERNAME and verify_password(password):
        return "admin"

    # 2. Check Database Users
    try:
        user = await db.get_user(username)
        if user:
            # LOGGER.info(f"Checking DB user: {username}, stored_hash={user.get('password')}, input_hash={hash_password(password)}")
            if user.get("password") == hash_password(password):
                return user.get("role", "user")
    except Exception as e:
        LOGGER.error(f"DB Auth Error: {e}")

    return None

def is_authenticated(request: Request) -> bool:
    return request.session.get("authenticated", False)

def require_auth(request: Request):
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required")
    return True

def require_admin(request: Request):
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required")

    role = request.session.get("role")
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return True

def get_current_user(request: Request) -> Optional[str]:
    if is_authenticated(request):
        return request.session.get("username")
    return None

def get_current_user_role(request: Request) -> Optional[str]:
    if is_authenticated(request):
        return request.session.get("role")
    return None
