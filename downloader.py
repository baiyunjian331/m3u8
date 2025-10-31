"""Utilities for managing M3U8 download tasks."""
from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional

import ipaddress
import m3u8
import requests
from Crypto.Cipher import AES
from urllib.parse import urljoin, urlparse

import socket


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


@dataclass
class TaskStatus:
    """Represents the public status of a download task."""

    status: str
    progress: int
    message: str
    speed: Optional[str] = None
    eta: Optional[str] = None
    download_url: Optional[str] = None

    def to_dict(self) -> Dict[str, Optional[str]]:
        return {
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "speed": self.speed,
            "eta": self.eta,
            "download_url": self.download_url,
        }


def format_eta(seconds: Optional[float]) -> Optional[str]:
    """Convert seconds into a localized HH:MM:SS style string."""

    if seconds is None:
        return None

    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)

    if hours > 0:
        return f"{hours:d}小时{minutes:02d}分{secs:02d}秒"
    if minutes > 0:
        return f"{minutes:d}分{secs:02d}秒"
    return f"{secs:d}秒"


def is_safe_url(url: str) -> bool:
    """Validate that the supplied URL resolves to a public IP address."""

    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return False

        addresses = []
        try:
            ip = ipaddress.ip_address(hostname)
            addresses.append(ip)
        except ValueError:
            try:
                addr_info = socket.getaddrinfo(hostname, None)
            except socket.gaierror:
                return False

            for _family, _socktype, _proto, _canonname, sockaddr in addr_info:
                if not sockaddr:
                    continue
                address = sockaddr[0]
                try:
                    resolved_ip = ipaddress.ip_address(address)
                except ValueError:
                    return False
                addresses.append(resolved_ip)

            if not addresses:
                return False

        for resolved_ip in addresses:
            if (
                resolved_ip.is_private
                or resolved_ip.is_loopback
                or resolved_ip.is_link_local
                or resolved_ip.is_reserved
            ):
                return False

        return True
    except Exception:
        return False


class DownloadError(Exception):
    """Raised when a recoverable error happens during download."""


@dataclass
class _TaskContext:
    task_id: str
    url: str
    filename: str
    start_time: float = field(default_factory=time.time)
    downloaded_bytes: int = 0
    total_segments: int = 0


class DownloadManager:
    """Coordinate download tasks and expose their status for the API layer."""

    def __init__(self, output_dir: str) -> None:
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)
        self._lock = threading.Lock()
        self._statuses: Dict[str, TaskStatus] = {}
        self._tasks: Dict[str, _TaskContext] = {}

    def enqueue(
        self,
        url: str,
        filename: str,
        thread_factory: Callable[..., threading.Thread],
        *,
        task_id: Optional[str] = None,
    ) -> str:
        """Create a new download task and execute it on a worker thread."""

        if task_id is None:
            task_id = str(uuid.uuid4())
        context = _TaskContext(task_id=task_id, url=url, filename=filename)

        with self._lock:
            self._tasks[task_id] = context
            self._statuses[task_id] = TaskStatus(
                status="downloading",
                progress=0,
                message="正在解析 M3U8 文件...",
            )

        worker = thread_factory(target=self._run_task, args=(context,), daemon=True)
        worker.start()
        return task_id

    def get_status(self, task_id: str) -> Dict[str, Optional[str]]:
        with self._lock:
            status = self._statuses.get(task_id)
            if status is None:
                return TaskStatus(
                    status="not_found",
                    progress=0,
                    message="任务不存在",
                    speed=None,
                    eta=None,
                    download_url=None,
                ).to_dict()
            return status.to_dict()

    def list_files(self) -> list[Dict[str, str]]:
        files = []
        for name in os.listdir(self.output_dir):
            path = os.path.join(self.output_dir, name)
            if os.path.isfile(path):
                files.append({"name": name, "url": f"/files/{name}"})
        files.sort(key=lambda item: item["name"])
        return files

    # Internal helpers -------------------------------------------------

    def _update_status(self, task_id: str, **updates: Optional[str]) -> None:
        with self._lock:
            status = self._statuses.get(task_id)
            if not status:
                status = TaskStatus(
                    status="downloading",
                    progress=0,
                    message="正在解析 M3U8 文件...",
                )
                self._statuses[task_id] = status

            for key, value in updates.items():
                setattr(status, key, value)

    def _fail_task(self, task_id: str, message: str, *, progress: Optional[int] = None) -> None:
        status = self.get_status(task_id)
        download_url = status.get("download_url")
        payload = {
            "status": "error",
            "message": message,
            "speed": None,
            "eta": None,
            "download_url": download_url,
        }
        if progress is not None:
            payload["progress"] = progress
        else:
            payload["progress"] = status.get("progress", 0)

        self._update_status(task_id, **payload)

    def _run_task(self, context: _TaskContext) -> None:
        task_id = context.task_id
        url = context.url
        filename = context.filename
        temp_output_path: Optional[str] = None

        try:
            response = requests.get(url, headers=HEADERS, timeout=30)
            response.raise_for_status()

            playlist = m3u8.loads(response.text)

            if playlist.playlists:
                self._fail_task(
                    task_id,
                    (
                        "检测到多个视频流（variant playlist）。请从浏览器开发工具中找到具体的视频流 m3u8 链接，"
                        "而不是主索引文件。"
                    ),
                    progress=0,
                )
                return

            if not playlist.segments:
                self._fail_task(
                    task_id,
                    "未找到视频片段。请确认这是一个有效的 m3u8 视频文件链接。",
                    progress=0,
                )
                return

            base_url = url.rsplit("/", 1)[0] + "/"
            context.total_segments = len(playlist.segments)

            self._update_status(
                task_id,
                message=f"找到 {context.total_segments} 个视频片段，开始下载...",
            )

            key_cache: Dict[str, bytes] = {}
            media_sequence = getattr(playlist, "media_sequence", 0)
            output_path = os.path.join(self.output_dir, filename)
            temp_output_path = output_path + ".part"

            with open(temp_output_path, "wb") as output_file:
                for index, segment in enumerate(playlist.segments):
                    segment_url = urljoin(base_url, segment.uri)
                    if not is_safe_url(segment_url):
                        raise DownloadError(
                            f"片段 {index + 1} URL 指向内部网络资源，出于安全考虑已拒绝下载"
                        )

                    seg_response = requests.get(segment_url, headers=HEADERS, timeout=30)
                    seg_response.raise_for_status()
                    segment_data = seg_response.content

                    if segment.key and segment.key.method and segment.key.method.upper() != "NONE":
                        method = segment.key.method.upper()
                        if method != "AES-128":
                            raise DownloadError(
                                f"片段 {index + 1} 使用了不支持的加密方式: {method}"
                            )

                        key_uri = segment.key.uri
                        key_url = urljoin(base_url, key_uri)
                        if not is_safe_url(key_url):
                            raise DownloadError(
                                f"片段 {index + 1} 密钥 URL 指向内部网络资源，出于安全考虑已拒绝下载"
                            )

                        key_bytes = key_cache.get(key_url)
                        if key_bytes is None:
                            key_response = requests.get(key_url, headers=HEADERS, timeout=30)
                            key_response.raise_for_status()
                            key_bytes = key_response.content
                            key_cache[key_url] = key_bytes

                        iv_hex = segment.key.iv
                        if iv_hex:
                            iv_str = iv_hex.lower().replace("0x", "").zfill(32)
                            try:
                                iv_bytes = bytes.fromhex(iv_str)
                            except ValueError as exc:
                                raise DownloadError(f"片段 {index + 1} IV 解析失败: {exc}")
                        else:
                            sequence_number = media_sequence + index
                            iv_bytes = sequence_number.to_bytes(16, byteorder="big")

                        if len(iv_bytes) != 16:
                            raise DownloadError(f"片段 {index + 1} IV 长度无效，无法解密")

                        try:
                            cipher = AES.new(key_bytes, AES.MODE_CBC, iv=iv_bytes)
                            segment_data = cipher.decrypt(segment_data)
                        except Exception as exc:
                            raise DownloadError(f"片段 {index + 1} 解密失败: {exc}")

                    output_file.write(segment_data)
                    context.downloaded_bytes += len(segment_data)

                    progress = int((index + 1) / context.total_segments * 100)
                    elapsed = time.time() - context.start_time
                    speed_bytes_per_second = (
                        context.downloaded_bytes / elapsed if elapsed > 0 else 0
                    )
                    speed_text = (
                        f"{speed_bytes_per_second / (1024 * 1024):.2f} MB/s"
                        if speed_bytes_per_second
                        else None
                    )

                    average_segment_size = context.downloaded_bytes / (index + 1)
                    remaining_segments = context.total_segments - (index + 1)
                    remaining_bytes = average_segment_size * remaining_segments
                    eta_seconds = (
                        remaining_bytes / speed_bytes_per_second
                        if speed_bytes_per_second
                        else None
                    )

                    self._update_status(
                        task_id,
                        progress=progress,
                        speed=speed_text,
                        eta=format_eta(eta_seconds),
                        message=f"下载中... {index + 1}/{context.total_segments} ({progress}%)",
                    )

            os.replace(temp_output_path, output_path)
            self._update_status(
                task_id,
                status="completed",
                progress=100,
                message=f"下载完成！文件 {filename} 已准备好下载。",
                speed=None,
                eta=None,
                download_url=f"/files/{filename}",
            )

        except DownloadError as exc:
            self._cleanup_partial_file(temp_output_path)
            self._fail_task(task_id, str(exc))
        except Exception as exc:
            self._cleanup_partial_file(temp_output_path)
            self._fail_task(task_id, f"下载失败: {exc}")

    def _cleanup_partial_file(self, temp_output_path: Optional[str]) -> None:
        if temp_output_path and os.path.exists(temp_output_path):
            try:
                os.remove(temp_output_path)
            except OSError:
                pass


__all__ = [
    "DownloadManager",
    "DownloadError",
    "format_eta",
    "is_safe_url",
    "socket",
]
