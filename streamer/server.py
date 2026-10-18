"""
Combined Large-file Streamer & File Metadata API for Telegram File Bot.
Runs on Render / VPS / Always-on server over HTTPS (valid SSL).
"""
import asyncio
import hmac
import os
import re
import sqlite3

from aiohttp import web
from pyrogram import Client

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
STREAM_KEY = os.environ["STREAM_KEY"]
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8080"))
SESSION_DIR = os.environ.get("SESSION_DIR", "session")
CHUNK = 1024 * 1024  # Telegram serves files in 1 MiB chunks

os.makedirs(SESSION_DIR, exist_ok=True)
DB_PATH = os.path.join(SESSION_DIR, "files.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS files (
                id TEXT PRIMARY KEY,
                file_id TEXT NOT NULL,
                name TEXT NOT NULL,
                mime TEXT NOT NULL,
                size INTEGER NOT NULL,
                owner INTEGER NOT NULL,
                downloads INTEGER NOT NULL DEFAULT 0,
                quality TEXT NOT NULL DEFAULT 'SD',
                group_key TEXT NOT NULL DEFAULT '',
                created TIMESTAMP DEFAULT CURRENT_TIMESTAMP
              )''')
    conn.commit()
    conn.close()

init_db()

tg = Client("streamer", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN,
            no_updates=True, workdir=SESSION_DIR)


async def health(request):
    return web.Response(text="ok")


async def api_handler(request):
    if not hmac.compare_digest(request.headers.get("X-Stream-Key", ""), STREAM_KEY):
        return web.json_response({"error": "forbidden"}, status=403)

    if request.method == 'POST':
        # Increment download hit
        if "hit" in request.query:
            file_id = request.query.get("id", "")
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("UPDATE files SET downloads = downloads + 1 WHERE id = ?", (file_id,))
            conn.commit()
            conn.close()
            return web.json_response({"ok": True})

        # Delete file
        if "del" in request.query:
            try:
                data = await request.json()
            except Exception:
                data = {}
            file_id = data.get("id", "")
            owner = int(data.get("owner", 0))
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("DELETE FROM files WHERE id = ? AND owner = ?", (file_id, owner))
            deleted = c.rowcount
            conn.commit()
            conn.close()
            return web.json_response({"deleted": deleted > 0})

        # Register or get user files
        if "myfiles" in request.query:
            owner = int(request.query.get("owner", 0))
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT * FROM files WHERE owner = ? ORDER BY created DESC LIMIT 10", (owner,))
            rows = [dict(r) for r in c.fetchall()]
            conn.close()
            return web.json_response({"files": rows})

        # Register new file
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "bad json"}, status=400)

        id_val = data.get("id")
        file_id = data.get("file_id")
        name = data.get("name")
        mime = data.get("mime")
        size = int(data.get("size", 0))
        owner = int(data.get("owner", 0))
        quality = data.get("quality", "SD")
        group_key = data.get("group_key", "")

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO files (id, file_id, name, mime, size, owner, quality, group_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                  (id_val, file_id, name, mime, size, owner, quality, group_key))
        conn.commit()
        conn.close()
        return web.json_response({"ok": True})

    # GET request: get file metadata + qualities
    file_id = request.query.get("id", "")
    if not re.match(r"^[a-f0-9]{32}$", file_id):
        return web.json_response({"error": "not found"}, status=404)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM files WHERE id = ?", (file_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return web.json_response({"error": "not found"}, status=404)

    file_data = dict(row)
    group_key = file_data.get("group_key", "")

    qualities = []
    if group_key:
        c.execute("SELECT id, file_id, name, mime, size, quality FROM files WHERE group_key = ? ORDER BY size DESC", (group_key,))
        qualities = [dict(r) for r in c.fetchall()]
    else:
        qualities = [{
            "id": file_data["id"],
            "file_id": file_data["file_id"],
            "name": file_data["name"],
            "mime": file_data["mime"],
            "size": file_data["size"],
            "quality": file_data["quality"]
        }]
    conn.close()

    return web.json_response({
        "file_id": file_data["file_id"],
        "name": file_data["name"],
        "mime": file_data["mime"],
        "size": file_data["size"],
        "quality": file_data["quality"],
        "group_key": file_data["group_key"],
        "qualities": qualities
    })


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
        chunk = await gen.__anext__()
    except StopAsyncIteration:
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
        pass
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
    app.add_routes([
        web.get("/health", health),
        web.get("/stream", stream),
        web.get("/api", api_handler),
        web.post("/api", api_handler)
    ])
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    web.run_app(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
