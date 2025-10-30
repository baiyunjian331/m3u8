import os
import re
import requests
import m3u8
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory, abort, url_for
from urllib.parse import urljoin, urlparse
from werkzeug.utils import secure_filename
import threading
import ipaddress
import socket

app = Flask(__name__)

DOWNLOAD_FOLDER = 'files'
if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)

download_status = {}

def is_safe_url(url):
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        
        if not hostname:
            return False
        
        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
        except ValueError:
            try:
                resolved_ip = socket.gethostbyname(hostname)
                ip = ipaddress.ip_address(resolved_ip)
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                    return False
            except (socket.gaierror, ValueError):
                return False
        
        return True
    except Exception:
        return False

def download_m3u8(url, filename, task_id, variant_url=None):
    try:
        download_status[task_id] = {'status': 'downloading', 'progress': 0, 'message': '正在解析 M3U8 文件...'}

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        playlist = m3u8.loads(response.text)

        selected_variant_url = variant_url
        if playlist.playlists:
            best_variant = None
            for variant in playlist.playlists:
                stream_info = getattr(variant, 'stream_info', None)
                bandwidth = getattr(stream_info, 'bandwidth', 0) or 0
                variant_absolute = urljoin(url, variant.uri)

                if not best_variant or bandwidth > best_variant['bandwidth']:
                    best_variant = {
                        'bandwidth': bandwidth,
                        'url': variant_absolute
                    }

            if not selected_variant_url and best_variant:
                selected_variant_url = best_variant['url']
                if not is_safe_url(selected_variant_url):
                    download_status[task_id] = {
                        'status': 'error',
                        'message': '最高质量视频流地址不安全，已终止下载。'
                    }
                    return
                download_status[task_id]['message'] = '检测到多个清晰度，已自动选择最高质量进行下载...'

            if selected_variant_url:
                if not is_safe_url(selected_variant_url):
                    download_status[task_id] = {
                        'status': 'error',
                        'message': '所选清晰度的视频流地址不安全，已终止下载。'
                    }
                    return

                if variant_url:
                    download_status[task_id]['message'] = '已选择指定清晰度，正在准备下载...'

                variant_response = requests.get(selected_variant_url, headers=headers, timeout=30)
                variant_response.raise_for_status()
                playlist = m3u8.loads(variant_response.text)
                base_url = selected_variant_url.rsplit('/', 1)[0] + '/'
            else:
                download_status[task_id] = {
                    'status': 'error',
                    'message': '未能确定可下载的视频流，请尝试手动选择。'
                }
                return
        else:
            base_url = url.rsplit('/', 1)[0] + '/'

        if not playlist.segments:
            download_status[task_id] = {'status': 'error', 'message': '未找到视频片段。请确认这是一个有效的 m3u8 视频文件链接。'}
            return

        total_segments = len(playlist.segments)
        download_status[task_id]['message'] = f'找到 {total_segments} 个视频片段，开始下载...'
        
        ts_files = []
        failed_segments = []
        
        for i, segment in enumerate(playlist.segments):
            segment_url = urljoin(base_url, segment.uri)
            
            if not is_safe_url(segment_url):
                download_status[task_id] = {
                    'status': 'error',
                    'message': f'片段 {i + 1} URL 指向内部网络资源，出于安全考虑已拒绝下载'
                }
                return
            
            try:
                seg_response = requests.get(segment_url, headers=headers, timeout=30)
                seg_response.raise_for_status()
                ts_files.append(seg_response.content)
                
                progress = int((i + 1) / total_segments * 100)
                download_status[task_id]['progress'] = progress
                download_status[task_id]['message'] = f'下载中... {i + 1}/{total_segments} ({progress}%)'
                
            except Exception as e:
                failed_segments.append(i)
                download_status[task_id] = {
                    'status': 'error',
                    'message': f'片段 {i + 1} 下载失败: {str(e)}。已下载的片段: {i}/{total_segments}'
                }
                return
        
        if failed_segments:
            download_status[task_id] = {
                'status': 'error',
                'message': f'下载失败。有 {len(failed_segments)} 个片段下载失败。'
            }
            return
        
        output_path = os.path.join(DOWNLOAD_FOLDER, filename)
        
        with open(output_path, 'wb') as f:
            for ts_data in ts_files:
                f.write(ts_data)
        
        download_status[task_id] = {
            'status': 'completed',
            'progress': 100,
            'message': f'下载完成！文件已保存到: {output_path}'
        }
        
    except Exception as e:
        download_status[task_id] = {
            'status': 'error',
            'message': f'下载失败: {str(e)}'
        }

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/download', methods=['POST'])
def start_download():
    data = request.json or {}
    url = data.get('url')
    filename = data.get('filename', 'video.mp4')
    variant_uri = data.get('variant_uri')

    if not url:
        return jsonify({'error': '请提供 M3U8 链接'}), 400

    if not url.startswith(('http://', 'https://')):
        return jsonify({'error': '无效的 URL 格式'}), 400

    if not is_safe_url(url):
        return jsonify({'error': 'URL 指向内部网络资源，出于安全考虑已拒绝'}), 400

    filename = secure_filename(filename)
    if not filename:
        filename = 'video.mp4'

    if not filename.endswith('.mp4'):
        filename += '.mp4'

    safe_path = os.path.abspath(os.path.join(DOWNLOAD_FOLDER, filename))
    if not safe_path.startswith(os.path.abspath(DOWNLOAD_FOLDER)):
        return jsonify({'error': '无效的文件名'}), 400

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }

    if not variant_uri:
        try:
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
        except Exception as exc:
            return jsonify({'error': f'无法访问 M3U8 文件: {str(exc)}'}), 400

        playlist = m3u8.loads(response.text)

        if playlist.playlists:
            variants = []
            best_variant_uri = None
            best_bandwidth = -1

            for index, variant in enumerate(playlist.playlists):
                stream_info = getattr(variant, 'stream_info', None)
                bandwidth = getattr(stream_info, 'bandwidth', 0) or 0
                resolution = getattr(stream_info, 'resolution', None)
                resolution_text = None
                if resolution and isinstance(resolution, (list, tuple)) and len(resolution) == 2:
                    resolution_text = f"{resolution[0]}x{resolution[1]}"

                if bandwidth > best_bandwidth:
                    best_bandwidth = bandwidth
                    best_variant_uri = variant.uri

                variants.append({
                    'uri': variant.uri,
                    'bandwidth': bandwidth,
                    'resolution': resolution_text,
                    'codecs': getattr(stream_info, 'codecs', None),
                    'index': index
                })

            if variants:
                return jsonify({
                    'message': '检测到多个清晰度，请选择后继续下载。',
                    'variants': variants,
                    'default': best_variant_uri
                })

        if not playlist.segments:
            return jsonify({'error': '未找到视频片段。请确认这是一个有效的 m3u8 视频文件链接。'}), 400

    selected_variant_url = None
    if variant_uri:
        selected_variant_url = urljoin(url, variant_uri)
        if not is_safe_url(selected_variant_url):
            return jsonify({'error': '所选清晰度的链接指向内部网络资源，已拒绝请求。'}), 400

    task_id = str(hash(url + filename + (selected_variant_url or '')))

    thread = threading.Thread(target=download_m3u8, args=(url, filename, task_id, selected_variant_url))
    thread.daemon = True
    thread.start()

    return jsonify({'task_id': task_id, 'message': '开始下载任务'})

@app.route('/status/<task_id>')
def get_status(task_id):
    status = download_status.get(task_id, {'status': 'not_found', 'message': '任务不存在'})
    return jsonify(status)

def format_file_size(size_bytes):
    if size_bytes == 0:
        return '0 B'
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    size = float(size_bytes)
    unit_index = 0
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    return f"{size:.2f} {units[unit_index]}"


@app.route('/files')
def list_files():
    files = []
    for entry in os.scandir(DOWNLOAD_FOLDER):
        if entry.is_file():
            stat = entry.stat()
            files.append({
                'name': entry.name,
                'url': url_for('serve_file', filename=entry.name),
                'size': format_file_size(stat.st_size),
                'created_at': datetime.fromtimestamp(stat.st_ctime).strftime('%Y-%m-%d %H:%M:%S'),
                '_sort_key': stat.st_ctime
            })

    files.sort(key=lambda item: item['_sort_key'], reverse=True)
    for file_item in files:
        file_item.pop('_sort_key', None)

    return jsonify({'files': files})


@app.route('/files/<path:filename>')
def serve_file(filename):
    download_root = os.path.abspath(DOWNLOAD_FOLDER)
    requested_path = os.path.abspath(os.path.join(download_root, filename))

    try:
        common_path = os.path.commonpath([download_root, requested_path])
    except ValueError:
        abort(404)

    if common_path != download_root:
        abort(404)

    if not os.path.isfile(requested_path):
        abort(404)

    safe_relative_path = os.path.relpath(requested_path, download_root)
    return send_from_directory(download_root, safe_relative_path, as_attachment=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
