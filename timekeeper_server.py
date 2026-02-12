"""
学会タイムキーパー v0.2
FastAPI + Socket.IO によるリアルタイム同期サーバー

使い方:
    python timekeeper_server.py

アクセス:
    http://localhost:8000/          → トップ (QRコード付き)
    http://localhost:8000/admin     → 管理画面 (司会・スタッフ用)
    http://localhost:8000/display   → 発表者画面 (大型表示用)
"""

import asyncio
import socket
import time
import os
import io
import base64
from pathlib import Path

import uvicorn
import socketio
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

# ============================================================
# Socket.IO + FastAPI セットアップ
# ============================================================
sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",
    logger=False,
    engineio_logger=False,
)
app = FastAPI()
combined_app = socketio.ASGIApp(sio, other_asgi_app=app)

# 静的ファイル (bell.wav など)
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# ============================================================
# タイマー状態 (サーバー側で一元管理)
# ============================================================
state = {
    "running": False,
    "paused": False,
    "total_sec": 3 * 60,          # 発表時間（秒）
    "remaining_sec": 3 * 60,      # 残り時間（秒）
    "elapsed_sec": 0,             # 経過時間（秒）
    "bells": [
        {"enabled": True, "at_sec": 60,  "count": 1, "triggered": False},   # ベル1回
        {"enabled": True, "at_sec": 120, "count": 2, "triggered": False},   # ベル2回
        {"enabled": True, "at_sec": 180, "count": 3, "triggered": False},   # ベル3回 (終了)
    ],
    "over": False,
}

# タイマータスク管理
_timer_task = None
_start_wall: float = 0.0
_elapsed_at_pause: float = 0.0


def get_public_ip() -> str:
    """同一LAN内で使えるIPアドレスを取得"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def make_qr_base64(url: str) -> str:
    """QRコードをBase64文字列で返す"""
    try:
        import qrcode
        qr = qrcode.QRCode(box_size=6, border=3)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#00d4ff", back_color="#0d1f2d")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""


async def broadcast_state():
    """全クライアントに状態をブロードキャスト"""
    await sio.emit("state", state)


# ============================================================
# タイマーループ
# ============================================================
async def timer_loop():
    global state
    while state["running"] and not state["paused"]:
        await asyncio.sleep(0.1)
        now = time.time()
        elapsed = _elapsed_at_pause + (now - _start_wall)
        remaining = state["total_sec"] - elapsed

        state["elapsed_sec"] = int(elapsed)
        state["remaining_sec"] = int(remaining)
        state["over"] = remaining < 0

        # ベルトリガーチェック
        bells_fired = []
        for i, bell in enumerate(state["bells"]):
            if bell["enabled"] and not bell["triggered"]:
                if int(elapsed) >= bell["at_sec"]:
                    state["bells"][i]["triggered"] = True
                    bells_fired.append(bell["count"])

        # 状態を全クライアントへ
        payload = dict(state)
        if bells_fired:
            payload["fire_bells"] = bells_fired
        await sio.emit("state", payload)


# ============================================================
# Socket.IO イベント
# ============================================================
@sio.event
async def connect(sid, environ):
    # 接続時に現在状態を送信
    await sio.emit("state", state, to=sid)


@sio.event
async def disconnect(sid):
    pass


@sio.event
async def cmd(sid, data):
    """管理画面からのコマンド受信"""
    global state, _timer_task, _start_wall, _elapsed_at_pause

    action = data.get("action")

    if action == "start":
        if not state["running"]:
            state["running"] = True
            state["paused"] = False
            _start_wall = time.time()
            _elapsed_at_pause = state["elapsed_sec"]
            _timer_task = asyncio.ensure_future(timer_loop())
        elif state["paused"]:
            # 一時停止から再開
            state["paused"] = False
            _start_wall = time.time()
            _elapsed_at_pause = state["elapsed_sec"]
            _timer_task = asyncio.ensure_future(timer_loop())

    elif action == "pause":
        if state["running"] and not state["paused"]:
            state["paused"] = True
            _elapsed_at_pause = state["elapsed_sec"]
            if _timer_task:
                _timer_task.cancel()

    elif action == "reset":
        state["running"] = False
        state["paused"] = False
        state["elapsed_sec"] = 0
        state["remaining_sec"] = state["total_sec"]
        state["over"] = False
        _elapsed_at_pause = 0.0
        for b in state["bells"]:
            b["triggered"] = False
        if _timer_task:
            _timer_task.cancel()

    elif action == "set_total":
        # 発表時間を変更（停止中のみ）
        if not state["running"] or state["paused"]:
            mins = int(data.get("minutes", 3))
            secs = int(data.get("seconds", 0))
            total = mins * 60 + secs
            if total > 0:
                state["total_sec"] = total
                state["remaining_sec"] = total
                state["elapsed_sec"] = 0
                _elapsed_at_pause = 0.0
                for b in state["bells"]:
                    b["triggered"] = False

    elif action == "set_bell":
        idx = int(data.get("index", 0))
        if 0 <= idx < len(state["bells"]):
            mins = int(data.get("minutes", 0))
            secs = int(data.get("seconds", 0))
            state["bells"][idx]["at_sec"] = mins * 60 + secs
            state["bells"][idx]["enabled"] = bool(data.get("enabled", True))
            state["bells"][idx]["triggered"] = False

    elif action == "manual_bell":
        count = int(data.get("count", 1))
        await sio.emit("state", {**state, "fire_bells": [count]})
        return  # broadcast はここで終わり

    await broadcast_state()


# ============================================================
# ページルーティング
# ============================================================
def load_html(name: str) -> str:
    p = Path(__file__).parent / "templates" / name
    return p.read_text(encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
async def index():
    ip = get_public_ip()
    qr_admin = make_qr_base64(f"http://{ip}:8000/admin")
    qr_display = make_qr_base64(f"http://{ip}:8000/display")
    html = load_html("index.html")
    html = html.replace("{{IP}}", ip)
    html = html.replace("{{QR_ADMIN}}", qr_admin)
    html = html.replace("{{QR_DISPLAY}}", qr_display)
    return HTMLResponse(html)


@app.get("/admin", response_class=HTMLResponse)
async def admin():
    return HTMLResponse(load_html("admin.html"))


@app.get("/display", response_class=HTMLResponse)
async def display():
    return HTMLResponse(load_html("display.html"))


# ============================================================
# エントリーポイント
# ============================================================
if __name__ == "__main__":
    ip = get_public_ip()
    print("=" * 50)
    print("  学会タイムキーパー v0.2")
    print("=" * 50)
    print(f"  トップ  : http://{ip}:8000/")
    print(f"  管理画面: http://{ip}:8000/admin")
    print(f"  発表者  : http://{ip}:8000/display")
    print("=" * 50)
    print("  終了: Ctrl+C")
    print()
    uvicorn.run(
        "timekeeper_server:combined_app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="warning",
    )
