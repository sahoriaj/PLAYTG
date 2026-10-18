"""
Large-file streamer for the Telegram file bot.

Why it exists: the normal Telegram Bot API only lets a bot download files up to 20 MB.
Over MTProto (what Telegram apps use) a bot can read files up to 2 GB. This tiny server
logs in with the SAME bot token over MTProto and streams a file by its Bot API file_id,
with HTTP Range support (seeking / resume). Only the Cloudflare Worker talks to it.

Env vars: API_ID, API_HASH (from https://my.telegram.org), BOT_TOKEN, STREAM_KEY (shared secret with the Worker)
Optional: HOST (default 127.0.0.1), PORT (default 8080), SESSION_DIR (default ./session)
"""
import asyncio
import hmac
import os
import re

from aiohttp import web
from pyrogram import Client

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
STREAM_KEY = os.environ["STREAM_KEY"]
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8080"))
SESSION_DIR = os.environ.get("SESSION_DIR", "session")
CHUNK = 1024 * 1024  # Telegram serves files in 1 MiB chunks

os.makedirs(SESSION_DIR, exist_ok=True)
tg = Client("streamer", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN,
            no_updates=True, workdir=SESSION_DIR)


async def health(request):
    return web.Response(text="ok")


async def stream(request):
    if not hmac.compare_digest(request.headers.get("X-Stream-Key", ""), STREAM_KEY):
        return web.Response(status=403, text="forbidden")

    file_id = request.query.get("file_id", "")
    try:
        size = int(request.query.get("size", "0"))
    except ValueError:
        size = 0
    if not file_id or size <= 0:
        return web.Response(status=400, text="bad request")

    # ---- Range ----
    start, end, status = 0, size - 1, 200
    m = re.match(r"bytes=(\d*)-(\d*)$", request.headers.get("Range", "").strip())
    if m and (m.group(1) or m.group(2)):
        if not m.group(1):
            start = max(0, size - int(m.group(2)))
        else:
            start = int(m.group(1))
            if m.group(2):
                end = min(int(m.group(2)), size - 1)
        if start > end or start >= size:
            return web.Response(status=416, headers={"Content-Range": f"bytes */{size}"})
        status = 206
    length = end - start + 1

    headers = {
        "Content-Type": "application/octet-stream",
        "Content-Length": str(length),
        "Accept-Ranges": "bytes",
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"

    first_chunk_index = start // CHUNK
    skip = start % CHUNK
    n_chunks = end // CHUNK - first_chunk_index + 1

    gen = tg.stream_media(file_id, limit=n_chunks, offset=first_chunk_index)
    try:
        chunk = await gen.__anext__()          # fetch the first chunk BEFORE sending headers,
    except StopAsyncIteration:                 # so a bad file_id gives a clean error
        return web.Response(status=404, text="empty or missing file")
    except Exception as e:
        return web.Response(status=502, text=f"telegram error: {e}")

    resp = web.StreamResponse(status=status, headers=headers)
    await resp.prepare(request)
    remaining = length
    try:
        while True:
            if skip:
                chunk, skip = chunk[skip:], 0
            if len(chunk) > remaining:
                chunk = chunk[:remaining]
            await resp.write(chunk)
            remaining -= len(chunk)
            if remaining <= 0:
                break
            try:
                chunk = await gen.__anext__()
            except StopAsyncIteration:
                break
    except (ConnectionResetError, ConnectionError):
        pass                                   # client closed the connection (seek / stop)
    finally:
        await gen.aclose()
    try:
        await resp.write_eof()
    except Exception:
        pass
    return resp


async def on_startup(app):
    await tg.start()


async def on_cleanup(app):
    await tg.stop()


def main():
    app = web.Application()
    app.add_routes([web.get("/health", health), web.get("/stream", stream)])
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    web.run_app(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
