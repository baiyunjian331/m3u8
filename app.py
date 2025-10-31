"""Flask application that mirrors the get-m3u8 user experience."""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Dict
from urllib.parse import urlparse

import ipaddress
import socket
from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    send_from_directory,
)
from werkzeug.utils import secure_filename

from downloader import DownloadManager, DownloadTaskOptions


app = Flask(__name__)

BASE_DIR = Path(__file__).parent
DOWNLOAD_FOLDER = BASE_DIR / "files"
DOWNLOAD_FOLDER.mkdir(exist_ok=True)

manager = DownloadManager(DOWNLOAD_FOLDER)
HEADERS: Dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
    )
}


def is_safe_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return False
        try:
            ip_obj = ipaddress.ip_address(hostname)
            addresses = [ip_obj]
        except ValueError:
            try:
                addr_info = socket.getaddrinfo(hostname, None)
            except socket.gaierror:
                return False
            addresses = []
            for info in addr_info:
                sockaddr = info[4]
                if not sockaddr:
                    continue
                try:
                    addresses.append(ipaddress.ip_address(sockaddr[0]))
                except ValueError:
                    return False
        if not addresses:
            return False
        for ip_obj in addresses:
            if (
                ip_obj.is_private
                or ip_obj.is_loopback
                or ip_obj.is_link_local
                or ip_obj.is_reserved
            ):
                return False
        return True
    except Exception:
        return False


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/tasks", methods=["POST"])
def create_task():
    payload = request.get_json(force=True, silent=True) or {}
    url = payload.get("url", "").strip()
    title = payload.get("title") or ""
    output_format = (payload.get("output_format") or "ts").lower()
    start_segment = payload.get("start_segment")
    end_segment = payload.get("end_segment")
    stream_to_disk = bool(payload.get("stream_to_disk", True))
    decrypt = bool(payload.get("decrypt", True))
    headers = {**HEADERS, **(payload.get("headers") or {})}
    autostart_raw = payload.get("autostart")
    if autostart_raw is None:
        autostart = True
    elif isinstance(autostart_raw, str):
        autostart = autostart_raw.strip().lower() not in {"0", "false", "no"}
    else:
        autostart = bool(autostart_raw)

    if not url:
        return jsonify({"error": "请输入有效的 M3U8 地址"}), 400
    if not is_safe_url(url):
        return jsonify({"error": "只允许下载公网地址"}), 400

    try:
        options = DownloadTaskOptions(
            url=url,
            title=title or derive_title_from_url(url),
            output_format=output_format,
            start_segment=int(start_segment) if start_segment else None,
            end_segment=int(end_segment) if end_segment else None,
            stream_to_disk=stream_to_disk,
            decrypt=decrypt,
            headers=headers,
        )
        options.validate()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

    task_id = str(uuid.uuid4())
    manager.create_task(task_id, options)
    if autostart:
        manager.start_task(task_id)
    return jsonify({"task_id": task_id})


@app.route("/tasks", methods=["GET"])
def list_tasks():
    return jsonify({
        "tasks": [task.to_dict() for task in manager.list_tasks()],
    })


@app.route("/tasks/<task_id>")
def get_task(task_id: str):
    try:
        task = manager.get_task(task_id)
    except KeyError:
        return jsonify({"error": "任务不存在"}), 404
    return jsonify(task.to_dict())


@app.route("/tasks/<task_id>/retry", methods=["POST"])
def retry_segment(task_id: str):
    payload = request.get_json(force=True, silent=True) or {}
    index = payload.get("segment_index")
    if index is None:
        return jsonify({"error": "缺少 segment_index 参数"}), 400
    try:
        manager.retry_segment(task_id, int(index))
    except (KeyError, IndexError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"status": "ok"})


@app.route("/tasks/<task_id>/force-save", methods=["POST"])
def force_save(task_id: str):
    try:
        manager.force_save(task_id)
    except KeyError:
        return jsonify({"error": "任务不存在"}), 404
    return jsonify({"status": "ok"})


@app.route("/tasks/<task_id>/start", methods=["POST"])
def start_task(task_id: str):
    try:
        task = manager.start_task(task_id)
    except KeyError:
        return jsonify({"error": "任务不存在"}), 404
    return jsonify(task.to_dict())


@app.route("/tasks/<task_id>/pause", methods=["POST"])
def pause_task(task_id: str):
    try:
        manager.pause_task(task_id)
        task = manager.get_task(task_id)
    except KeyError:
        return jsonify({"error": "任务不存在"}), 404
    return jsonify(task.to_dict())


@app.route("/tasks/<task_id>/resume", methods=["POST"])
def resume_task(task_id: str):
    try:
        task = manager.resume_task(task_id)
    except KeyError:
        return jsonify({"error": "任务不存在"}), 404
    return jsonify(task.to_dict())


@app.route("/tasks/<task_id>", methods=["DELETE"])
def delete_task(task_id: str):
    payload = request.get_json(force=True, silent=True) or {}
    remove_flag = payload.get("remove_files")
    if remove_flag is None:
        args = getattr(request, "args", {}) or {}
        remove_flag = args.get("remove_files")
    remove_files = str(remove_flag).lower() in {"1", "true", "yes"}
    try:
        manager.delete_task(task_id, remove_files=remove_files)
    except KeyError:
        return jsonify({"error": "任务不存在"}), 404
    return jsonify({"status": "ok"})


@app.route("/files/<path:filename>")
def download_file(filename: str):
    safe_name = secure_filename(filename)
    return send_from_directory(DOWNLOAD_FOLDER, safe_name, as_attachment=True)


def derive_title_from_url(url: str) -> str:
    name = urlparse(url).path.rsplit("/", 1)[-1]
    if not name:
        return "video"
    return name.split(".")[0]


if __name__ == "__main__":  # pragma: no cover
    app.run(debug=True)
