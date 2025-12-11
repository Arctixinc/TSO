import asyncio
import os
import time
import aiofiles
from fastapi import APIRouter, Request, Form
from fastapi.responses import StreamingResponse, HTMLResponse, RedirectResponse
from Backend.fastapi.security.credentials import get_current_user_role

router = APIRouter()

LOG_FILE = os.path.abspath("log.txt")
LIVE_LOGS_PASSWORD = "pass420"
SESSION_DURATION = 600  # 10 minutes

async def log_generator():
    if not os.path.exists(LOG_FILE):
        yield b"data: log.txt not found\n\n"
        return

    async with aiofiles.open(LOG_FILE, mode="r", errors="ignore") as f:
        # Start from the beginning as requested
        await f.seek(0)

        while True:
            line = await f.readline()
            if line:
                yield line.encode("utf-8")
            else:
                await asyncio.sleep(0.3)

@router.get("/live-logs")
async def stream_logs(request: Request):
    # 1. Check if user is Admin
    role = get_current_user_role(request)
    if role == "admin":
        return StreamingResponse(
            log_generator(),
            media_type="text/plain",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*"
            }
        )

    # 2. Check for temporary live-logs session
    expiry = request.session.get("live_logs_expiry")
    if expiry and time.time() < expiry:
        return StreamingResponse(
            log_generator(),
            media_type="text/plain",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*"
            }
        )

    # 3. Return Password Popup (HTML Form)
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Live Logs Access</title>
        <style>
            body {
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background-color: #121212;
                color: #e0e0e0;
                display: flex;
                justify-content: center;
                align-items: center;
                height: 100vh;
                margin: 0;
            }
            .auth-card {
                background-color: #1e1e1e;
                padding: 2rem;
                border-radius: 12px;
                box-shadow: 0 8px 16px rgba(0, 0, 0, 0.5);
                text-align: center;
                width: 100%;
                max-width: 320px;
            }
            h2 { margin-bottom: 1.5rem; color: #ffffff; }
            input[type="password"] {
                width: 100%;
                padding: 12px;
                margin-bottom: 1rem;
                border: 1px solid #333;
                border-radius: 6px;
                background-color: #2c2c2c;
                color: #fff;
                box-sizing: border-box;
            }
            input[type="password"]:focus {
                outline: none;
                border-color: #007bff;
            }
            button {
                width: 100%;
                padding: 12px;
                background-color: #007bff;
                color: white;
                border: none;
                border-radius: 6px;
                cursor: pointer;
                font-size: 1rem;
                transition: background-color 0.2s;
            }
            button:hover { background-color: #0056b3; }
        </style>
    </head>
    <body>
        <div class="auth-card">
            <h2>🔒 Restricted Access</h2>
            <form action="/live-logs/auth" method="post">
                <input type="password" name="password" placeholder="Enter Access Password" required autofocus>
                <button type="submit">Unlock Logs</button>
            </form>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@router.post("/live-logs/auth")
async def auth_live_logs(request: Request, password: str = Form(...)):
    if password == LIVE_LOGS_PASSWORD:
        request.session["live_logs_expiry"] = time.time() + SESSION_DURATION
        return RedirectResponse(url="/live-logs", status_code=303)
    else:
        return HTMLResponse(
            content="<script>alert('❌ Incorrect Password'); window.location.href='/live-logs';</script>",
            status_code=403
        )
