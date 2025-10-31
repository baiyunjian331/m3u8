"""Download management inspired by the get-m3u8 project."""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin

import m3u8
import requests
from Crypto.Cipher import AES


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


class DownloadError(Exception):
    """Raised when a task cannot be completed."""


@dataclass
class DownloadTaskOptions:
    """Options that mirror the capabilities advertised by get-m3u8."""

    url: str
    title: str
    output_format: str = "ts"
    start_segment: Optional[int] = None
    end_segment: Optional[int] = None
    stream_to_disk: bool = True
    max_retries: int = 3
    decrypt: bool = True
    headers: Dict[str, str] = field(default_factory=dict)

    def normalized_headers(self) -> Dict[str, str]:
        headers = DEFAULT_HEADERS.copy()
        headers.update(self.headers)
        return headers

    def validate(self) -> None:
        if not self.url:
            raise ValueError("url is required")
        if self.output_format not in {"ts", "mp4"}:
            raise ValueError("output_format must be 'ts' or 'mp4'")
        if self.start_segment is not None and self.start_segment < 1:
            raise ValueError("start_segment must be >= 1")
        if (
            self.end_segment is not None
            and self.start_segment is not None
            and self.end_segment < self.start_segment
        ):
            raise ValueError("end_segment must be greater or equal to start_segment")


@dataclass
class SegmentStatus:
    index: int
    url: str
    duration: Optional[float]
    key_uri: Optional[str]
    iv: Optional[bytes]
    method: Optional[str]
    status: str = "pending"
    size: int = 0
    retries: int = 0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "index": self.index,
            "url": self.url,
            "duration": self.duration,
            "status": self.status,
            "size": self.size,
            "retries": self.retries,
            "error": self.error,
        }


class DownloadTask:
    """Background worker that downloads a single playlist."""

    def __init__(self, task_id: str, options: DownloadTaskOptions, output_dir: Path):
        self.id = task_id
        self.options = options
        self.output_dir = output_dir
        self.created_at = time.time()
        self.started_at: Optional[float] = None
        self.completed_at: Optional[float] = None
        self.status = "ready"
        self.message: Optional[str] = None
        self.playlist_url: Optional[str] = None

        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._force_event = threading.Event()
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._current_key_uri: Optional[str] = None
        self._current_key_value: Optional[bytes] = None
        self._segments: List[SegmentStatus] = []
        self._cursor = 0

        safe_title = self._make_safe_title(options.title)
        self._base_name = safe_title
        self.temp_path = output_dir / f"{task_id}.download"
        self.ts_path = output_dir / f"{safe_title}.ts"
        suffix = ".mp4" if options.output_format == "mp4" else ".ts"
        self.output_path = output_dir / f"{safe_title}{suffix}"

        self.total_bytes = 0
        self.speed_bps: Optional[float] = None
        self.eta_seconds: Optional[float] = None
        self._last_stat_update = time.time()
        self._bytes_since_last_stat = 0
        self._ffmpeg_missing = False

    # ------------------------------------------------------------------
    @staticmethod
    def _make_safe_title(title: str) -> str:
        candidate = title.strip() or "video"
        cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in candidate)
        return cleaned[:80]

    def start(self) -> None:
        resume = False
        thread_to_start: Optional[threading.Thread] = None
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                if not self._pause_event.is_set():
                    self._pause_event.set()
                    resume = True
            else:
                if self.status in {"completed", "forced"}:
                    return
                self.options.validate()
                self._stop_event.clear()
                self._force_event.clear()
                self._pause_event.set()
                self._thread = threading.Thread(target=self._run, name=f"task-{self.id}")
                self._thread.daemon = True
                thread_to_start = self._thread
        if thread_to_start:
            thread_to_start.start()
        if resume:
            self._update_status("downloading", "Resuming download")

    def request_force_save(self) -> None:
        self._force_event.set()

    def stop(self) -> None:
        self._stop_event.set()
        self._pause_event.set()
        thread = None
        with self._lock:
            thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=1)

    def pause(self) -> None:
        with self._lock:
            if not self._thread or not self._thread.is_alive():
                return
            self._pause_event.clear()
        self._update_status("paused", "Download paused")

    def resume(self) -> None:
        self.start()

    def cleanup(self, remove_outputs: bool = False) -> None:
        if self.temp_path.exists():
            try:
                self.temp_path.unlink()
            except OSError:
                pass
        if remove_outputs:
            paths = {self.ts_path}
            if self.output_path:
                paths.add(self.output_path)
            for path in paths:
                if path.exists():
                    try:
                        path.unlink()
                    except OSError:
                        pass

    def retry_segment(self, segment_index: int) -> None:
        with self._lock:
            if segment_index < 0 or segment_index >= len(self._segments):
                raise IndexError("segment index out of range")
            segment = self._segments[segment_index]
            segment.status = "pending"
            segment.error = None
            segment.retries = 0
            if self._cursor > segment_index:
                self._cursor = segment_index

    def to_dict(self) -> Dict[str, object]:
        with self._lock:
            completed = len([s for s in self._segments if s.status == "completed"])
            total = len(self._segments)
            progress = completed / total if total else 0
            return {
                "id": self.id,
                "title": self.options.title,
                "output_format": self.options.output_format,
                "start_segment": self.options.start_segment,
                "end_segment": self.options.end_segment,
                "stream_to_disk": self.options.stream_to_disk,
                "decrypt": self.options.decrypt,
                "status": self.status,
                "message": self.message,
                "created_at": self.created_at,
                "started_at": self.started_at,
                "completed_at": self.completed_at,
                "segments": [segment.to_dict() for segment in self._segments],
                "total_segments": total,
                "downloaded_segments": completed,
                "progress": progress,
                "total_bytes": self.total_bytes,
                "speed_bps": self.speed_bps,
                "eta_seconds": self.eta_seconds,
                "output_path": str(self.output_path) if self.output_path else None,
                "ffmpeg_missing": self._ffmpeg_missing,
            }

    # ------------------------------------------------------------------
    def _run(self) -> None:
        self.started_at = time.time()
        try:
            self._update_status("preparing", "Resolving playlist")
            playlist = self._load_playlist()
            self._prepare_segments(playlist)
            if not self._segments:
                raise DownloadError("Playlist does not contain any segments")

            self._update_status("downloading", "Downloading segments")
            self._download_segments()
            if self.status in {"stopped", "forced"}:
                return
            if self.options.output_format == "mp4":
                self._convert_to_mp4()
            self.completed_at = time.time()
            if self.status != "error":
                self._update_status("completed", "Download finished")
        except DownloadError as exc:
            self._update_status("error", str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            self._update_status("error", f"Unexpected error: {exc}")
        finally:
            with self._lock:
                if self._thread is threading.current_thread():
                    self._thread = None
                self._pause_event.set()

    def _update_status(self, status: str, message: Optional[str] = None) -> None:
        with self._lock:
            self.status = status
            if message is not None:
                self.message = message

    def _load_playlist(self) -> m3u8.M3U8:
        response = requests.get(
            self.options.url,
            headers=self.options.normalized_headers(),
            timeout=30,
        )
        response.raise_for_status()
        playlist = m3u8.loads(response.text)
        self.playlist_url = response.url
        if playlist.is_variant:
            raise DownloadError(
                "Variant playlists are not supported. Provide a media playlist URL."
            )
        return playlist

    def _prepare_segments(self, playlist: m3u8.M3U8) -> None:
        base_uri = playlist.base_uri or self.playlist_url or self.options.url
        prepared: List[SegmentStatus] = []
        for idx, segment in enumerate(playlist.segments):
            position = idx + 1
            if self.options.start_segment and position < self.options.start_segment:
                continue
            if self.options.end_segment and position > self.options.end_segment:
                break
            key = segment.key or playlist.keys and playlist.keys[-1]
            key_uri: Optional[str] = None
            iv: Optional[bytes] = None
            method: Optional[str] = None
            if key and key.uri:
                key_uri = key.absolute_uri or urljoin(base_uri, key.uri)
                method = (key.method or "").upper()
                if key.iv:
                    iv = self._parse_iv(key.iv)
                else:
                    iv = position.to_bytes(16, "big")
            prepared.append(
                SegmentStatus(
                    index=len(prepared),
                    url=segment.absolute_uri or urljoin(base_uri, segment.uri),
                    duration=segment.duration,
                    key_uri=key_uri,
                    iv=iv,
                    method=method,
                )
            )
        with self._lock:
            self._segments = prepared
            self._cursor = 0

    @staticmethod
    def _parse_iv(raw_iv: str) -> bytes:
        value = raw_iv.strip()
        if value.startswith("0x") or value.startswith("0X"):
            value = value[2:]
        value = value.zfill(32)
        return bytes.fromhex(value)

    def _download_segments(self) -> None:
        data_buffer: List[bytes] = []
        if self.temp_path.exists():
            self.temp_path.unlink()

        while self._cursor < len(self._segments):
            if self._stop_event.is_set():
                self._update_status("stopped", "Download cancelled")
                return
            if self._force_event.is_set():
                self._update_status("forced", "Partial download saved")
                break
            if not self._pause_event.is_set():
                if self.status != "paused":
                    self._update_status("paused", "Download paused")
                # Wait until resume or cancellation.
                if not self._pause_event.wait(timeout=0.2):
                    continue
                self._update_status("downloading", "Downloading segments")

            segment = self._segments[self._cursor]
            try:
                payload = self._download_single_segment(segment)
                segment.status = "completed"
                segment.error = None
                segment.size = len(payload)
                if self.options.stream_to_disk:
                    self._append_to_temp(payload)
                else:
                    data_buffer.append(payload)
                self._cursor += 1
            except DownloadError as exc:
                segment.retries += 1
                segment.status = "failed"
                segment.error = str(exc)
                if segment.retries > self.options.max_retries:
                    raise DownloadError(
                        f"Segment {segment.index} failed after {segment.retries} retries: {exc}"
                    )
                time.sleep(1)
            finally:
                self._update_stats()

        if not self.options.stream_to_disk and data_buffer and self.status != "forced":
            for chunk in data_buffer:
                self._append_to_temp(chunk)

        if self.status == "forced":
            final_path = self.output_dir / f"{self._base_name}.partial.ts"
        else:
            final_path = self.ts_path
        if final_path.exists():
            final_path.unlink()
        if self.temp_path.exists():
            shutil.move(str(self.temp_path), final_path)
        self.output_path = final_path if self.options.output_format == "ts" or self.status == "forced" else self.output_path

    def _append_to_temp(self, chunk: bytes) -> None:
        with open(self.temp_path, "ab") as handle:
            handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())

    def _download_single_segment(self, segment: SegmentStatus) -> bytes:
        segment.status = "downloading"
        response = requests.get(
            segment.url,
            headers=self.options.normalized_headers(),
            timeout=30,
        )
        if response.status_code >= 400:
            raise DownloadError(f"HTTP {response.status_code}")
        payload = response.content
        key, iv = self._resolve_key(segment)
        if key and iv and segment.method == "AES-128":
            cipher = AES.new(key, AES.MODE_CBC, iv)
            payload = cipher.decrypt(payload)
        self.total_bytes += len(payload)
        self._bytes_since_last_stat += len(payload)
        return payload

    def _resolve_key(self, segment: SegmentStatus) -> (Optional[bytes], Optional[bytes]):
        if not self.options.decrypt or not segment.key_uri:
            return None, None
        if segment.key_uri != self._current_key_uri or self._current_key_value is None:
            response = requests.get(
                segment.key_uri,
                headers=self.options.normalized_headers(),
                timeout=30,
            )
            response.raise_for_status()
            self._current_key_uri = segment.key_uri
            self._current_key_value = response.content
        return self._current_key_value, segment.iv

    def _convert_to_mp4(self) -> None:
        if self.status == "forced":
            return
        if not self.ts_path.exists():
            raise DownloadError("TS file missing for conversion")
        if shutil.which("ffmpeg") is None:
            self._ffmpeg_missing = True
            self.output_path = self.ts_path
            self._update_status(
                "completed",
                "ffmpeg not available; saved TS stream instead of MP4",
            )
            return
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(self.ts_path),
            "-c",
            "copy",
            str(self.output_path),
        ]
        completed = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if completed.returncode != 0:
            raise DownloadError(
                f"ffmpeg failed: {completed.stderr.decode('utf-8', 'ignore')[:200]}"
            )
        if self.output_path != self.ts_path and self.ts_path.exists():
            self.ts_path.unlink()

    def _update_stats(self) -> None:
        now = time.time()
        elapsed_since_last = now - self._last_stat_update
        total_elapsed = (now - self.started_at) if self.started_at else None
        if elapsed_since_last >= 1:
            if elapsed_since_last > 0:
                self.speed_bps = self._bytes_since_last_stat / elapsed_since_last
            self._bytes_since_last_stat = 0
            self._last_stat_update = now
        if total_elapsed and total_elapsed > 0:
            completed = len([s for s in self._segments if s.status == "completed"])
            if completed:
                avg_per_segment = total_elapsed / completed
                remaining = len(self._segments) - completed
                self.eta_seconds = remaining * avg_per_segment


class DownloadManager:
    """Coordinates multiple download tasks."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._tasks: Dict[str, DownloadTask] = {}
        self._lock = threading.RLock()

    def create_task(self, task_id: str, options: DownloadTaskOptions) -> DownloadTask:
        task = DownloadTask(task_id, options, self.output_dir)
        with self._lock:
            self._tasks[task_id] = task
        return task

    def get_task(self, task_id: str) -> DownloadTask:
        with self._lock:
            if task_id not in self._tasks:
                raise KeyError(task_id)
            return self._tasks[task_id]

    def list_tasks(self) -> List[DownloadTask]:
        with self._lock:
            return list(self._tasks.values())

    def start_task(self, task_id: str) -> DownloadTask:
        task = self.get_task(task_id)
        task.start()
        return task

    def pause_task(self, task_id: str) -> None:
        task = self.get_task(task_id)
        task.pause()

    def resume_task(self, task_id: str) -> DownloadTask:
        task = self.get_task(task_id)
        task.resume()
        return task

    def delete_task(self, task_id: str, remove_files: bool = False) -> None:
        task = self.get_task(task_id)
        task.stop()
        task.cleanup(remove_outputs=remove_files)
        with self._lock:
            self._tasks.pop(task_id, None)

    def retry_segment(self, task_id: str, segment_index: int) -> None:
        task = self.get_task(task_id)
        task.retry_segment(segment_index)

    def force_save(self, task_id: str) -> None:
        task = self.get_task(task_id)
        task.request_force_save()
