import zlib
import json
import asyncio
from concurrent.futures import ThreadPoolExecutor

executor = ThreadPoolExecutor()

def compress_data(data):
    """Compresses data using zlib.

    Args:
        data: The data to compress.

    Returns:
        The compressed data.
    """
    return zlib.compress(data.encode(), level=zlib.Z_BEST_COMPRESSION)

def decompress_data(data):
    """Decompresses data using zlib.

    Args:
        data: The data to decompress.

    Returns:
        The decompressed data.
    """
    return zlib.decompress(data).decode()

def base62_encode(data):
    """Encodes data using base62.

    Args:
        data: The data to encode.

    Returns:
        The base62-encoded data.
    """
    BASE62_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    num = int.from_bytes(data, 'big')
    base62 = []
    while num:
        num, rem = divmod(num, 62)
        base62.append(BASE62_ALPHABET[rem])
    return ''.join(reversed(base62)) or '0'

def base62_decode(data):
    """Decodes data using base62.

    Args:
        data: The data to decode.

    Returns:
        The base62-decoded data.
    """
    BASE62_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    num = 0
    for char in data:
        num = num * 62 + BASE62_ALPHABET.index(char)
    return num.to_bytes((num.bit_length() + 7) // 8, 'big') or b'\0'

async def async_compress_data(data):
    """Asynchronously compresses data using zlib.

    Args:
        data: The data to compress.

    Returns:
        The compressed data.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, compress_data, data)

async def async_decompress_data(data):
    """Asynchronously decompresses data using zlib.

    Args:
        data: The data to decompress.

    Returns:
        The decompressed data.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, decompress_data, data)

async def async_base62_encode(data):
    """Asynchronously encodes data using base62.

    Args:
        data: The data to encode.

    Returns:
        The base62-encoded data.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, base62_encode, data)

async def async_base62_decode(data):
    """Asynchronously decodes data using base62.

    Args:
        data: The data to decode.

    Returns:
        The base62-decoded data.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, base62_decode, data)

async def encode_string(data):
    """Encodes a string by compressing and then base62-encoding it.

    Args:
        data: The data to encode.

    Returns:
        The encoded string.
    """
    json_data = json.dumps(data)
    compressed_data = await async_compress_data(json_data)
    return await async_base62_encode(compressed_data)

async def decode_string(encoded_data):
    """Decodes a string by base62-decoding and then decompressing it.

    Args:
        encoded_data: The data to decode.

    Returns:
        The decoded data.
    """
    compressed_data = await async_base62_decode(encoded_data)
    json_data = await async_decompress_data(compressed_data)
    return json.loads(json_data)
