#!/usr/bin/env python3
"""AutoMD Desktop — native window wrapping the chat web UI via pywebview.

Dependencies: pip install pywebview fastapi "uvicorn[standard]"

For WebSocket support in the browser UI, uvicorn needs a WebSocket backend.
If you prefer minimal installs, at least install one of:
    pip install websockets
    pip install wsproto

Usage:
    python app.py              # single window → port 8765
    python app.py 1            # window 1   → port 8766
    python app.py 2            # window 2   → port 8767
"""

import sys
import threading
import os
import platform
import webbrowser
import uvicorn

from server import app as fastapi_app

BASE_PORT = 8765
offset = int(sys.argv[1]) if len(sys.argv) > 1 else 0
PORT = BASE_PORT + offset


def _run_server():
    uvicorn.run(fastapi_app, host="127.0.0.1", port=PORT, log_level="warning")


def _is_wsl() -> bool:
    release = platform.release().lower()
    return "microsoft" in release or "wsl" in release


def _has_gui_display() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


if __name__ == "__main__":
    threading.Thread(target=_run_server, daemon=True).start()
    url = f"http://127.0.0.1:{PORT}"

    # WSL/headless environments often crash in pywebview's Qt/GTK backend
    # before Python can catch an exception. Avoid importing/starting it there.
    if _is_wsl() or not _has_gui_display():
        print("[AutoMD] 检测到 WSL 或无图形显示环境，使用默认浏览器打开。")
        print(f"[AutoMD] 浏览器地址: {url}")
        opened = webbrowser.open(url)
        if not opened:
            print("[AutoMD] 自动打开失败，请手动复制上面的地址到浏览器。")
        input("按 Enter 退出运行... ")
    else:
        import webview

        try:
            webview.create_window(
                f"AutoMD — :{PORT}",
                url,
                width=1100,
                height=750,
                min_size=(800, 500),
            )
            webview.start()
        except Exception as exc:
            print(f"[AutoMD] pywebview 启动失败，改为用默认浏览器打开: {exc}")
            print(f"[AutoMD] 浏览器地址: {url}")
            opened = webbrowser.open(url)
            if not opened:
                print("[AutoMD] 自动打开失败，请手动复制上面的地址到浏览器。")
            input("按 Enter 退出运行... ")
