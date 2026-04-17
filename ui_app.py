import asyncio
import queue
import threading
import traceback
from pathlib import Path
from typing import Optional

import gradio as gr

from main import PROGRESS_REPORT_PATH, run_pipeline

DEFAULT_TASK = (
    "对 1IEP 和配体：Cc1ccc(cc1Nc2nccc(n2)c3cccnc3)NC(=O)c4ccc(cc4)CN5CCN(CC5)C 进行MD的文件预处理和对接，生成后续可执行的MD所需文件。"
    "所有输出保存到 ./output，并在output下创建protein_preparation、ligand_preparation、docking_result等子目录。"
    "所有中间文件保存在项目根目录的 temp 文件夹，有用的输出文件必须保存在 output 文件夹。"
    "生成可复现的执行报告（含命令和结果摘要），每个专家都要给出系统性工作总结。"
)

DEFAULT_USER_INSTRUCTION = "无额外偏好，请优先保证可复现与稳健性。"

_STOP_EVENT = threading.Event()
_RUNNING_EVENT = threading.Event()
_USER_REPLY_QUEUE: queue.Queue[str] = queue.Queue()
_LOG_QUEUE: queue.Queue[tuple[str, str]] = queue.Queue()
_CHAT_LOCK = threading.Lock()
_CHAT_LINES: list[str] = []
_FINAL_REPORT = "等待运行。"
_STATUS_TEXT = "就绪"
_PROGRESS_REPORT = ""


def _read_progress_report() -> str:
    try:
        path = Path(PROGRESS_REPORT_PATH)
        if path.exists():
            content = path.read_text(encoding="utf-8").strip()
            if content:
                return content
    except OSError:
        pass

    return "[star]\n## 工作进度\n- 暂无可用进度文档。\n[TERMINATE]"


def _drain_queue(q: queue.Queue) -> None:
    while True:
        try:
            q.get_nowait()
        except queue.Empty:
            break


def _append_chat_line(line: str) -> None:
    text = (line or "").strip()
    if not text:
        return
    with _CHAT_LOCK:
        _CHAT_LINES.append(text)


def _snapshot_chat() -> str:
    with _CHAT_LOCK:
        return "\n".join(_CHAT_LINES).strip()


def _reset_session() -> None:
    global _FINAL_REPORT, _STATUS_TEXT, _PROGRESS_REPORT
    with _CHAT_LOCK:
        _CHAT_LINES.clear()
    _FINAL_REPORT = "等待运行。"
    _STATUS_TEXT = "就绪"
    _PROGRESS_REPORT = _read_progress_report()


def _run_pipeline_worker(task: str, user_note: str) -> None:
    def on_log(line: str) -> None:
        _LOG_QUEUE.put(("log", line))

    def on_user_input(prompt: str) -> str:
        _LOG_QUEUE.put(("log", "[user] 系统请求输入："))
        _LOG_QUEUE.put(("log", prompt or "请给出下一步偏好/约束。"))
        _LOG_QUEUE.put(("log", "[user] 请在下方输入并提交。"))

        while True:
            if _STOP_EVENT.is_set():
                return "用户请求停止，请 coordinator 立即结束流程。"
            try:
                reply = _USER_REPLY_QUEUE.get(timeout=0.2)
                text = (reply or "").strip()
                if text:
                    _LOG_QUEUE.put(("log", f"[user] 已提交输入: {text}"))
                    return text
            except queue.Empty:
                continue

    def should_stop() -> bool:
        return _STOP_EVENT.is_set()

    try:
        _RUNNING_EVENT.set()
        report = asyncio.run(
            run_pipeline(
                task,
                log_callback=on_log,
                user_input_callback=on_user_input,
                should_stop_callback=should_stop,
                user_instruction=user_note,
            )
        )
        _LOG_QUEUE.put(("report", report))
    except Exception:
        _LOG_QUEUE.put(("log", "[system] 流程执行异常："))
        _LOG_QUEUE.put(("log", traceback.format_exc()))
        _LOG_QUEUE.put(("report", "执行失败，请查看日志区中的异常栈。"))
    finally:
        _RUNNING_EVENT.clear()
        _LOG_QUEUE.put(("done", ""))


def _poll_updates() -> tuple[str, str, str, str]:
    global _FINAL_REPORT, _STATUS_TEXT, _PROGRESS_REPORT

    updated = False
    while True:
        try:
            kind, payload = _LOG_QUEUE.get_nowait()
        except queue.Empty:
            break

        updated = True
        if kind == "log":
            _append_chat_line(payload)
            _PROGRESS_REPORT = _read_progress_report()
        elif kind == "report":
            _FINAL_REPORT = payload or "等待运行。"
            _append_chat_line("[system] 流程完成，已生成最终报告。")
            _PROGRESS_REPORT = _read_progress_report()
            _STATUS_TEXT = "已完成"
        elif kind == "done":
            if _STOP_EVENT.is_set():
                _STATUS_TEXT = "已停止"
            elif _STATUS_TEXT != "已完成":
                _STATUS_TEXT = "已完成"

    if not updated:
        _PROGRESS_REPORT = _read_progress_report()

    return _snapshot_chat(), _FINAL_REPORT, _PROGRESS_REPORT, f"状态: {_STATUS_TEXT}"


def _start_run(task_text: str, user_instruction: str):
    global _STATUS_TEXT

    if _RUNNING_EVENT.is_set():
        return (
            gr.update(value=_snapshot_chat()),
            gr.update(value=_FINAL_REPORT),
            gr.update(value=_PROGRESS_REPORT or _read_progress_report()),
            gr.update(value=f"状态: {_STATUS_TEXT}"),
            gr.update(),
        )

    _STOP_EVENT.clear()
    _drain_queue(_USER_REPLY_QUEUE)
    _drain_queue(_LOG_QUEUE)
    _reset_session()

    task_text = (task_text or "").strip() or DEFAULT_TASK
    user_instruction = (user_instruction or "").strip() or DEFAULT_USER_INSTRUCTION

    _append_chat_line(f"[user] 任务: {task_text}")
    _append_chat_line(f"[user] 默认偏好: {user_instruction}")

    worker = threading.Thread(target=_run_pipeline_worker, args=(task_text, user_instruction), daemon=True)
    worker.start()
    _STATUS_TEXT = "任务运行中"

    return (
        gr.update(value=_snapshot_chat()),
        gr.update(value=_FINAL_REPORT),
        gr.update(value=_PROGRESS_REPORT),
        gr.update(value=f"状态: {_STATUS_TEXT}"),
        gr.update(value=""),
    )


def _send_user_reply(user_reply: str):
    user_reply = (user_reply or "").strip()
    if not user_reply:
        return gr.update(), gr.update()

    if not _RUNNING_EVENT.is_set():
        _append_chat_line("[system] 当前没有运行中的任务。请先点击开始任务。")
        return gr.update(value=_snapshot_chat()), gr.update(value="")

    _USER_REPLY_QUEUE.put(user_reply)
    _append_chat_line(f"[user] 已提交输入: {user_reply}")
    return gr.update(value=_snapshot_chat()), gr.update(value="")


def _stop_run():
    global _STATUS_TEXT
    _STOP_EVENT.set()
    _STATUS_TEXT = "正在停止"
    _append_chat_line("[system] 已发送停止请求，流程将在安全检查点结束。")
    return gr.update(value=_snapshot_chat()), gr.update(value=f"状态: {_STATUS_TEXT}")


def _clear_all():
    _STOP_EVENT.clear()
    _drain_queue(_USER_REPLY_QUEUE)
    _drain_queue(_LOG_QUEUE)
    _reset_session()
    return (
        gr.update(value=""),
        gr.update(value=_FINAL_REPORT),
        gr.update(value=_PROGRESS_REPORT),
        gr.update(value=f"状态: {_STATUS_TEXT}"),
        gr.update(value=DEFAULT_TASK),
        gr.update(value=DEFAULT_USER_INSTRUCTION),
        gr.update(value=""),
    )


def create_ui() -> gr.Blocks:
    with gr.Blocks(title="AutoMD") as demo:
        gr.Markdown("# AutoMD\n浏览器式交互界面，用于 MD 预处理与对接任务。")

        with gr.Row():
            with gr.Column(scale=2):
                task_input = gr.Textbox(label="任务输入", lines=8, value=DEFAULT_TASK)
                user_input = gr.Textbox(label="用户默认偏好", value=DEFAULT_USER_INSTRUCTION)
                user_reply = gr.Textbox(label="用户实时输入", placeholder="当流程请求输入时，在这里补充并提交。")

                with gr.Row():
                    start_button = gr.Button("开始任务", variant="primary")
                    stop_button = gr.Button("停止运行", variant="stop")
                    send_button = gr.Button("发送用户输入")
                    clear_button = gr.Button("清空")

                status_box = gr.Markdown("状态: 就绪")
                chat_log = gr.Textbox(label="对话日志", lines=18, value="", interactive=False)

            with gr.Column(scale=1):
                progress_box = gr.Markdown(_read_progress_report())
                final_report_box = gr.Markdown("等待运行。")

        start_button.click(
            fn=_start_run,
            inputs=[task_input, user_input],
            outputs=[chat_log, final_report_box, progress_box, status_box, user_reply],
        )
        send_button.click(
            fn=_send_user_reply,
            inputs=[user_reply],
            outputs=[chat_log, user_reply],
        )
        user_reply.submit(
            fn=_send_user_reply,
            inputs=[user_reply],
            outputs=[chat_log, user_reply],
        )
        stop_button.click(
            fn=_stop_run,
            outputs=[chat_log, status_box],
        )
        clear_button.click(
            fn=_clear_all,
            outputs=[chat_log, final_report_box, progress_box, status_box, task_input, user_input, user_reply],
        )

        timer = gr.Timer(0.5)
        timer.tick(
            fn=_poll_updates,
            outputs=[chat_log, final_report_box, progress_box, status_box],
        )

    return demo


def main() -> None:
    app = create_ui()
    app.queue()
    app.launch(inbrowser=True, theme=gr.themes.Soft())


if __name__ == "__main__":
    main()
