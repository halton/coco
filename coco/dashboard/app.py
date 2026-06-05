"""coco.dashboard.app — FastAPI Live HUD (dashboard-001 + dashboard-002)."""
from __future__ import annotations

import asyncio
import json
import os
import struct
import sys
import time
import zlib
from pathlib import Path
from typing import AsyncIterator, Set

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel

from coco.dashboard.event_parser import parse_line


FRAME_PATH = os.environ.get("COCO_DASHBOARD_FRAME_PATH", "/tmp/coco-frame.jpg")
LOG_PATH = os.environ.get("COCO_DASHBOARD_LOG_PATH", "/tmp/coco-stdout.log")
METRICS_PATH = os.environ.get(
    "COCO_DASHBOARD_METRICS_PATH",
    str(Path.home() / ".cache" / "coco" / "metrics.jsonl"),
)

# dashboard-002: 手动按钮可触发的精准动作白名单（与 coco/actions.py 对齐）
_ALLOWED_ACTIONS = {
    "look_left", "look_right", "look_up", "look_down",
    "nod", "shake",
    "tilt_left", "tilt_right",
    "goto_sleep", "wake_up",
}

# subprocess 跑动作的超时（s）
_ACTION_TIMEOUT_S = float(os.environ.get("COCO_DASHBOARD_ACTION_TIMEOUT_S", "15"))


class ActionRequest(BaseModel):
    action: str


def _placeholder_png() -> bytes:
    """1x1 灰色 PNG（frame 文件不存在时返回，避免 500）。"""
    sig = b"\x89PNG\r\n\x1a\n"

    def _chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
    idat = _chunk(b"IDAT", zlib.compress(b"\x00\x80"))
    iend = _chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


PLACEHOLDER_PNG = _placeholder_png()


HTML_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<title>可可 Live HUD</title>
<style>
  body { margin:0; font-family: -apple-system, "PingFang SC", sans-serif;
         background:#111; color:#eee; }
  header { padding:8px 16px; background:#222; border-bottom:1px solid #333; }
  header h1 { margin:0; font-size:16px; font-weight:500; }
  #wrap { display:flex; height: calc(100vh - 38px); }
  #cam-pane { flex:1 1 60%; background:#000; display:flex; align-items:center;
              justify-content:center; }
  #cam { max-width:100%; max-height:100%; }
  #side { flex:1 1 40%; border-left:1px solid #333; display:flex;
          flex-direction:column; }
  #side h2 { margin:0; padding:8px 12px; font-size:13px; background:#1a1a1a;
             border-bottom:1px solid #333; font-weight:500; }
  #events { flex:1; overflow-y:auto; margin:0; padding:0; list-style:none; }
  #events li { padding:6px 12px; border-bottom:1px solid #222; font-size:13px;
               line-height:1.4; }
  .t { color:#888; margin-right:8px; }
  .ty-transcript { border-left:3px solid #4af; }
  .ty-reply { border-left:3px solid #4f8; }
  .ty-wake { border-left:3px solid #fa4; }
  .ty-vision { border-left:3px solid #f48; }
  .ty-tts { border-left:3px solid #af4; }
  .ty-raw { color:#666; }
  #status { padding:4px 12px; font-size:11px; color:#888;
            border-top:1px solid #333; }
  #action-panel { position:fixed; top:46px; right:10px;
                  background:rgba(0,0,0,0.6); padding:8px 10px;
                  border-radius:8px; z-index:10; max-width:220px; }
  #action-panel .title { color:#ddd; font-size:12px; margin-bottom:6px; }
  #action-panel button { margin:2px; padding:4px 8px; font-size:12px;
                         background:#333; color:#eee; border:1px solid #555;
                         border-radius:4px; cursor:pointer; }
  #action-panel button:hover { background:#444; }
  #action-panel button:active { background:#2a4; }
  #action-status { color:#fa0; font-size:11px; margin-top:4px;
                   min-height:14px; word-break:break-all; }
  .act { color:#fa0; margin-left:6px; }
</style>
</head>
<body>
<header><h1>可可 Live HUD &mdash; reachy 看到 / 听到</h1></header>
<div id="action-panel">
  <div class="title">手动动作</div>
  <button onclick="doAction('look_left')">&larr;</button>
  <button onclick="doAction('look_right')">&rarr;</button>
  <button onclick="doAction('look_up')">&uarr;</button>
  <button onclick="doAction('look_down')">&darr;</button>
  <button onclick="doAction('nod')">点头</button>
  <button onclick="doAction('shake')">摇头</button>
  <button onclick="doAction('tilt_left')">歪左</button>
  <button onclick="doAction('tilt_right')">歪右</button>
  <button onclick="doAction('goto_sleep')">睡觉</button>
  <button onclick="doAction('wake_up')">起来</button>
  <div id="action-status"></div>
</div>
<div id="wrap">
  <div id="cam-pane">
    <img id="cam" src="/stream/camera.mjpg" alt="camera stream" />
  </div>
  <div id="side">
    <h2>事件 timeline</h2>
    <ul id="events"></ul>
    <div id="status">connecting...</div>
  </div>
</div>
<script>
(function(){
  var ul = document.getElementById('events');
  var st = document.getElementById('status');
  var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  var ws = new WebSocket(proto + '//' + location.host + '/ws/events');
  ws.onopen = function(){ st.textContent = 'connected'; };
  ws.onclose = function(){ st.textContent = 'disconnected'; };
  ws.onerror = function(){ st.textContent = 'error'; };
  ws.onmessage = function(ev){
    try {
      var e = JSON.parse(ev.data);
      var li = document.createElement('li');
      li.className = 'ty-' + (e.type || 'raw');
      var t = new Date((e.ts || Date.now()/1000) * 1000);
      var hh = String(t.getHours()).padStart(2,'0');
      var mm = String(t.getMinutes()).padStart(2,'0');
      var ss = String(t.getSeconds()).padStart(2,'0');
      var mkSpan = function(cls, text){
        var s = document.createElement('span');
        if (cls) s.className = cls;
        s.textContent = text;
        return s;
      };
      var mkBold = function(text){
        var b = document.createElement('b');
        b.textContent = text;
        return b;
      };
      li.appendChild(mkSpan('t', hh+':'+mm+':'+ss));
      li.appendChild(mkSpan('ty', e.type || '?'));
      li.appendChild(document.createTextNode(' '));
      if (e.type === 'transcript') {
        li.appendChild(mkBold('user:'));
        li.appendChild(document.createTextNode(' ' + (e.transcript || '')));
        if (e.reply) {
          li.appendChild(document.createElement('br'));
          li.appendChild(mkBold('coco:'));
          li.appendChild(document.createTextNode(' ' + e.reply));
        }
        if (e.action) {
          li.appendChild(mkSpan('act', '[ACT:' + e.action + ']'));
        }
      } else if (e.type === 'wake') {
        li.appendChild(document.createTextNode('wake hit'));
      } else if (e.type === 'vision') {
        var vt = String(e.event || '');
        if (e.track_id) vt += ' track_id=' + e.track_id;
        li.appendChild(document.createTextNode(vt));
      } else if (e.type === 'tts') {
        li.appendChild(document.createTextNode('tts first_chunk_ms=' + e.first_chunk_ms));
      } else {
        li.appendChild(document.createTextNode(String(e.raw || '').slice(0,200)));
      }
      ul.insertBefore(li, ul.firstChild);
      while (ul.childNodes.length > 200) ul.removeChild(ul.lastChild);
    } catch(err) { /* ignore */ }
  };
})();

async function doAction(action){
  var st = document.getElementById('action-status');
  st.textContent = action + ' ...';
  try {
    var r = await fetch('/api/action', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({action: action})
    });
    var d = await r.json();
    st.textContent = action + ' -> ' + (d.status || ('http_'+r.status));
    console.log('action', action, d);
  } catch(e) {
    st.textContent = action + ' err: ' + e;
    console.error(e);
  }
}
</script>
</body>
</html>
"""


def create_app() -> FastAPI:
    app = FastAPI(title="coco dashboard")
    clients: Set[WebSocket] = set()

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(HTML_PAGE)

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True, "frame_path": FRAME_PATH, "log_path": LOG_PATH}

    @app.post("/api/action")
    async def post_action(req: ActionRequest) -> dict:
        """dashboard-002: 浏览器按钮触发短期 ReachyMini client 执行精准动作。

        关键设计:
        - 用 subprocess 启短期 ReachyMini client 跑 coco.actions.<name>(robot)
        - subprocess 末尾用 os._exit(0) 跳过 atexit / r.stop()，避免 zenoh
          多 client 断言风暴（参考 wiggle.py 踩坑）
        - 不耦合 coco 主进程，每次按钮独立 connect
        - 测试钩子 COCO_DASHBOARD_ACTION_FAKE=1 时跳过 subprocess（仅校验白名单）
        """
        if req.action not in _ALLOWED_ACTIONS:
            raise HTTPException(
                status_code=400,
                detail=f"unknown action: {req.action!r}",
            )

        # 测试 / mock 钩子：避免 verify 跑时真打开 ReachyMini
        if os.environ.get("COCO_DASHBOARD_ACTION_FAKE") == "1":
            return {"status": "ok", "rc": 0, "fake": True, "action": req.action}

        py = sys.executable
        script = (
            "import os, time\n"
            "from reachy_mini import ReachyMini\n"
            "from coco import actions\n"
            f"_action = {req.action!r}\n"
            "r = ReachyMini(spawn_daemon=False, media_backend='no_media')\n"
            "try:\n"
            "    try:\n"
            "        r.enable_motors()\n"
            "    except Exception:\n"
            "        pass\n"
            "    time.sleep(0.3)\n"
            "    fn = getattr(actions, _action, None)\n"
            "    if fn is None:\n"
            "        raise RuntimeError('action not found in coco.actions: ' + _action)\n"
            "    fn(r)\n"
            "    time.sleep(0.4)\n"
            "finally:\n"
            "    # 跳过 r.stop() / atexit，避免 zenoh 多 client 断言\n"
            "    os._exit(0)\n"
        )
        cmd = [py, "-c", script]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_ACTION_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
            return {"status": "timeout", "action": req.action}
        rc = proc.returncode
        return {
            "status": "ok" if rc == 0 else "error",
            "rc": rc,
            "action": req.action,
            "stdout": stdout.decode("utf-8", errors="replace")[-400:],
            "stderr": stderr.decode("utf-8", errors="replace")[-400:],
        }

    @app.get("/frame.jpg")
    async def frame_jpg() -> Response:
        try:
            with open(FRAME_PATH, "rb") as f:
                data = f.read()
            return Response(
                content=data,
                media_type="image/jpeg",
                headers={"Cache-Control": "no-store"},
            )
        except FileNotFoundError:
            return Response(
                content=PLACEHOLDER_PNG,
                media_type="image/png",
                headers={"Cache-Control": "no-store"},
            )
        except OSError:
            return Response(
                content=PLACEHOLDER_PNG,
                media_type="image/png",
                headers={"Cache-Control": "no-store"},
            )

    @app.get("/stream/camera.mjpg")
    async def stream_camera() -> StreamingResponse:
        boundary = b"frame"

        async def gen() -> AsyncIterator[bytes]:
            try:
                fps = float(os.environ.get("COCO_DASHBOARD_STREAM_FPS", "10"))
            except ValueError:
                fps = 10.0
            interval = 1.0 / max(1.0, fps)
            last_mtime = 0.0
            while True:
                payload = PLACEHOLDER_PNG
                mime = b"image/png"
                try:
                    st = os.stat(FRAME_PATH)
                    if st.st_mtime != last_mtime:
                        last_mtime = st.st_mtime
                    with open(FRAME_PATH, "rb") as f:
                        payload = f.read()
                    mime = b"image/jpeg"
                except FileNotFoundError:
                    pass
                except OSError:
                    pass
                yield (
                    b"--" + boundary + b"\r\n"
                    b"Content-Type: " + mime + b"\r\n"
                    b"Content-Length: " + str(len(payload)).encode() + b"\r\n\r\n"
                    + payload + b"\r\n"
                )
                await asyncio.sleep(interval)

        return StreamingResponse(
            gen(),
            media_type="multipart/x-mixed-replace; boundary=frame",
        )

    async def _log_tailer() -> None:
        """后台任务: tail -F LOG_PATH，解析后 broadcast。"""
        pos = 0
        # 从文件末尾开始追加
        try:
            pos = os.path.getsize(LOG_PATH)
        except OSError:
            pos = 0
        while True:
            try:
                size = os.path.getsize(LOG_PATH)
                if size < pos:
                    # 文件被截断/轮转
                    pos = 0
                if size > pos:
                    with open(LOG_PATH, "rb") as f:
                        f.seek(pos)
                        chunk = f.read(size - pos).decode("utf-8", errors="replace")
                        pos = size
                    for line in chunk.splitlines():
                        evt = parse_line(line)
                        if evt is None:
                            continue
                        msg = json.dumps(evt, ensure_ascii=False)
                        dead = []
                        for ws in list(clients):
                            try:
                                await ws.send_text(msg)
                            except Exception:  # noqa: BLE001
                                dead.append(ws)
                        for d in dead:
                            clients.discard(d)
            except FileNotFoundError:
                pass
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(0.5)

    @app.on_event("startup")
    async def _on_startup() -> None:
        asyncio.create_task(_log_tailer())

    @app.websocket("/ws/events")
    async def ws_events(ws: WebSocket) -> None:
        await ws.accept()
        clients.add(ws)
        # 推一条 hello
        try:
            await ws.send_text(json.dumps({
                "ts": time.time(), "type": "raw",
                "raw": "[dashboard] connected",
            }, ensure_ascii=False))
            while True:
                # 让 client 可以 ping
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            clients.discard(ws)

    return app


app = create_app()
