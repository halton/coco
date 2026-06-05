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
from typing import AsyncIterator, List, Optional, Set

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
# interact-042: 扩 10→16 (3 antenna + 3 body_yaw)，与 LLM ACTION_TOOL_ENUM / HTML 按钮一致
_ALLOWED_ACTIONS = {
    "look_left", "look_right", "look_up", "look_down",
    "nod", "shake",
    "tilt_left", "tilt_right",
    "goto_sleep", "wake_up",
    # interact-042
    "wiggle_antennas", "perk_up", "droop_antennas",
    "turn_body_left", "turn_body_right", "turn_body_center",
}

# subprocess 跑动作的超时（s）
_ACTION_TIMEOUT_S = float(os.environ.get("COCO_DASHBOARD_ACTION_TIMEOUT_S", "15"))


# dashboard-005: LLM model 热切（不重启 coco）
# dashboard 把 model 名写入 runtime_config.json，coco/llm.py 每 30s check 一次。
_RUNTIME_CONFIG_PATH = Path(
    os.environ.get(
        "COCO_RUNTIME_CONFIG_PATH",
        str(Path.home() / ".cache" / "coco" / "runtime_config.json"),
    )
)
_ALLOWED_MODELS = {
    "gpt-4o-mini",
    "gpt-4o",
    "gpt-4.1",
    "claude-sonnet-4.5",
    "claude-opus-4.7",
    "gemini-2.5-pro",
}

# dashboard-007: 感知 toggle 白名单（face_id / wake-word 软开关，hot-reload 30s 生效）
# 主程序侧：coco/perception/face_id.py + coco/wake_word.py 在 identify/feed 入口
# 节流读 runtime_config.json，按 face_id_enabled/wake_enabled 切自身状态。
_ALLOWED_PERCEPTION_KEYS = {"face_id_enabled", "wake_enabled"}


class ActionRequest(BaseModel):
    action: str


class ModelRequest(BaseModel):
    model: str


class TogglePerceptionRequest(BaseModel):
    enabled: bool


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
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>可可 Live HUD</title>
<style>
  body { margin:0; font-family: -apple-system, "PingFang SC", sans-serif;
         background:#111; color:#eee; }
  header { padding:8px 16px; background:#222; border-bottom:1px solid #333; }
  header h1 { margin:0; font-size:16px; font-weight:500; }
  /* dashboard-006: 三栏 CSS Grid 布局 (camera / timeline+perf / control) */
  #wrap { display:grid;
          grid-template-columns: minmax(400px,1fr) minmax(400px,1.2fr) 320px;
          height: calc(100vh - 38px); }
  #cam-pane { background:#000; display:flex; align-items:center;
              justify-content:center; overflow:hidden; }
  #cam { max-width:100%; max-height:100%; }
  #side { border-left:1px solid #333; display:flex;
          flex-direction:column; overflow:hidden; }
  #side h2 { margin:0; padding:8px 12px; font-size:13px; background:#1a1a1a;
             border-bottom:1px solid #333; font-weight:500; }
  #events { flex:1; overflow-y:auto; margin:0; padding:0; list-style:none; }
  #events li { padding:6px 12px; border-bottom:1px solid #222; font-size:13px;
               line-height:1.4; }
  #perf-chart { display:block; background:#0a0a0a; border:1px solid #444;
                margin:8px 12px; width:calc(100% - 24px); height:200px; }
  #perf-legend { padding:0 12px 4px; font-size:11px; color:#bbb;
                 display:flex; gap:14px; }
  #perf-legend .sw { display:inline-block; width:10px; height:10px;
                     margin-right:4px; vertical-align:middle; border-radius:2px; }
  .t { color:#888; margin-right:8px; }
  .ty-transcript { border-left:3px solid #4af; }
  .ty-reply { border-left:3px solid #4f8; }
  .ty-wake { border-left:3px solid #fa4; }
  .ty-vision { border-left:3px solid #f48; }
  .ty-tts { border-left:3px solid #af4; }
  .ty-raw { color:#666; }
  #status { padding:4px 12px; font-size:11px; color:#888;
            border-top:1px solid #333; }
  /* dashboard-006: 右栏 control panel (<details> 折叠), 已脱离 position:fixed */
  #control-panel { border-left:1px solid #333; background:#181818;
                   overflow-y:auto; padding:8px; box-sizing:border-box; }
  #control-panel details { margin-bottom:8px;
                            background:rgba(0,0,0,0.4);
                            border:1px solid #333; border-radius:6px;
                            padding:6px 10px; }
  #control-panel details > summary { cursor:pointer; font-size:12px;
                                       color:#ddd; padding:2px 0;
                                       user-select:none; outline:none; }
  #control-panel details[open] > summary { margin-bottom:6px;
                                              border-bottom:1px solid #333;
                                              padding-bottom:4px; }
  #control-panel .panel-body { font-size:12px; color:#eee; }
  #control-panel button { margin:2px; padding:4px 8px; font-size:12px;
                          background:#333; color:#eee; border:1px solid #555;
                          border-radius:4px; cursor:pointer; }
  #control-panel button:hover { background:#444; }
  #control-panel button:active { background:#2a4; }
  #action-status { color:#fa0; font-size:11px; margin-top:4px;
                   min-height:14px; word-break:break-all; }
  #pose-status { color:#fa0; font-size:11px; margin-top:4px; min-height:14px; }
  #llm-status { margin-top:4px; font-size:11px; color:#fa0; }
  #watchdog-status-body { font-size:11px; color:#bbb; }
  .act { color:#fa0; margin-left:6px; }
  /* dashboard-006: small-screen 1-column stack */
  @media (max-width: 1024px) {
    #wrap { grid-template-columns: 1fr; height:auto; }
    #cam-pane { min-height:50vh; }
    #side { border-left:none; border-top:1px solid #333; min-height:60vh; }
    #control-panel { border-left:none; border-top:1px solid #333;
                     order:3; max-height:none; }
  }
</style>
</head>
<body>
<div id="watchdog-bar" style="display:none; position:fixed; top:0; left:0; right:0; padding:6px 12px; background:#c00; color:#fff; font-size:13px; z-index:100; text-align:center;">
  <span id="watchdog-msg">&#9888; watchdog 检测到服务异常</span>
</div>
<header><h1>可可 Live HUD &mdash; reachy 看到 / 听到</h1></header>
<div id="wrap">
  <div id="cam-pane">
    <img id="cam" src="/stream/camera.mjpg" alt="camera stream" />
  </div>
  <div id="side">
    <h2>性能 (dt &amp; tts_first_chunk_ms)</h2>
    <canvas id="perf-chart" width="800" height="200"></canvas>
    <div id="perf-legend">
      <span><span class="sw" style="background:#4af"></span>dt 蓝 (左 Y 0-10s)</span>
      <span><span class="sw" style="background:#fa4"></span>first_chunk_ms 橙 (右 Y 0-3000ms)</span>
    </div>
    <h2>事件 timeline</h2>
    <ul id="events"></ul>
    <div id="status">connecting...</div>
  </div>
  <div id="control-panel">
    <details id="action-panel" open>
      <summary>手动动作</summary>
      <div class="panel-body">
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
        <!-- interact-042: antenna + body_yaw 按钮 -->
        <button onclick="doAction('wiggle_antennas')">摇摆天线</button>
        <button onclick="doAction('perk_up')">天线竖起</button>
        <button onclick="doAction('droop_antennas')">天线下垂</button>
        <button onclick="doAction('turn_body_left')">转身向左</button>
        <button onclick="doAction('turn_body_right')">转身向右</button>
        <button onclick="doAction('turn_body_center')">身体回正</button>
        <div id="action-status"></div>
      </div>
    </details>
    <details id="pose-panel">
      <summary>头部姿态 (rad)</summary>
      <div class="panel-body">
        <div>Pitch <span id="pitch-val">0.00</span></div>
        <input type="range" id="pitch" min="-0.5" max="0.5" step="0.01" value="0" oninput="onPose()" style="width:100%;">
        <div>Yaw <span id="yaw-val">0.00</span></div>
        <input type="range" id="yaw" min="-0.5" max="0.5" step="0.01" value="0" oninput="onPose()" style="width:100%;">
        <div>Roll <span id="roll-val">0.00</span></div>
        <input type="range" id="roll" min="-0.5" max="0.5" step="0.01" value="0" oninput="onPose()" style="width:100%;">
        <!-- interact-042: antenna L/R + body_yaw 滑条 -->
        <div>Antenna Left <span id="antenna_left-val">0.00</span></div>
        <input type="range" id="antenna_left" min="-1.5" max="1.5" step="0.05" value="0" oninput="onPose()" style="width:100%;">
        <div>Antenna Right <span id="antenna_right-val">0.00</span></div>
        <input type="range" id="antenna_right" min="-1.5" max="1.5" step="0.05" value="0" oninput="onPose()" style="width:100%;">
        <div>Body Yaw <span id="body_yaw-val">0.00</span></div>
        <input type="range" id="body_yaw" min="-1.57" max="1.57" step="0.05" value="0" oninput="onPose()" style="width:100%;">
        <button onclick="resetPose()" style="margin-top:6px;">回中</button>
        <div id="pose-status"></div>
      </div>
    </details>
    <details id="llm-panel">
      <summary>LLM Model</summary>
      <div class="panel-body">
        <select id="llm-model" onchange="changeModel()" style="width:100%;">
          <option value="gpt-4o-mini">gpt-4o-mini</option>
          <option value="gpt-4o">gpt-4o</option>
          <option value="gpt-4.1">gpt-4.1</option>
          <option value="claude-sonnet-4.5">claude-sonnet-4.5</option>
          <option value="claude-opus-4.7">claude-opus-4.7</option>
          <option value="gemini-2.5-pro">gemini-2.5-pro</option>
        </select>
        <div id="llm-status"></div>
      </div>
    </details>
    <details id="perception-panel">
      <summary>感知设置</summary>
      <div class="panel-body">
        <label style="display:block;margin:2px 0">
          <input type="checkbox" id="face-id-toggle" onchange="changePerception('face_id', this.checked)" checked>
          人脸识别 (face_id)
        </label>
        <label style="display:block;margin:2px 0">
          <input type="checkbox" id="wake-toggle" onchange="changePerception('wake', this.checked)" checked>
          语音唤醒 (wake-word)
        </label>
        <div id="perception-status" style="font-size:11px;margin-top:4px;color:#fa0"></div>
      </div>
    </details>
    <details id="watchdog-status">
      <summary>watchdog 状态</summary>
      <div class="panel-body">
        <div id="watchdog-status-body">无最近告警 (轮询 /api/watchdog/recent 每 10s)</div>
      </div>
    </details>
  </div>
</div>
<script>
(function(){
  var ul = document.getElementById('events');
  var st = document.getElementById('status');
  var canvas = document.getElementById('perf-chart');
  var ctx = canvas ? canvas.getContext('2d') : null;
  var dtPoints = [];          // {ts, v} v=seconds (0..10)
  var firstChunkPoints = [];  // {ts, v} v=ms (0..3000)
  var MAX_POINTS = 50;
  var DT_MAX = 10.0;          // 左 Y 轴上限 (s)
  var FC_MAX = 3000.0;        // 右 Y 轴上限 (ms)

  function pushPoint(arr, ts, v){
    arr.push({ts: ts, v: v});
    if (arr.length > MAX_POINTS) arr.shift();
  }

  function drawChart(){
    if (!ctx) return;
    var W = canvas.width, H = canvas.height;
    var padL = 36, padR = 40, padT = 10, padB = 18;
    var plotW = W - padL - padR;
    var plotH = H - padT - padB;
    ctx.fillStyle = '#0a0a0a';
    ctx.fillRect(0, 0, W, H);
    ctx.strokeStyle = '#555';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padL, padT);
    ctx.lineTo(padL, padT + plotH);
    ctx.lineTo(padL + plotW, padT + plotH);
    ctx.stroke();
    ctx.strokeStyle = '#222';
    ctx.setLineDash([3, 3]);
    var dtGridY = padT + plotH - (3.0 / DT_MAX) * plotH;
    ctx.beginPath();
    ctx.moveTo(padL, dtGridY); ctx.lineTo(padL + plotW, dtGridY);
    ctx.stroke();
    var fcGridY = padT + plotH - (1500.0 / FC_MAX) * plotH;
    ctx.beginPath();
    ctx.moveTo(padL, fcGridY); ctx.lineTo(padL + plotW, fcGridY);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = '#4af';
    ctx.font = '10px sans-serif';
    ctx.textAlign = 'right';
    ctx.fillText('10s', padL - 4, padT + 8);
    ctx.fillText('3s',  padL - 4, dtGridY + 3);
    ctx.fillText('0',   padL - 4, padT + plotH);
    ctx.fillStyle = '#fa4';
    ctx.textAlign = 'left';
    ctx.fillText('3000ms', padL + plotW + 4, padT + 8);
    ctx.fillText('1500',   padL + plotW + 4, fcGridY + 3);
    ctx.fillText('0',      padL + plotW + 4, padT + plotH);
    function drawSeries(arr, color, maxV){
      if (arr.length < 1) return;
      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      ctx.lineWidth = 2;
      ctx.beginPath();
      for (var i = 0; i < arr.length; i++){
        var x = padL + (arr.length === 1 ? plotW : (i / (MAX_POINTS - 1)) * plotW);
        var clamped = Math.max(0, Math.min(maxV, arr[i].v));
        var y = padT + plotH - (clamped / maxV) * plotH;
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.stroke();
      for (var j = 0; j < arr.length; j++){
        var xj = padL + (arr.length === 1 ? plotW : (j / (MAX_POINTS - 1)) * plotW);
        var cj = Math.max(0, Math.min(maxV, arr[j].v));
        var yj = padT + plotH - (cj / maxV) * plotH;
        ctx.beginPath();
        ctx.arc(xj, yj, 2, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    drawSeries(dtPoints, '#4af', DT_MAX);
    drawSeries(firstChunkPoints, '#fa4', FC_MAX);
  }
  drawChart();

  var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  var ws = new WebSocket(proto + '//' + location.host + '/ws/events');
  ws.onopen = function(){ st.textContent = 'connected'; };
  ws.onclose = function(){ st.textContent = 'disconnected'; };
  ws.onerror = function(){ st.textContent = 'error'; };
  ws.onmessage = function(ev){
    try {
      var e = JSON.parse(ev.data);
      var chartChanged = false;
      if (typeof e.dt === 'number' && isFinite(e.dt)){
        pushPoint(dtPoints, e.ts || Date.now()/1000, e.dt);
        chartChanged = true;
      }
      if (typeof e.first_chunk_ms === 'number' && isFinite(e.first_chunk_ms)){
        pushPoint(firstChunkPoints, e.ts || Date.now()/1000, e.first_chunk_ms);
        chartChanged = true;
      }
      if (chartChanged) drawChart();
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

// dashboard-005: LLM model 切换
async function changeModel(){
  var m = document.getElementById('llm-model').value;
  document.getElementById('llm-status').textContent = '切换中...';
  try {
    var r = await fetch('/api/config/llm_model', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({model: m})
    });
    var d = await r.json();
    document.getElementById('llm-status').textContent = (d.status || ('http_'+r.status)) + ' (~30s 生效)';
  } catch(e) {
    document.getElementById('llm-status').textContent = '失败: ' + e.message;
  }
}
async function loadModel(){
  try {
    var r = await fetch('/api/config/llm_model');
    var d = await r.json();
    if (d.model) document.getElementById('llm-model').value = d.model;
  } catch(e) { /* ignore */ }
}
loadModel();

// dashboard-007: face_id + wake-word toggle (hot-reload via runtime_config.json)
async function changePerception(key, on){
  var map = {face_id: 'face_id_enabled', wake: 'wake_enabled'};
  var k = map[key];
  var st = document.getElementById('perception-status');
  st.textContent = key + ' 切换中...';
  try {
    var r = await fetch('/api/config/' + k, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({enabled: on})
    });
    var d = await r.json();
    st.textContent = (d.status || ('http_'+r.status)) + ' ' + k + '=' + on + ' (~30s 生效)';
  } catch(e) {
    st.textContent = key + ' 失败: ' + e.message;
  }
}
async function loadPerception(){
  var ids = {face_id_enabled: 'face-id-toggle', wake_enabled: 'wake-toggle'};
  for (var k in ids) {
    try {
      var r = await fetch('/api/config/' + k);
      var d = await r.json();
      var el = document.getElementById(ids[k]);
      if (el) el.checked = d.enabled !== false;
    } catch(e) { /* ignore */ }
  }
}
loadPerception();

// dashboard-006: perf-chart canvas dynamic resize (fixes "compressed at 800px" bug)
function resizeCanvas(){
  var c = document.getElementById('perf-chart');
  if (!c) return;
  var w = c.clientWidth || 800;
  if (w < 100) w = 800;
  c.width = w;
  if (typeof drawChart === 'function') drawChart();
}
window.addEventListener('resize', resizeCanvas);
resizeCanvas();
</script>
<script>
let poseTimer = null;
function onPose() {
  ['pitch','yaw','roll'].forEach(function(k){
    document.getElementById(k+'-val').textContent = parseFloat(document.getElementById(k).value).toFixed(2);
  });
  // interact-042: antenna L/R + body_yaw 滑条值更新
  ['antenna_left','antenna_right','body_yaw'].forEach(function(k){
    var el = document.getElementById(k);
    if (el) document.getElementById(k+'-val').textContent = parseFloat(el.value).toFixed(2);
  });
  if (poseTimer) clearTimeout(poseTimer);
  poseTimer = setTimeout(sendPose, 200);
}
async function sendPose() {
  var st = document.getElementById('pose-status');
  var body = {
    pitch: parseFloat(document.getElementById('pitch').value),
    yaw: parseFloat(document.getElementById('yaw').value),
    roll: parseFloat(document.getElementById('roll').value)
  };
  // interact-042: 把 antenna L/R + body_yaw 一并 POST (向后兼容)
  var aL = document.getElementById('antenna_left');
  var aR = document.getElementById('antenna_right');
  var bY = document.getElementById('body_yaw');
  if (aL && aR) body.antennas = [parseFloat(aL.value), parseFloat(aR.value)];
  if (bY) body.body_yaw = parseFloat(bY.value);
  st.textContent = 'pose ...';
  try {
    var r = await fetch('/api/pose', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
    var d = await r.json();
    st.textContent = 'pose -> ' + (d.status || ('http_'+r.status));
  } catch(e) {
    st.textContent = 'pose err: ' + e;
    console.error(e);
  }
}
function resetPose() {
  ['pitch','yaw','roll'].forEach(function(k){
    document.getElementById(k).value = '0';
    document.getElementById(k+'-val').textContent = '0.00';
  });
  // interact-042: antenna + body_yaw 回中
  ['antenna_left','antenna_right','body_yaw'].forEach(function(k){
    var el = document.getElementById(k);
    if (el) {
      el.value = '0';
      document.getElementById(k+'-val').textContent = '0.00';
    }
  });
  sendPose();
}
async function pollWatchdog() {
  try {
    const r = await fetch('/api/watchdog/recent?limit=10');
    const d = await r.json();
    const events = d.events || [];
    const bad = events.filter(function(e){
      const k = e.kind || e.event;
      return k === 'health.degraded' || k === 'restart.failed' || k === 'restart.give_up';
    });
    const body = document.getElementById('watchdog-status-body');
    if (bad.length > 0) {
      const last = bad[bad.length - 1];
      const k = last.kind || last.event || '';
      const svc = last.service || '?';
      const ts = last.ts || '';
      document.getElementById('watchdog-msg').textContent = '⚠ ' + k + ' service=' + svc + ' at ' + ts;
      document.getElementById('watchdog-bar').style.display = 'block';
      if (body) body.textContent = '⚠ ' + bad.length + ' alert(s); latest: ' + k + ' svc=' + svc + ' at ' + ts;
    } else {
      document.getElementById('watchdog-bar').style.display = 'none';
      if (body) body.textContent = '✓ 无告警 (last poll ' + new Date().toLocaleTimeString() + ')';
    }
  } catch(e) {
    const body = document.getElementById('watchdog-status-body');
    if (body) body.textContent = 'poll err: ' + (e && e.message ? e.message : e);
  }
}
setInterval(pollWatchdog, 10000);
pollWatchdog();
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

    # ------------------------------------------------------------------
    # dashboard-005: LLM model hot-switch endpoints
    # ------------------------------------------------------------------
    @app.get("/api/config/llm_model")
    async def get_llm_model() -> dict:
        """读 ~/.cache/coco/runtime_config.json 的 llm_model 字段；不存在返回空。"""
        try:
            if _RUNTIME_CONFIG_PATH.exists():
                data = json.loads(_RUNTIME_CONFIG_PATH.read_text() or "{}")
                if isinstance(data, dict):
                    m = data.get("llm_model", "")
                    return {"model": m if isinstance(m, str) else ""}
        except (OSError, ValueError):
            pass
        return {"model": ""}

    @app.post("/api/config/llm_model")
    async def post_llm_model(req: ModelRequest) -> dict:
        """写 llm_model 到 runtime_config.json（atomic rename）.

        - 白名单校验，非法 → 400
        - 合并写：保留 runtime_config 里其它字段
        - 写 .tmp 后 os.replace 到目标，避免半截写被 coco 主进程读到
        - coco/llm.py _maybe_reload_model 周期性 check 此文件（默认 30s）
        """
        if req.model not in _ALLOWED_MODELS:
            raise HTTPException(
                status_code=400,
                detail=f"unknown model: {req.model!r}",
            )
        _RUNTIME_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        data: dict = {}
        if _RUNTIME_CONFIG_PATH.exists():
            try:
                parsed = json.loads(_RUNTIME_CONFIG_PATH.read_text() or "{}")
                if isinstance(parsed, dict):
                    data = parsed
            except (OSError, ValueError):
                data = {}
        data["llm_model"] = req.model
        tmp = _RUNTIME_CONFIG_PATH.with_suffix(
            _RUNTIME_CONFIG_PATH.suffix + ".tmp"
        )
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        os.replace(tmp, _RUNTIME_CONFIG_PATH)
        return {
            "status": "saved",
            "model": req.model,
            "path": str(_RUNTIME_CONFIG_PATH),
        }

    # ------------------------------------------------------------------
    # dashboard-007: face_id / wake-word toggle endpoints
    # ------------------------------------------------------------------
    # generic GET/POST /api/config/{key}，key 必须在 _ALLOWED_PERCEPTION_KEYS 白名单
    # 主程序侧 (face_id.identify / wake_word.feed 入口) 每 30s 节流读此文件，
    # 按 face_id_enabled / wake_enabled 切自身软开关；不重启 thread / 不动 audio。
    @app.get("/api/config/{key}")
    async def get_perception_config(key: str) -> dict:
        """读 runtime_config.json[key]；缺省 → enabled=true（默认开）。"""
        if key not in _ALLOWED_PERCEPTION_KEYS:
            raise HTTPException(
                status_code=404,
                detail=f"unknown config key: {key!r}",
            )
        try:
            if _RUNTIME_CONFIG_PATH.exists():
                data = json.loads(_RUNTIME_CONFIG_PATH.read_text() or "{}")
                if isinstance(data, dict) and key in data:
                    return {"key": key, "enabled": bool(data.get(key, True))}
        except (OSError, ValueError):
            pass
        return {"key": key, "enabled": True}

    @app.post("/api/config/{key}")
    async def post_perception_config(
        key: str, req: TogglePerceptionRequest
    ) -> dict:
        """写 runtime_config.json[key] = req.enabled（atomic rename, 合并写）.

        - 白名单校验，非法 key → 404
        - 合并写：保留 llm_model 等其它字段
        - .tmp + os.replace 原子替换，主进程不会读到半截
        - 主进程 face_id / wake 模块每 30s check 一次此文件
        """
        if key not in _ALLOWED_PERCEPTION_KEYS:
            raise HTTPException(
                status_code=404,
                detail=f"unknown config key: {key!r}",
            )
        _RUNTIME_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        data: dict = {}
        if _RUNTIME_CONFIG_PATH.exists():
            try:
                parsed = json.loads(_RUNTIME_CONFIG_PATH.read_text() or "{}")
                if isinstance(parsed, dict):
                    data = parsed
            except (OSError, ValueError):
                data = {}
        data[key] = bool(req.enabled)
        tmp = _RUNTIME_CONFIG_PATH.with_suffix(
            _RUNTIME_CONFIG_PATH.suffix + ".tmp"
        )
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        os.replace(tmp, _RUNTIME_CONFIG_PATH)
        return {
            "status": "saved",
            "key": key,
            "enabled": bool(req.enabled),
            "path": str(_RUNTIME_CONFIG_PATH),
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


# === dashboard-004: pose sliders (pitch/yaw/roll) ===
# 直接发 4x4 head matrix, 不耦合 coco.actions 预设
_POSE_MAX_RAD = float(os.environ.get("COCO_DASHBOARD_POSE_MAX_RAD", "0.6"))
_POSE_TIMEOUT_S = float(os.environ.get("COCO_DASHBOARD_POSE_TIMEOUT_S", "8"))
# interact-042: antenna + body_yaw clamp (保守, 远低于 SDK 极限)
_ANTENNA_MAX_RAD = 1.5  # SDK 极限 ±3.05, 本层 ±1.5
_BODY_YAW_MAX_RAD = 1.5707963267948966  # π/2


class PoseRequest(BaseModel):
    pitch: float = 0.0
    yaw: float = 0.0
    roll: float = 0.0
    # interact-042: 向后兼容扩展 — antennas=[L, R] 与 body_yaw 都可选
    # 不提供时不更新对应 joint, 保留旧 pitch/yaw/roll 行为
    antennas: Optional[List[float]] = None
    body_yaw: Optional[float] = None


def _pose_clamp(v: float) -> float:
    return max(-_POSE_MAX_RAD, min(_POSE_MAX_RAD, float(v)))


def _antenna_clamp(v: float) -> float:
    return max(-_ANTENNA_MAX_RAD, min(_ANTENNA_MAX_RAD, float(v)))


def _body_yaw_clamp(v: float) -> float:
    return max(-_BODY_YAW_MAX_RAD, min(_BODY_YAW_MAX_RAD, float(v)))


_POSE_SUBPROCESS_TEMPLATE = """import os, time
import numpy as np
from reachy_mini import ReachyMini
pitch = {pitch}
yaw = {yaw}
roll = {roll}
antennas = {antennas}
body_yaw = {body_yaw}
r = ReachyMini(spawn_daemon=False, media_backend='no_media')
try:
    try:
        r.enable_motors()
    except Exception:
        pass
    time.sleep(0.2)
    cp, sp = np.cos(pitch), np.sin(pitch)
    cy, sy = np.cos(yaw), np.sin(yaw)
    cr, sr = np.cos(roll), np.sin(roll)
    Rx = np.array([[1,0,0],[0,cp,-sp],[0,sp,cp]])
    Ry = np.array([[cy,0,sy],[0,1,0],[-sy,0,cy]])
    Rz = np.array([[cr,-sr,0],[sr,cr,0],[0,0,1]])
    R = Rz @ Ry @ Rx
    M = np.eye(4)
    M[:3,:3] = R
    r.set_target(head=M)
    # interact-042: antenna + body_yaw 一并下发 (None 时跳过, 向后兼容)
    if antennas is not None:
        try:
            r.set_target(antennas=antennas)
        except Exception:
            pass
    if body_yaw is not None:
        try:
            r.set_target(body_yaw=body_yaw)
        except Exception:
            pass
    time.sleep(0.4)
finally:
    os._exit(0)
"""


@app.post("/api/pose")
async def post_pose(req: PoseRequest) -> dict:
    """dashboard-004: 滑条直接控制头部 pitch/yaw/roll, clamp +/- _POSE_MAX_RAD.

    复用 dashboard-002 模式: subprocess 短期 ReachyMini client + os._exit(0)
    避免 zenoh 多 client 断言风暴。

    测试钩子 COCO_DASHBOARD_FAKE_POSE=1 时跳过 subprocess, 返 clamp 后的值。

    interact-042: 扩展 antennas=[L, R] (clamp ±1.5) 与 body_yaw (clamp ±π/2),
    向后兼容 — 不传时与 dashboard-004 行为一致。
    """
    p_ = _pose_clamp(req.pitch)
    y_ = _pose_clamp(req.yaw)
    r_ = _pose_clamp(req.roll)
    # interact-042: antenna + body_yaw clamp
    antennas_ = None
    if req.antennas is not None and len(req.antennas) >= 2:
        antennas_ = [_antenna_clamp(req.antennas[0]), _antenna_clamp(req.antennas[1])]
    body_yaw_ = None
    if req.body_yaw is not None:
        body_yaw_ = _body_yaw_clamp(req.body_yaw)

    if os.environ.get("COCO_DASHBOARD_FAKE_POSE") == "1":
        out = {"status": "ok", "rc": 0, "fake": True,
               "pitch": p_, "yaw": y_, "roll": r_}
        if antennas_ is not None:
            out["antennas"] = antennas_
        if body_yaw_ is not None:
            out["body_yaw"] = body_yaw_
        return out

    py = sys.executable
    script = _POSE_SUBPROCESS_TEMPLATE.format(
        pitch=repr(p_),
        yaw=repr(y_),
        roll=repr(r_),
        antennas=repr(antennas_),
        body_yaw=repr(body_yaw_),
    )
    cmd = [py, "-c", script]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=_POSE_TIMEOUT_S
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
        return {"status": "timeout", "pitch": p_, "yaw": y_, "roll": r_}
    rc = proc.returncode
    out = {
        "status": "ok" if rc == 0 else "error",
        "rc": rc,
        "pitch": p_,
        "yaw": y_,
        "roll": r_,
        "stdout": stdout.decode("utf-8", errors="replace")[-400:],
        "stderr": stderr.decode("utf-8", errors="replace")[-400:],
    }
    if antennas_ is not None:
        out["antennas"] = antennas_
    if body_yaw_ is not None:
        out["body_yaw"] = body_yaw_
    return out


# infra-watchdog-fu redbar: 读 /tmp/coco-watchdog-events.log (JSON lines) 返回最近事件
import pathlib as _pl_redbar
_WATCHDOG_LOG_PATH = _pl_redbar.Path("/tmp/coco-watchdog-events.log")


@app.get("/api/watchdog/recent")
async def watchdog_recent(limit: int = 10):
    limit = max(1, min(100, limit))
    events = []
    try:
        if _WATCHDOG_LOG_PATH.exists():
            with _WATCHDOG_LOG_PATH.open() as f:
                lines = f.readlines()
            for line in lines[-limit:]:
                try:
                    events.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        pass
    return {"events": events, "count": len(events)}
