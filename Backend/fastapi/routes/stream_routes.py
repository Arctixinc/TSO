import math
import secrets
import mimetypes
from typing import Tuple
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse

from Backend.helper.encrypt import decode_string
from Backend.helper.exceptions import InvalidHash
from Backend.helper.custom_dl import ByteStreamer
from Backend.pyrofork.bot import StreamBot, work_loads, multi_clients

router = APIRouter(tags=["Streaming"])
class_cache = {}


def parse_range_header(range_header: str, file_size: int) -> Tuple[int, int]:
    if not range_header:
        return 0, file_size - 1
    try:
        range_value = range_header.replace("bytes=", "")
        start_str, end_str = range_value.split("-")
        start = int(start_str)
        end = int(end_str) if end_str else file_size - 1
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid Range header: {e}")

    if start < 0 or end >= file_size or start > end:
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
    message = await StreamBot.get_messages(int(chat_id), int(decoded_data["msg_id"]))
    file = message.video or message.document
    file_hash = file.file_unique_id[:6]

    return await media_streamer(
        request,
        chat_id=int(chat_id),
        message_id=int(decoded_data["msg_id"]),
        secure_hash=file_hash
    )


async def media_streamer(
    request: Request,
    chat_id: int,
    message_id: int,
    secure_hash: str,
) -> StreamingResponse:
    """
    Streams a Telegram file with Range support.
    Returns headers only for HEAD requests.
    """
    range_header = request.headers.get("Range", "")

    # Select the least loaded client
    index = min(work_loads, key=work_loads.get)
    client = multi_clients[index]

    # Reuse or create ByteStreamer for this client
    streamer = class_cache.get(client)
    if not streamer:
        streamer = ByteStreamer(client)
        class_cache[client] = streamer

    # Get file properties
    file_id = await streamer.get_file_properties(chat_id, message_id)
    if file_id.unique_id[:6] != secure_hash:
        raise InvalidHash

    file_size = file_id.file_size
    start, end = parse_range_header(range_header, file_size)

    chunk_size = 1024 * 1024  # 1MB chunks
    offset = start - (start % chunk_size)
    first_part_cut = start - offset
    last_part_cut = (end - offset) % chunk_size + 1
    part_count = ((end - offset) // chunk_size) + 1

    # File name & MIME type
    file_name = file_id.file_name or f"{secrets.token_hex(2)}.unknown"
    mime_type = file_id.mime_type or mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    if not file_id.file_name and "/" in mime_type:
        file_name = f"{secrets.token_hex(2)}.{mime_type.split('/')[1]}"

    headers = {
        "Content-Type": mime_type,
        "Content-Length": str(end - start + 1),
        "Content-Disposition": f'inline; filename="{file_name}"',
        "Accept-Ranges": "bytes",
        "Cache-Control": "public, max-age=3600, immutable",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges",
    }

    if range_header:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        status_code = 206
    else:
        status_code = 200

    # Return only headers for HEAD requests
    if request.method == "HEAD":
        return StreamingResponse(
            status_code=status_code,
            content=None,
            headers=headers,
            media_type=mime_type,
        )

    # Return actual streaming response for GET
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
