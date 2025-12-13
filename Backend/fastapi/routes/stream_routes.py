import math
import re
import secrets
import mimetypes
from typing import Tuple
from fastapi import APIRouter, Request, HTTPException, Response
from fastapi.responses import StreamingResponse

from Backend.logger import LOGGER
from Backend.helper.encrypt import decode_string
from Backend.helper.exceptions import InvalidHash
from Backend.helper.custom_dl import ByteStreamer
from Backend.pyrofork.bot import StreamBot, work_loads, multi_clients

router = APIRouter(tags=["Streaming"])
class_cache = {}


def parse_range_header(range_header: str, file_size: int) -> Tuple[int, int]:
    """Parses the HTTP Range header safely."""
    if not range_header:
        return 0, file_size - 1
    try:
        start, end = (range_header.replace("bytes=", "") + "-").split("-")[:2]
        start = int(start)
        end = int(end) if end else file_size - 1
        if start < 0 or end >= file_size or start > end:
            raise ValueError
    except Exception:
        raise HTTPException(
            status_code=416,
            detail="Requested Range Not Satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )
    return start, end


@router.get("/dl/{id}/{name}")
@router.head("/dl/{id}/{name}")
async def stream_handler(request: Request, id: str, name: str):
    """
    Handles GET and HEAD requests for streaming Telegram files.
    HEAD requests return headers only.
    """
    decoded_data = await decode_string(id)
    if not decoded_data.get("msg_id"):
        raise HTTPException(status_code=400, detail="Missing id")

    chat_id = f"-100{decoded_data['chat_id']}"
    msg_id = int(decoded_data["msg_id"])

    try:
        message = await StreamBot.get_messages(int(chat_id), msg_id)
    except Exception as e:
        LOGGER.error(f"Failed to fetch message {msg_id} from {chat_id}: {e}")
        raise HTTPException(status_code=502, detail="Unable to fetch message from Telegram")

    file = message.video or message.document
    if not file:
        raise HTTPException(status_code=404, detail="No downloadable media found in message")

    file_hash = file.file_unique_id[:6]

    return await media_streamer(
        request,
        chat_id=int(chat_id),
        message_id=msg_id,
        secure_hash=file_hash
    )


async def media_streamer(
    request: Request,
    chat_id: int,
    message_id: int,
    secure_hash: str,
) -> StreamingResponse:
    """
    Streams a Telegram file with Range header support.
    Returns only headers for HEAD requests.
    """
    range_header = request.headers.get("Range", "")

    # Choose least loaded Telegram client
    index = min(work_loads, key=work_loads.get)
    work_loads[index] += 1
    client = multi_clients[index]
    LOGGER.info(f"Selected client {index} for ChatID: {chat_id}, MsgID: {message_id}")

    try:
        # Use cached ByteStreamer instance
        streamer = class_cache.get(client)
        if not streamer:
            streamer = ByteStreamer(client)
            class_cache[client] = streamer
            LOGGER.info(f"Created new ByteStreamer for client {index}")

        # Retrieve file metadata
        try:
            file_id = await streamer.get_file_properties(chat_id, message_id)
        except Exception as e:
            LOGGER.error(f"Failed to get file properties: {e}")
            raise HTTPException(status_code=502, detail="Unable to fetch file properties")

        if file_id.unique_id[:6] != secure_hash:
            LOGGER.warning(f"Invalid hash for ChatID: {chat_id}, MsgID: {message_id}")
            raise InvalidHash

        file_size = file_id.file_size
        start, end = parse_range_header(range_header, file_size)

        # Chunk setup
        chunk_size = 1024 * 1024  # 1 MB
        offset = start - (start % chunk_size)
        first_part_cut = start - offset
        last_part_cut = (end - offset) % chunk_size + 1
        part_count = ((end - offset) // chunk_size) + 1

        # Sanitize filename
        file_name = file_id.file_name or f"{secrets.token_hex(2)}.unknown"
        file_name = re.sub(r'[^A-Za-z0-9._-]', '_', file_name)

        # MIME detection
        mime_type = file_id.mime_type or mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        if not file_id.file_name and "/" in mime_type:
            file_name = f"{secrets.token_hex(2)}.{mime_type.split('/')[1]}"

        LOGGER.info(
            f"Streaming {file_name} | ChatID: {chat_id} | MsgID: {message_id} | "
            f"Range: {start}-{end}/{file_size} | Client: {index}"
    )

        headers = {
            "Content-Type": mime_type,
            "Content-Length": str(end - start + 1),
            "Content-Disposition": f'inline; filename="{file_name}"',
            "Accept-Ranges": "bytes",
            "Cache-Control": "public, max-age=3600, immutable",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges",
            "ETag": f'"{file_id.unique_id}"',
        }

        if range_header:
            headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
            status_code = 206
        else:
            status_code = 200

        # For HEAD requests, send headers only
        if request.method == "HEAD":
            work_loads[index] -= 1
            return Response(status_code=status_code, headers=headers, media_type=mime_type)

        # Stream file for GET
        body = streamer.yield_file(
            file_id=file_id,
            index=index,
            offset=offset,
            first_part_cut=first_part_cut,
            last_part_cut=last_part_cut,
            part_count=part_count,
            chunk_size=chunk_size,
        )

        return StreamingResponse(
            status_code=status_code,
            content=body,
            headers=headers,
            media_type=mime_type,
        )
    except Exception:
        work_loads[index] -= 1
        raise
