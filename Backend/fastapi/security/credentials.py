from fastapi import HTTPException, Request
from fastapi.security import HTTPBearer
from Backend.config import Telegram
from typing import Optional
import hashlib

ADMIN_PASSWORD_HASH = hashlib.sha256(Telegram.ADMIN_PASSWORD.encode()).hexdigest()

security = HTTPBearer(auto_error=False)

def verify_password(password: str) -> bool:
    """Verifies a password against the admin password hash.

    Args:
        password (str): The password to verify.

    Returns:
        bool: True if the password is correct, False otherwise.
    """
    return hashlib.sha256(password.encode()).hexdigest() == ADMIN_PASSWORD_HASH

def verify_credentials(username: str, password: str) -> bool:
    """Verifies a username and password.

    Args:
        username (str): The username to verify.
        password (str): The password to verify.

    Returns:
        bool: True if the credentials are correct, False otherwise.
    """
    return username == Telegram.ADMIN_USERNAME and verify_password(password)

def is_authenticated(request: Request) -> bool:
    """Checks if a user is authenticated.

    Args:
        request (Request): The request object.

    Returns:
        bool: True if the user is authenticated, False otherwise.
    """
    return request.session.get("authenticated", False)

def require_auth(request: Request):
    """Requires authentication for a route.

    Args:
        request (Request): The request object.

    Raises:
        HTTPException: If the user is not authenticated.

    Returns:
        True if the user is authenticated.
    """
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required")
    return True

def get_current_user(request: Request) -> Optional[str]:
    """Gets the current user.

    Args:
        request (Request): The request object.

    Returns:
        Optional[str]: The username of the current user, or None if not authenticated.
    """
    if is_authenticated(request):
        return request.session.get("username")
    return None
