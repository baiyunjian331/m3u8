"""Command line helper using the high level download manager."""
from __future__ import annotations

import argparse
import sys
import time
import uuid
from pathlib import Path

from downloader import DownloadManager, DownloadTaskOptions


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download an M3U8 stream")
    parser.add_argument("url", help="M3U8 playlist URL")
    parser.add_argument("title", nargs="?", default="video", help="Base name for the output file")
    parser.add_argument(
        "--format",
        choices=["ts", "mp4"],
        default="ts",
        help="Target container format",
    )
    parser.add_argument("--output", default="files", help="Directory used to store downloads")
    parser.add_argument("--start", type=int, help="Start segment number (1-based)")
    parser.add_argument("--end", type=int, help="End segment number (inclusive)")
    parser.add_argument("--memory", action="store_true", help="Buffer segments in memory before writing")
    parser.add_argument("--no-decrypt", action="store_true", help="Disable AES-128 decryption")
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Number of retries per segment before failing",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    options = DownloadTaskOptions(
        url=args.url,
        title=args.title,
        output_format=args.format,
        start_segment=args.start,
        end_segment=args.end,
        stream_to_disk=not args.memory,
        decrypt=not args.no_decrypt,
        max_retries=args.max_retries,
    )
    manager = DownloadManager(Path(args.output))
    task_id = str(uuid.uuid4())
    task = manager.create_task(task_id, options)

    spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    idx = 0
    try:
        while True:
            status = task.to_dict()
            state = status["status"]
            progress = status.get("progress", 0) * 100
            speed = status.get("speed_bps") or 0
            speed_text = format_speed(speed)
            message = status.get("message") or ""
            sys.stdout.write(
                f"\r{spinner[idx % len(spinner)]} {progress:6.2f}% {state:<10} {speed_text:<12} {message:<40}"
            )
            sys.stdout.flush()
            if state in {"completed", "error", "forced", "stopped"}:
                break
            idx += 1
            time.sleep(1)
    except KeyboardInterrupt:
        task.request_force_save()
        sys.stdout.write("\n用户中断，已尝试保存已下载片段。\n")
    else:
        sys.stdout.write("\n")
    final_status = task.to_dict()
    print(f"状态: {final_status['status']} - {final_status.get('message', '')}")
    if final_status.get("output_path"):
        print(f"输出文件: {final_status['output_path']}")
    if final_status.get("ffmpeg_missing"):
        print("提示: 未找到 ffmpeg，已以 TS 格式保存。")
    return 0 if final_status["status"] == "completed" else 1


def format_speed(speed: float) -> str:
    if speed <= 0:
        return "—"
    if speed > 1024 * 1024:
        return f"{speed / 1024 / 1024:.2f} MB/s"
    if speed > 1024:
        return f"{speed / 1024:.2f} KB/s"
    return f"{speed:.0f} B/s"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
