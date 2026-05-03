"""
main.py - SwiftDeploy API service.

Runs in stable or canary mode controlled by the MODE environment variable.
Canary mode adds X-Mode: canary to every response and activates the chaos endpoint.
"""

import os
import time
import random
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MODE = os.environ.get("MODE", "stable").lower()
APP_VERSION = os.environ.get("APP_VERSION", "1.0.0")
APP_PORT = int(os.environ.get("APP_PORT", "3000"))

START_TIME = time.time()

# Chaos state
_chaos_mode: Optional[str] = None
_chaos_duration: int = 0
_chaos_rate: float = 0.0
_chaos_active_until: float = 0.0

app = FastAPI(title="SwiftDeploy Service", version=APP_VERSION)


class ChaosMiddleware:
    """
    Applied only in canary mode.
    Injects X-Mode header and applies active chaos behaviour.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_header(message):
            if message["type"] == "http.response.start" and MODE == "canary":
                headers = list(message.get("headers", []))
                headers.append((b"x-mode", b"canary"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_header)


if MODE == "canary":
    app.add_middleware(ChaosMiddleware)


async def apply_chaos():
    """Apply active chaos effects. Called at the start of each request."""
    global _chaos_mode, _chaos_active_until

    if _chaos_mode is None:
        return None

    now = time.time()

    if _chaos_mode == "slow":
        if now < _chaos_active_until:
            await asyncio.sleep(_chaos_duration)
        else:
            _chaos_mode = None
        return None

    if _chaos_mode == "error":
        if now < _chaos_active_until:
            if random.random() < _chaos_rate:
                return JSONResponse(
                    status_code=500,
                    content={"error": "chaos error injection", "mode": "canary"},
                )
        else:
            _chaos_mode = None
        return None

    return None


@app.get("/")
async def root(request: Request):
    chaos_response = await apply_chaos() if MODE == "canary" else None
    if chaos_response:
        return chaos_response

    return JSONResponse(content={
        "message": f"Welcome to SwiftDeploy service",
        "mode": MODE,
        "version": APP_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@app.get("/healthz")
async def healthz():
    uptime = int(time.time() - START_TIME)
    return JSONResponse(content={
        "status": "ok",
        "mode": MODE,
        "version": APP_VERSION,
        "uptime_seconds": uptime,
    })


@app.post("/chaos")
async def chaos(request: Request):
    global _chaos_mode, _chaos_duration, _chaos_rate, _chaos_active_until

    if MODE != "canary":
        return JSONResponse(
            status_code=403,
            content={"error": "chaos endpoint only available in canary mode"},
        )

    body = await request.json()
    mode = body.get("mode")

    if mode == "slow":
        duration = int(body.get("duration", 5))
        _chaos_mode = "slow"
        _chaos_duration = duration
        _chaos_active_until = time.time() + 3600  # active for 1 hour
        return JSONResponse(content={
            "chaos": "slow",
            "duration": duration,
            "message": f"responses will be delayed by {duration}s",
        })

    elif mode == "error":
        rate = float(body.get("rate", 0.5))
        _chaos_mode = "error"
        _chaos_rate = rate
        _chaos_active_until = time.time() + 3600
        return JSONResponse(content={
            "chaos": "error",
            "rate": rate,
            "message": f"~{int(rate * 100)}% of requests will return 500",
        })

    elif mode == "recover":
        _chaos_mode = None
        _chaos_duration = 0
        _chaos_rate = 0.0
        _chaos_active_until = 0.0
        return JSONResponse(content={
            "chaos": "recover",
            "message": "chaos cancelled, service returning to normal",
        })

    return JSONResponse(
        status_code=400,
        content={"error": f"unknown chaos mode: {mode}"},
    )


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=APP_PORT, log_level="info")
