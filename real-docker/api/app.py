"""
app.py
A tiny shop API for Lifeguard-Agent's real Docker mode, backed by PostgreSQL and Redis like the
simulated shop. Plain Python (http.server), plus the database and cache libraries.

  GET  /api/health     200 when the database and the cache answer, 503 when one doesn't
  GET  /api/products   the products (cached in Redis for 30 s)
  GET  /api/cart       the visitor's cart, kept in Redis: without the cache, customers can't add anything
  POST /api/orders     places an order in the database

Version 2.3.2 has a bug on purpose: it reads the database password from a new setting,
SHOP_DB_PASSWORD, that the deploy never filled in, so it stops at once and keeps restarting.
That's the "bad deploy" a rollback to 2.3.1 fixes.
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg
import redis

VERSION = os.environ.get("APP_VERSION", "2.3.1")

if VERSION == "2.3.2":
    PASSWORD = os.environ.get("SHOP_DB_PASSWORD")
    if not PASSWORD:
        print("FATAL config error: SHOP_DB_PASSWORD is not set (new in 2.3.2)", flush=True)
        sys.exit(1)
else:
    PASSWORD = os.environ.get("DB_PASSWORD", "shop")

DATABASE = f"host=db dbname=shop user=shop password={PASSWORD} connect_timeout=3"
CACHE = redis.Redis(host="cache", port=6379, socket_timeout=2, socket_connect_timeout=2)


def log(text: str) -> None:
    """One log line, the way docker logs shows it."""
    print(text, flush=True)


class Shop(BaseHTTPRequestHandler):
    """Answers the shop's API requests."""

    def answer(self, status: int, data) -> None:
        """Send a JSON answer and log the request like a web framework would."""
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        log(f"{'INFO' if status < 400 else 'ERROR'} {self.command} {self.path} {status}")

    def log_message(self, format, *args) -> None:
        """Quiet: answer() already writes one clear line per request."""

    def do_GET(self) -> None:
        try:
            if self.path == "/api/health":
                with psycopg.connect(DATABASE) as connection:
                    connection.execute("SELECT 1")
                CACHE.ping()
                self.answer(200, {"status": "ok", "version": VERSION})
            elif self.path == "/api/products":
                cached = CACHE.get("products")
                if cached:
                    self.answer(200, json.loads(cached))
                    return
                with psycopg.connect(DATABASE) as connection:
                    rows = connection.execute("SELECT id, name, price::text FROM products ORDER BY id").fetchall()
                products = [{"id": r[0], "name": r[1], "price": r[2]} for r in rows]
                CACHE.setex("products", 30, json.dumps(products))
                self.answer(200, products)
            elif self.path == "/api/cart":
                self.answer(200, {"items": [item.decode() for item in CACHE.lrange("cart:demo", 0, -1)]})
            else:
                self.answer(404, {"error": "not found"})
        except redis.RedisError as error:
            log(f"ERROR cache unreachable: {error}")
            self.answer(503, {"error": "Service unavailable"})
        except psycopg.Error as error:
            log(f"ERROR database: {error}".replace("\n", " "))
            self.answer(503, {"error": "Service unavailable"})

    def do_POST(self) -> None:
        try:
            if self.path == "/api/orders":
                with psycopg.connect(DATABASE) as connection:
                    connection.execute("INSERT INTO orders (product_id) VALUES (1)")
                CACHE.rpush("cart:demo", "order placed")
                self.answer(201, {"status": "created"})
            else:
                self.answer(404, {"error": "not found"})
        except (redis.RedisError, psycopg.Error) as error:
            log(f"ERROR POST /api/orders failed: {error}".replace("\n", " "))
            self.answer(500, {"error": "Something went wrong"})


if __name__ == "__main__":
    log(f"INFO shop api {VERSION} listening on :8000")
    ThreadingHTTPServer(("0.0.0.0", 8000), Shop).serve_forever()
