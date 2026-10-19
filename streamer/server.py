"""
Unified Docker-ready Python Backend with Supabase Database for Telegram File Bot.
Combines:
  1. Telegram Bot Webhook handler (/webhook)
  2. File Metadata API (/api) with Supabase Postgres
  3. MTProto Large-File Streamer (/stream)
Powered by Pyrogram / Kurigram, Aiohttp, and Supabase.
"""
import asyncio
import hmac
import os
import re
import aiohttp
from aiohttp import web
from pyrogram import Client

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
STREAM_KEY = os.environ["STREAM_KEY"]
WORKER_URL = os.environ.get("WORKER_URL", "https://dawn-wildflower-1f83.yaoc.workers.dev")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "K8mQ2xVn7RtP4wZc9LbY3hJdF6sA1eUg")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8080"))
SESSION_DIR = os.environ.get("SESSION_DIR", "session")
CHUNK = 1024 * 1024  # 1 MiB chunks

os.makedirs(SESSION_DIR, exist_ok=True)

tg = Client("streamer", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN,
            no_updates=True, workdir=SESSION_DIR)


async def health(request):
    return web.Response(text="ok")


# --- Supabase Database Helpers ---
async def db_insert_file(file_data):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
    url = f"{SUPABASE_URL}/rest/v1/files"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates"
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=file_data, headers=headers) as resp:
            return await resp.text()

async def db_get_file(file_id):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    url = f"{SUPABASE_URL}/rest/v1/files?id=eq.{file_id}&select=*"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}"
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data[0] if data else None
    return None

async def db_get_group(group_key):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    url = f"{SUPABASE_URL}/rest/v1/files?group_key=eq.{group_key}&select=id,file_id,name,mime,size,quality&order=size.desc"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}"
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                return await resp.json()
    return []

async def db_increment_downloads(file_id):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
    file_data = await db_get_file(file_id)
    if not file_data:
        return
    new_downloads = file_data.get("downloads", 0) + 1
    url = f"{SUPABASE_URL}/rest/v1/files?id=eq.{file_id}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }
    async with aiohttp.ClientSession() as session:
        async with session.patch(url, json={"downloads": new_downloads}, headers=headers) as resp:
            pass

async def db_get_user_files(owner):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    url = f"{SUPABASE_URL}/rest/v1/files?owner=eq.{owner}&select=*&order=created.desc&limit=10"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}"
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                return await resp.json()
    return []

async def db_delete_file(file_id, owner):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    url = f"{SUPABASE_URL}/rest/v1/files?id=eq.{file_id}&owner=eq.{owner}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}"
    }
    async with aiohttp.ClientSession() as session:
        async with session.delete(url, headers=headers) as resp:
            return resp.status in (200, 204)


async def telegram_api(method, data):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=data) as resp:
            return await resp.json()


def parse_quality_and_group(filename):
    quality = "SD"
    lower = filename.lower()
    if re.search(r'\b(2160p|4k|uhd)\b', lower):
        quality = "4K"
    elif re.search(r'\b(1080p|fhd|full[-_\s]?hd)\b', lower):
        quality = "1080p"
    elif re.search(r'\b(720p|hd)\b', lower):
        quality = "720p"
    elif re.search(r'\b(480p)\b', lower):
        quality = "480p"
    elif re.search(r'\b(360p)\b', lower):
        quality = "360p"
    elif re.search(r'\b(240p)\b', lower):
        quality = "240p"

    base = re.sub(r'\.[^.]+$', '', filename)
    clean = re.sub(r'\b(2160p|4k|uhd|1080p|fhd|full[-_\s]?hd|720p|hd|480p|sd|360p|240p|hevc|x264|x265|aac|dts|bluray|web[-_\s]?dl|rip)\b', '', base, flags=re.IGNORECASE)
    clean = re.sub(r'[^a-z0-9]', '', clean.lower())
    return quality, clean or "video"


async def webhook_handler(request):
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
        return web.Response(status=403, text="forbidden")

    try:
        update = await request.json()
    except Exception:
        return web.Response(status=400, text="bad request")

    if not update or "message" not in update:
        return web.Response(text="ok")

    msg = update["message"]
    chat_id = msg["chat"]["id"]
    uid = msg["from"]["id"]

    # Text commands
    if "text" in msg:
        t = msg["text"].strip()
        if t.startswith("/start") or t.startswith("/help"):
            await telegram_api("sendMessage", {
                "chat_id": chat_id,
                "text": "👋 Send me any video up to 2 GB and I'll give you **Direct MP4 Stream Links** with **Quality Support**.\n\n/myfiles – your last 10 files\n/del <id> – delete a link",
                "parse_mode": "HTML"
            })
            return web.Response(text="ok")
        elif t.startswith("/myfiles"):
            rows = await db_get_user_files(uid)
            out = ""
            for r in rows:
                link = f"{WORKER_URL}/s/{r['id']}/video.mp4"
                out += f"🎬 <b>{r['name']}</b> [<b>{r['quality']}</b>] ({round(r['size']/1048576, 2)} MB)\n{link}\n<code>/del {r['id']}</code>\n\n"

            await telegram_api("sendMessage", {
                "chat_id": chat_id,
                "text": out or "You have no files yet.",
                "parse_mode": "HTML"
            })
            return web.Response(text="ok")
        elif t.startswith("/del"):
            m = re.match(r'^/del\s+([a-f0-9]{32})', t)
            if m:
                file_id = m.group(1)
                deleted = await db_delete_file(file_id, uid)
                await telegram_api("sendMessage", {
                    "chat_id": chat_id,
                    "text": "🗑 Link deleted." if deleted else "❌ File not found.",
                    "parse_mode": "HTML"
                })
            return web.Response(text="ok")

    # Detect video/file
    f = None
    name = "video.mp4"
    mime = "video/mp4"
    for key in ["document", "video", "animation", "video_note"]:
        if key in msg:
            f = msg[key]
            name = f.get("file_name", "video.mp4")
            mime = f.get("mime_type", "video/mp4")
            break

    if not f:
        await telegram_api("sendMessage", {"chat_id": chat_id, "text": "Please send a video file.", "parse_mode": "HTML"})
        return web.Response(text="ok")

    size = int(f.get("file_size", 0))
    if size <= 0:
        await telegram_api("sendMessage", {"chat_id": chat_id, "text": "❌ Could not determine file size.", "parse_mode": "HTML"})
        return web.Response(text="ok")

    file_id_tg = f["file_id"]
    id_val = os.urandom(16).hex()
    name = re.sub(r'[^\w.\- ]+', '_', name)[:200]
    mime = mime or "video/mp4"

    quality, group_key = parse_quality_and_group(name)

    await db_insert_file({
        "id": id_val,
        "file_id": file_id_tg,
        "name": name,
        "mime": mime,
        "size": size,
        "owner": uid,
        "quality": quality,
        "group_key": group_key
    })

    group_files = await db_get_group(group_key)

    link = f"{WORKER_URL}/s/{id_val}/video.mp4"
    text = f"✅ <b>{name}</b>\n🎬 <b>Detected Quality:</b> <code>{quality}</code>\n📦 {round(size/1048576, 2)} MB\n\n▶️ <b>Direct MP4 Stream Link ($quality):</b>\n<code>{link}</code>\n"

    inline_keyboard = [[{"text": f"▶️ {quality}", "url": link}]]
    if len(group_files) > 1:
        text += "\n🌟 <b>All Available Qualities:</b>\n"
        for gf in group_files:
            if gf["id"] != id_val:
                gf_link = f"{WORKER_URL}/s/{gf['id']}/video.mp4"
                text += f"• [<b>{gf['quality']}</b>] {gf['name']}\n<code>{gf_link}</code>\n"

    await telegram_api("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": inline_keyboard}
    })
    return web.Response(text="ok")


async def api_handler(request):
    if not hmac.compare_digest(request.headers.get("X-Stream-Key", ""), STREAM_KEY):
        return web.json_response({"error": "forbidden"}, status=403)

    file_id = request.query.get("id", "")
    if request.method == 'POST' and "hit" in request.query:
        await db_increment_downloads(file_id)
        return web.json_response({"ok": True})

    if not re.match(r"^[a-f0-9]{32}$", file_id):
        return web.json_response({"error": "not found"}, status=404)

    file_data = await db_get_file(file_id)
    if not file_data:
        return web.json_response({"error": "not found"}, status=404)

    group_key = file_data.get("group_key", "")
    qualities = await db_get_group(group_key) if group_key else [file_data]

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
        web.post("/api", api_handler),
        web.post("/webhook", webhook_handler),
        web.get("/webhook", webhook_handler)
    ])
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    web.run_app(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
