import os
import threading
import uuid

from flask import Flask, render_template, request, jsonify, send_from_directory, abort
from werkzeug.utils import secure_filename

from downloader import DownloadManager, is_safe_url, socket as downloader_socket

app = Flask(__name__)

DOWNLOAD_FOLDER = "files"
download_manager = DownloadManager(DOWNLOAD_FOLDER)

socket = downloader_socket


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/download", methods=["POST"])
def start_download():
    data = request.json or {}
    url = data.get("url")
    filename = data.get("filename", "video.mp4")

    if not url:
        return jsonify({"error": "请提供 M3U8 链接"}), 400

    if not url.startswith(("http://", "https://")):
        return jsonify({"error": "无效的 URL 格式"}), 400

    if not is_safe_url(url):
        return jsonify({"error": "URL 指向内部网络资源，出于安全考虑已拒绝"}), 400

    filename = secure_filename(filename)
    if not filename:
        filename = "video.mp4"

    if not filename.endswith(".mp4"):
        filename += ".mp4"

    safe_path = os.path.abspath(os.path.join(DOWNLOAD_FOLDER, filename))
    if not safe_path.startswith(os.path.abspath(DOWNLOAD_FOLDER)):
        return jsonify({"error": "无效的文件名"}), 400

    task_id = str(uuid.uuid4())
    download_manager.enqueue(
        url,
        filename,
        thread_factory=threading.Thread,
        task_id=task_id,
    )
    return jsonify({"task_id": task_id, "message": "开始下载任务"})


@app.route("/status/<task_id>")
def get_status(task_id):
    return jsonify(download_manager.get_status(task_id))


@app.route("/files")
def list_files():
    return jsonify({"files": download_manager.list_files()})


@app.route("/files/<path:filename>")
def download_file(filename):
    download_root = download_manager.output_dir
    safe_path = os.path.abspath(os.path.join(download_root, filename))
    if not safe_path.startswith(os.path.abspath(download_root)) or not os.path.isfile(safe_path):
        abort(404)
    return send_from_directory(download_root, filename, as_attachment=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
