from http.server import BaseHTTPRequestHandler
import json
import urllib.parse
import os
import sqlite3
import re

STREAM_KEY = os.environ.get("STREAM_KEY", "K8mQ2xVn7RtP4wZc9LbY3hJdF6sA1eUgg")
DB_PATH = "/tmp/files.db"

def init_db():
    try:
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
    except Exception:
        pass

init_db()

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed_path = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed_path.query)

        auth_key = self.headers.get("X-Stream-Key") or self.headers.get("X-Api-Key") or ""
        if auth_key != STREAM_KEY:
            self.send_response(403)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"error": "forbidden"}).encode())
            return

        file_id = query.get("id", [""])[0]
        if not re.match(r"^[a-f0-9]{32}$", file_id):
            self.send_response(404)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"error": "not found"}).encode())
            return

        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT * FROM files WHERE id = ?", (file_id,))
            row = c.fetchone()
            if not row:
                conn.close()
                self.send_response(404)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": "not found"}).encode())
                return

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

            response_data = {
                "file_id": file_data["file_id"],
                "name": file_data["name"],
                "mime": file_data["mime"],
                "size": file_data["size"],
                "quality": file_data["quality"],
                "group_key": file_data["group_key"],
                "qualities": qualities
            }
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(response_data).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def do_POST(self):
        parsed_path = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed_path.query)

        auth_key = self.headers.get("X-Stream-Key") or self.headers.get("X-Api-Key") or ""
        if auth_key != STREAM_KEY:
            self.send_response(403)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"error": "forbidden"}).encode())
            return

        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        try:
            if "hit" in query:
                file_id = query.get("id", [""])[0]
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute("UPDATE files SET downloads = downloads + 1 WHERE id = ?", (file_id,))
                conn.commit()
                conn.close()
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True}).encode())
                return

            data = json.loads(body.decode('utf-8')) if body else {}
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

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())
