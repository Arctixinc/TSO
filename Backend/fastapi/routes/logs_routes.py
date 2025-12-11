import asyncio
import os
import aiofiles
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from Backend.fastapi.security.credentials import require_admin

router = APIRouter()

LOG_FILE = os.path.abspath("log.txt")

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
async def stream_logs(_: bool = Depends(require_admin)):
    return StreamingResponse(
        log_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*"
        }
    )
