"""Simple interactive console interface leveraging the download manager."""
from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

from downloader import DownloadManager, DownloadTaskOptions


def prompt(question: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{question}{suffix}: ")
    return value.strip() or (default or "")


def interactive() -> int:
    print("=== get-m3u8 桌面下载器 ===")
    url = prompt("请输入 M3U8 链接")
    if not url:
        print("必须提供 M3U8 链接")
        return 1
    title = prompt("保存标题", "video")
    output_format = prompt("保存格式 (ts/mp4)", "ts").lower()
    start_segment = prompt("起始片段 (可选)", "")
    end_segment = prompt("结束片段 (可选)", "")
    stream_choice = prompt("边下边存? (y/n)", "y").lower() != "n"
    decrypt_choice = prompt("开启 AES 解密? (y/n)", "y").lower() != "n"
    output_dir = Path(prompt("保存目录", "files"))

    options = DownloadTaskOptions(
        url=url,
        title=title,
        output_format=output_format,
        start_segment=int(start_segment) if start_segment else None,
        end_segment=int(end_segment) if end_segment else None,
        stream_to_disk=stream_choice,
        decrypt=decrypt_choice,
    )

    manager = DownloadManager(output_dir)
    task_id = str(uuid.uuid4())
    task = manager.create_task(task_id, options)

    print("开始下载，按 Ctrl+C 强制保存当前进度。")
    spinner = "|/-\\"
    idx = 0
    try:
        while True:
            status = task.to_dict()
            state = status["status"]
            progress = status.get("progress", 0) * 100
            speed = status.get("speed_bps") or 0
            eta = status.get("eta_seconds")
            message = status.get("message") or ""
            eta_text = format_eta(eta) if eta else "—"
            speed_text = format_speed(speed)
            sys.stdout.write(
                f"\r{spinner[idx % len(spinner)]} 状态:{state:<10} 进度:{progress:6.2f}% 速度:{speed_text:<10} ETA:{eta_text:<8} {message:<40}"
            )
            sys.stdout.flush()
            if state in {"completed", "error", "forced", "stopped"}:
                break
            idx += 1
            time.sleep(1)
    except KeyboardInterrupt:
        task.request_force_save()
        print("\n收到中断，正在尝试保存已下载片段...")
    finally:
        print("\n")
    final_status = task.to_dict()
    print(f"结果: {final_status['status']} - {final_status.get('message', '')}")
    if final_status.get("output_path"):
        print(f"文件位置: {final_status['output_path']}")
    if final_status.get("ffmpeg_missing"):
        print("提示: 未检测到 ffmpeg，已保存为 TS。")
    return 0 if final_status["status"] == "completed" else 1


def format_eta(seconds: float) -> str:
    seconds = int(seconds)
    mins, secs = divmod(seconds, 60)
    hours, mins = divmod(mins, 60)
    if hours:
        return f"{hours}h{mins:02d}m"
    if mins:
        return f"{mins}m{secs:02d}s"
    return f"{secs}s"


def format_speed(speed: float) -> str:
    if speed <= 0:
        return "—"
    if speed > 1024 * 1024:
        return f"{speed / 1024 / 1024:.2f} MB/s"
    if speed > 1024:
        return f"{speed / 1024:.2f} KB/s"
    return f"{speed:.0f} B/s"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(interactive())
