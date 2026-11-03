#!/usr/bin/env python3
"""
ntfy-relay: 带断点续发的 ntfy 中继服务 (stdlib only, no pip deps)

调用方 POST /send -> 立即尝试转发到 ntfy
  成功: 直接返回 delivered
  失败: 写入 SQLite 队列, 后台 worker 指数退避重试, 成功后出队

环境变量:
  NTFY_URL  ntfy 服务地址, 默认 http://ntfy:80
  DB_PATH   队列数据库路径, 默认 /data/queue.db
  PORT      监听端口, 默认 8080
"""
import json
import os
import sqlite3
import threading
import time
import urllib.request
import urllib.error

NTFY_URL = os.environ.get("NTFY_URL", "http://ntfy:80").rstrip("/")
DB_PATH = os.environ.get("DB_PATH", "/data/queue.db")
PORT = int(os.environ.get("PORT", "8080"))
MAX_ATTEMPTS = 20
RETRY_INTERVAL = 15

BACKOFF = [30, 120, 600, 3600, 21600]  # 30s, 2m, 10m, 1h, 6h, 之后每天


def backoff(attempts: int) -> int:
    return BACKOFF[min(attempts, len(BACKOFF) - 1)] if attempts < len(BACKOFF) else 86400


os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.execute(
    """CREATE TABLE IF NOT EXISTS queue(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        topic TEXT NOT NULL, title TEXT DEFAULT '', message TEXT DEFAULT '',
        priority TEXT DEFAULT 'default', tags TEXT DEFAULT '', click TEXT DEFAULT '',
        attempts INTEGER DEFAULT 0, next_retry_at REAL NOT NULL, created_at REAL NOT NULL
    )"""
)
db.execute("CREATE INDEX IF NOT EXISTS idx_due ON queue(next_retry_at, attempts)")
db.commit()
db_lock = threading.Lock()


def ntfy_post(topic, title, message, priority, tags, click, timeout=10) -> bool:
    headers = {"Title": title or "", "Priority": priority or "default"}
    if tags:
        headers["Tags"] = tags
    if click:
        headers["Click"] = click
    req = urllib.request.Request(
        f"{NTFY_URL}/{urllib.request.quote(topic, safe='')}",
        data=(message or "").encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status in (200, 201)


def enqueue(topic, title, message, priority, tags, click) -> int:
    now = time.time()
    with db_lock:
        cur = db.execute(
            "INSERT INTO queue(topic,title,message,priority,tags,click,attempts,next_retry_at,created_at)"
            " VALUES(?,?,?,?,?,?,0,?,?)",
            (topic, title, message, priority, tags, click, now + BACKOFF[0], now),
        )
        db.commit()
        return cur.lastrowid


def worker():
    while True:
        time.sleep(RETRY_INTERVAL)
        now = time.time()
        with db_lock:
            rows = db.execute(
                "SELECT id,topic,title,message,priority,tags,click,attempts FROM queue"
                " WHERE next_retry_at <= ? AND attempts < ? ORDER BY next_retry_at LIMIT 20",
                (now, MAX_ATTEMPTS),
            ).fetchall()
        for rid, topic, title, message, priority, tags, click, attempts in rows:
            try:
                if ntfy_post(topic, title, message, priority, tags, click):
                    with db_lock:
                        db.execute("DELETE FROM queue WHERE id=?", (rid,))
                        db.commit()
                    print(f"[relay] delivered id={rid} topic={topic}", flush=True)
                    continue
                raise RuntimeError("bad status")
            except Exception as e:  # noqa: BLE001 - 任何失败都入重试
                nxt = time.time() + backoff(attempts)
                with db_lock:
                    if attempts + 1 >= MAX_ATTEMPTS:
                        db.execute("DELETE FROM queue WHERE id=?", (rid,))
                        print(f"[relay] dropped id={rid} after {MAX_ATTEMPTS} attempts: {e}", flush=True)
                    else:
                        db.execute(
                            "UPDATE queue SET attempts=attempts+1, next_retry_at=? WHERE id=?",
                            (nxt, rid),
                        )
                        print(f"[relay] retry id={rid} attempt={attempts+1} next={nxt}: {e}", flush=True)
                    db.commit()


from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402


class Handler(BaseHTTPRequestHandler):
    server_version = "ntfy-relay/1.0"

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            return self._json(200, {"ok": True})
        if self.path == "/queue":
            with db_lock:
                rows = db.execute(
                    "SELECT id,topic,title,attempts,next_retry_at,created_at FROM queue ORDER BY id"
                ).fetchall()
            return self._json(200, {"pending": [
                {"id": r[0], "topic": r[1], "title": r[2], "attempts": r[3],
                 "next_retry_at": r[4], "created_at": r[5]} for r in rows
            ]})
        return self._json(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if self.path != "/send":
            return self._json(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self._json(400, {"error": "invalid json"})
        topic = (data.get("topic") or "").strip()
        if not topic:
            return self._json(400, {"error": "topic required"})
        title = data.get("title", "")
        message = data.get("message", "")
        priority = data.get("priority", "default")
        tags = data.get("tags", "")
        click = data.get("click", "")
        try:
            if ntfy_post(topic, title, message, priority, tags, click):
                return self._json(200, {"status": "delivered"})
        except Exception as e:  # noqa: BLE001
            print(f"[relay] direct send failed, enqueue: {e}", flush=True)
        qid = enqueue(topic, title, message, priority, tags, click)
        return self._json(202, {"status": "queued", "id": qid})

    def log_message(self, *args):  # 安静日志
        pass


if __name__ == "__main__":
    threading.Thread(target=worker, daemon=True).start()
    print(f"[relay] listening :{PORT}, upstream {NTFY_URL}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
