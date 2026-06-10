#!/usr/bin/env python3
"""AutoMD Web UI — FastAPI server wrapping chat.py ChatSession."""

import uuid
from pathlib import Path
import asyncio
import queue
import threading

import json as _json

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, StreamingResponse
import uvicorn

from chat import ChatSession

app = FastAPI(title="AutoMD Chat")
sessions: dict[str, ChatSession] = {}
HERE = Path(__file__).resolve().parent


@app.get("/")
async def index():
    return HTMLResponse((HERE / "static" / "index.html").read_text(encoding="utf-8"))


@app.get("/api/sessions")
async def list_sessions():
    sess_dir = HERE / "data" / "sessions"
    ids = set(sessions.keys())
    if sess_dir.exists():
        for f in sess_dir.glob("*.json"):
            ids.add(f.stem)
    return JSONResponse(sorted(ids))


@app.post("/api/sessions")
async def new_session():
    sid = uuid.uuid4().hex[:8]
    sessions[sid] = ChatSession(session_id=sid, interactive=False)
    # 🆕 同步创建产物目录, 避免 LLM 第一次调 pymol_execute / 写文件时因为目录缺失 404
    (HERE / "AutoMD_LangGraph" / "output" / sid).mkdir(parents=True, exist_ok=True)
    greeting = "你好，我是 AutoMD 助手。你可以直接输入任务，我会帮你完成对接、MD 和分析流程。"
    return JSONResponse({"session_id": sid, "greeting": greeting})


@app.delete("/api/sessions/{sid}")
async def delete_session(sid: str):
    sessions.pop(sid, None)
    f = HERE / "data" / "sessions" / f"{sid}.json"
    if f.exists():
        f.unlink()
    return JSONResponse({"ok": True})


@app.get("/api/sessions/{sid}/messages")
async def get_session_messages(sid: str):
    f = HERE / "data" / "sessions" / f"{sid}.json"
    if not f.exists():
        return JSONResponse([])
    raw = _json.loads(f.read_text(encoding="utf-8"))
    data = raw if isinstance(raw, list) else raw.get("all_msgs", [])
    role_map = {"human": "user", "ai": "assistant"}
    return JSONResponse([
        {"role": role_map.get(d.get("type",""),"assistant"),
         "content": d.get("content",""), "time": ""}
        for d in data
    ])


@app.post("/api/chat/{sid}")
async def chat(sid: str, req: Request):
    body = await req.json()
    msg = body.get("message", "").strip()
    if not msg:
        return JSONResponse({"reply": ""})
    if sid not in sessions:
        sessions[sid] = ChatSession(session_id=sid, interactive=False)
        # 🆕 兜底: 旧会话从 disk 重激活时也确保产物目录存在
        (HERE / "AutoMD_LangGraph" / "output" / sid).mkdir(parents=True, exist_ok=True)
    reply = sessions[sid].ask(msg)
    return JSONResponse({"reply": reply})


@app.get("/api/output/{path:path}")
async def serve_output(path: str):
    root = HERE / "AutoMD_LangGraph" / "output"
    target = root / path
    if not target.exists():
        return JSONResponse({"error": f"not found: {path}"}, status_code=404)
    # Serve images and other binary files directly; return text files as JSON content.
    import mimetypes
    mime, _ = mimetypes.guess_type(str(target))
    if mime and (mime.startswith("image/") or not mime.startswith("text/")):
        return FileResponse(str(target), media_type=mime)
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
        return JSONResponse({"content": content[:8000]})
    except Exception:
        return FileResponse(str(target))


@app.get("/api/files")
async def list_files(sid: str, path: str = ""):
    """List directory contents under output/{sid}/{path}.

    Returns JSON: {"path": "...", "items": [{"name", "path", "type", "size", "mtime"}, ...]}

    Security: rejects any path that escapes output/{sid}/ via .. or symlinks.
    """
    root = (HERE / "AutoMD_LangGraph" / "output" / sid).resolve()
    if not root.exists():
        return JSONResponse({"path": path, "items": [], "error": "session output dir not found"})

    target = (root / path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return JSONResponse({"error": "path traversal denied"}, status_code=403)

    if not target.exists():
        return JSONResponse({"path": path, "items": []})
    if not target.is_dir():
        return JSONResponse({"error": "not a directory", "path": path}, status_code=400)

    items = []
    for entry in sorted(target.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
        try:
            stat = entry.stat()
            items.append({
                "name": entry.name,
                "path": str(entry.relative_to(root)).replace("\\", "/"),
                "type": "dir" if entry.is_dir() else "file",
                "size": stat.st_size if entry.is_file() else None,
                "mtime": int(stat.st_mtime),
            })
        except (PermissionError, OSError):
            continue

    return JSONResponse({"path": path, "items": items})


@app.get("/api/file/text")
async def file_text(sid: str, path: str):
    """Read a text file (capped). Returns JSON: {content, size, truncated}.

    Only for text-like files. Binary files (detected by null bytes or known
    binary extensions) return 415. Files > 1 MB return 413.
    """
    root = (HERE / "AutoMD_LangGraph" / "output" / sid).resolve()
    target = (root / path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return JSONResponse({"error": "path traversal denied"}, status_code=403)
    if not target.exists() or not target.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)

    # Heuristic: refuse files > 1 MB
    size = target.stat().st_size
    if size > 1 * 1024 * 1024:
        return JSONResponse({
            "error": "file too large for preview (>1MB), download instead",
            "size": size,
            "download_url": f"/api/output/{sid}/{path}",
        }, status_code=413)

    # Known binary extensions — short-circuit
    binary_exts = {'.tar', '.gz', '.zip', '.bz2', '.xz', '.7z',
                   '.prmtop', '.inpcrd',
                   '.nc', '.crd', '.rst', '.dcd', '.bin', '.exe', '.dll', '.so', '.o', '.a',
                   '.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.pdf'}
    if target.suffix.lower() in binary_exts:
        return JSONResponse({
            "error": "binary file, download instead",
            "size": size,
            "download_url": f"/api/output/{sid}/{path}",
        }, status_code=415)

    # Sniff first 8 KB for null bytes (binary indicator)
    try:
        with open(target, 'rb') as f:
            head = f.read(8192)
        if b'\x00' in head:
            return JSONResponse({
                "error": "appears to be a binary file, download instead",
                "size": size,
                "download_url": f"/api/output/{sid}/{path}",
            }, status_code=415)
    except Exception as e:
        return JSONResponse({"error": f"read failed: {e}"}, status_code=500)

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return JSONResponse({"error": f"read failed: {e}"}, status_code=500)

    truncated = len(content) > 50000
    if truncated:
        content = content[:50000] + f"\n\n... [truncated, total {size} bytes]"
    return JSONResponse({"content": content, "size": size, "truncated": truncated})


@app.post("/api/chat/{sid}/stream")
async def chat_stream(sid: str, req: Request):
    body = await req.json()
    msg = body.get("message", "").strip()
    if not msg:
        return StreamingResponse(empty_gen(), media_type="text/event-stream")
    if sid not in sessions:
        sessions[sid] = ChatSession(session_id=sid, interactive=False)
        # 🆕 兜底: 旧会话从 disk 重激活时也确保产物目录存在
        (HERE / "AutoMD_LangGraph" / "output" / sid).mkdir(parents=True, exist_ok=True)

    async def gen():
        async for event in sessions[sid].ask_stream(msg):
            if isinstance(event, str):
                payload = {"type": "assistant_token", "token": event}
            else:
                payload = event
            yield f"data: {_json.dumps(payload, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


async def empty_gen():
    yield "data: [DONE]\n\n"


@app.websocket("/ws/run/{sid}")
async def ws_run(ws: WebSocket, sid: str):
    await ws.accept()
    body = await ws.receive_json()
    raw_task = body.get("raw_task", "")
    if not raw_task:
        await ws.send_json({"type": "error", "text": "missing raw_task"})
        await ws.close()
        return

    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), "AutoMD_LangGraph"))
    from graph import run_automd
    import nodes.print_utils as pu

    output_q: "queue.Queue[dict]" = queue.Queue()
    input_q: "queue.Queue[str]" = queue.Queue()
    stop_event = threading.Event()

    def push_log(text: str) -> None:
        output_q.put({"type": "log", "text": text})

    def worker() -> None:
        try:
            pu.set_web_mode(True)
            pu.set_web_sink(push_log)
            gen = run_automd(raw_task, thread_id=sid)
            send_back = None
            while not stop_event.is_set():
                try:
                    msg = gen.send(send_back)
                except StopIteration:
                    break
                send_back = None
                if msg is None:
                    continue
                output_q.put(msg)
                if msg.get("type") == "interrupt":
                    try:
                        send_back = input_q.get()
                    except Exception:
                        send_back = "skip"
            output_q.put({"type": "done"})
        except Exception as exc:
            output_q.put({"type": "error", "text": str(exc)})
        finally:
            pu.set_web_sink(None)
            stop_event.set()

    threading.Thread(target=worker, daemon=True).start()

    report_state: dict = {}
    try:
        while True:
            msg = await asyncio.to_thread(output_q.get)
            kind = msg.get("type")
            if kind == "done":
                break
            if kind == "error":
                await ws.send_json(msg)
                break

            if kind == "report":
                report_state = msg.get("state", {}) or {}

            await ws.send_json(msg)

            if kind == "interrupt":
                reply = await ws.receive_json()
                value = reply.get("value", "skip")
                codepoints = " ".join(f"U+{ord(ch):04X}" for ch in str(value))
                pu.debug(f"[ws_run] received interrupt reply raw={value!r} strip={str(value).strip()!r} lower={str(value).strip().lower()!r} len={len(str(value))} codepoints={codepoints}")
                await ws.send_json({"type": "interrupt_echo", "value": value, "text": f"后端收到回复: {value}"})
                input_q.put(value)
    except StopAsyncIteration:
        pass
    except WebSocketDisconnect:
        stop_event.set()
        pass
    finally:
        stop_event.set()
        if report_state and sid in sessions:
            try:
                # 1. 旧行为: 加合成消息到 LLM 历史 (给下一次用户消息用)
                sessions[sid]._on_workflow_done(report_state)
                # 2. 🆕 主动 invoke 总结 LLM, 流式发到前端
                try:
                    async for evt in sessions[sid]._stream_summary():
                        if isinstance(evt, dict):
                            t = evt.get("type", "")
                            if t == "assistant_token" and evt.get("token"):
                                await ws.send_json({"type": "summary_token", "token": evt["token"]})
                    await ws.send_json({"type": "summary_done"})
                except Exception as e:
                    pu.debug(f"[ws_run] summary LLM 失败: {e!r}")
                    # 总结失败也别让前端卡住, 发一个错误事件兜底
                    try:
                        await ws.send_json({"type": "error", "text": f"总结失败: {e}"})
                    except Exception:
                        pass
            except Exception as e:
                pu.debug(f"[ws_run] _on_workflow_done 失败: {e!r}")
        await ws.close()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8765)
