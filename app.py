import os
import re
import requests
import m3u8
import time
from Crypto.Cipher import AES
from flask import Flask, render_template, request, jsonify, send_from_directory, abort
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


def format_eta(seconds):
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

def is_safe_url(url):
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

            for _, _, _, _, sockaddr in addr_info:
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

def download_m3u8(url, filename, task_id):
    try:
        start_time = time.time()
        downloaded_bytes = 0
        temp_output_path = None
        download_status[task_id] = {
            'status': 'downloading',
            'progress': 0,
            'message': '正在解析 M3U8 文件...',
            'speed': None,
            'eta': None,
            'download_url': None
        }
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        
        playlist = m3u8.loads(response.text)
        
        if playlist.playlists:
            current_status = download_status.get(task_id, {})
            download_status[task_id] = {
                'status': 'error',
                'message': (
                    '检测到多个视频流（variant playlist）。请从浏览器开发工具中找到具体的视频流 m3u8 链接，'
                    '而不是主索引文件。'
                ),
                'progress': 0,
                'speed': None,
                'eta': None,
                'download_url': current_status.get('download_url')
            }
            return

        if not playlist.segments:
            current_status = download_status.get(task_id, {})
            download_status[task_id] = {
                'status': 'error',
                'message': '未找到视频片段。请确认这是一个有效的 m3u8 视频文件链接。',
                'progress': 0,
                'speed': None,
                'eta': None,
                'download_url': current_status.get('download_url')
            }
            return
        
        base_url = url.rsplit('/', 1)[0] + '/'
        
        total_segments = len(playlist.segments)
        download_status[task_id]['message'] = f'找到 {total_segments} 个视频片段，开始下载...'

        key_cache = {}
        media_sequence = getattr(playlist, 'media_sequence', 0)
        output_path = os.path.join(DOWNLOAD_FOLDER, filename)
        temp_output_path = output_path + '.part'

        with open(temp_output_path, 'wb') as output_file:

            def abort_download(message):
                current_status = download_status.get(task_id, {})
                progress = current_status.get('progress', 0)
                download_status[task_id] = {
                    'status': 'error',
                    'message': message,
                    'progress': progress,
                    'speed': None,
                    'eta': None,
                    'download_url': current_status.get('download_url')
                }
                try:
                    output_file.close()
                finally:
                    try:
                        if os.path.exists(temp_output_path):
                            os.remove(temp_output_path)
                    except OSError:
                        pass
                return

            for i, segment in enumerate(playlist.segments):
                segment_url = urljoin(base_url, segment.uri)

                if not is_safe_url(segment_url):
                    abort_download(f'片段 {i + 1} URL 指向内部网络资源，出于安全考虑已拒绝下载')
                    return

                try:
                    seg_response = requests.get(segment_url, headers=headers, timeout=30)
                    seg_response.raise_for_status()

                    segment_data = seg_response.content

                    if segment.key and segment.key.method and segment.key.method.upper() != 'NONE':
                        method = segment.key.method.upper()

                        if method != 'AES-128':
                            abort_download(f'片段 {i + 1} 使用了不支持的加密方式: {method}')
                            return

                        key_uri = segment.key.uri
                        key_url = urljoin(base_url, key_uri)

                        if not is_safe_url(key_url):
                            abort_download(f'片段 {i + 1} 密钥 URL 指向内部网络资源，出于安全考虑已拒绝下载')
                            return

                        key_bytes = key_cache.get(key_url)
                        if key_bytes is None:
                            try:
                                key_response = requests.get(key_url, headers=headers, timeout=30)
                                key_response.raise_for_status()
                                key_bytes = key_response.content
                                key_cache[key_url] = key_bytes
                            except Exception as key_error:
                                abort_download(f'片段 {i + 1} 密钥下载失败: {str(key_error)}')
                                return

                        iv_hex = segment.key.iv
                        if iv_hex:
                            iv_str = iv_hex.lower().replace('0x', '')
                            iv_str = iv_str.zfill(32)
                            try:
                                iv_bytes = bytes.fromhex(iv_str)
                            except ValueError as iv_error:
                                abort_download(f'片段 {i + 1} IV 解析失败: {str(iv_error)}')
                                return
                        else:
                            sequence_number = media_sequence + i
                            iv_bytes = sequence_number.to_bytes(16, byteorder='big')

                        if len(iv_bytes) != 16:
                            abort_download(f'片段 {i + 1} IV 长度无效，无法解密')
                            return

                        try:
                            cipher = AES.new(key_bytes, AES.MODE_CBC, iv=iv_bytes)
                            segment_data = cipher.decrypt(segment_data)
                        except Exception as decrypt_error:
                            abort_download(f'片段 {i + 1} 解密失败: {str(decrypt_error)}')
                            return

                    output_file.write(segment_data)
                    downloaded_bytes += len(segment_data)

                    progress = int((i + 1) / total_segments * 100)
                    download_status[task_id]['progress'] = progress

                    elapsed = time.time() - start_time
                    speed_bytes_per_second = downloaded_bytes / elapsed if elapsed > 0 else 0
                    speed_mb_per_second = speed_bytes_per_second / (1024 * 1024) if speed_bytes_per_second else 0

                    average_segment_size = downloaded_bytes / (i + 1)
                    remaining_segments = total_segments - (i + 1)
                    remaining_bytes = average_segment_size * remaining_segments
                    eta_seconds = remaining_bytes / speed_bytes_per_second if speed_bytes_per_second else None

                    speed_text = f"{speed_mb_per_second:.2f} MB/s" if speed_bytes_per_second else None
                    eta_text = format_eta(eta_seconds)

                    download_status[task_id]['speed'] = speed_text
                    download_status[task_id]['eta'] = eta_text
                    download_status[task_id]['message'] = f'下载中... {i + 1}/{total_segments} ({progress}%)'

                except Exception as e:
                    abort_download(f'片段 {i + 1} 下载失败: {str(e)}。已下载的片段: {i}/{total_segments}')
                    return

        try:
            os.replace(temp_output_path, output_path)
        except Exception as replace_error:
            if os.path.exists(temp_output_path):
                try:
                    os.remove(temp_output_path)
                except OSError:
                    pass
            current_status = download_status.get(task_id, {})
            progress = current_status.get('progress', 0)
            download_status[task_id] = {
                'status': 'error',
                'message': f'合并片段失败: {str(replace_error)}',
                'progress': progress,
                'speed': None,
                'eta': None,
                'download_url': current_status.get('download_url')
            }
            return

        download_status[task_id] = {
            'status': 'completed',
            'progress': 100,
            'message': f'下载完成！文件 {filename} 已准备好下载。',
            'speed': None,
            'eta': None,
            'download_url': f'/files/{filename}'
        }

    except Exception as e:
        if temp_output_path and os.path.exists(temp_output_path):
            try:
                os.remove(temp_output_path)
            except OSError:
                pass
        current_status = download_status.get(task_id, {})
        download_status[task_id] = {
            'status': 'error',
            'message': f'下载失败: {str(e)}',
            'progress': download_status.get(task_id, {}).get('progress', 0),
            'speed': None,
            'eta': None,
            'download_url': current_status.get('download_url')
        }

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/download', methods=['POST'])
def start_download():
    data = request.json
    url = data.get('url')
    filename = data.get('filename', 'video.mp4')
    
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
    
    task_id = str(hash(url + filename))
    
    thread = threading.Thread(target=download_m3u8, args=(url, filename, task_id))
    thread.daemon = True
    thread.start()
    
    return jsonify({'task_id': task_id, 'message': '开始下载任务'})

@app.route('/status/<task_id>')
def get_status(task_id):
    status = download_status.get(task_id)
    if status is None:
        status = {
            'status': 'not_found',
            'message': '任务不存在',
            'progress': 0,
            'speed': None,
            'eta': None,
            'download_url': None
        }
    return jsonify(status)

@app.route('/files')
def list_files():
    files = []
    for name in os.listdir(DOWNLOAD_FOLDER):
        file_path = os.path.join(DOWNLOAD_FOLDER, name)
        if os.path.isfile(file_path):
            files.append({'name': name, 'url': f'/files/{name}'})
    files.sort(key=lambda x: x['name'])
    return jsonify({'files': files})

@app.route('/files/<path:filename>')
def download_file(filename):
    safe_path = os.path.abspath(os.path.join(DOWNLOAD_FOLDER, filename))
    download_root = os.path.abspath(DOWNLOAD_FOLDER)
    if not safe_path.startswith(download_root) or not os.path.isfile(safe_path):
        abort(404)
    return send_from_directory(download_root, filename, as_attachment=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
