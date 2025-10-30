# Overview

This is an M3U8 video downloader tool that provides multiple interfaces for downloading M3U8 streaming videos. The project includes a web-based downloader (browser tool), a Flask web server version, and a desktop GUI application built with PySimpleGUI. The core functionality involves parsing M3U8 playlist files, downloading video segments (.ts files), and saving them to the server's `files/` directory.

The project is based on the original GitHub repository https://github.com/Momo707577045/m3u8-downloader, with additional implementations for server-based and desktop usage.

**Current Active Version**: Flask web server (`app.py`) - Downloads videos to server's `files/` folder instead of user's local computer.

# User Preferences

Preferred communication style: Simple, everyday language.

# Recent Changes (October 30, 2025)

- Created server-side M3U8 downloader using Flask
- Downloads save to `files/` folder on server instead of client's computer
- Added security features:
  - Path traversal protection using `secure_filename`
  - URL validation to prevent SSRF attacks
  - IP address filtering (blocks private/loopback/link-local addresses)
  - Segment URL validation for each video chunk
- Enhanced error handling:
  - Detects variant playlists and provides helpful error messages
  - Aborts download immediately on segment failure
  - Shows real-time progress and status updates
- Modern, responsive web interface with progress tracking

# System Architecture

## Multi-Interface Architecture

The application provides three distinct interfaces for the same core functionality:

1. **Web Browser Tool** (`m3u8-downloader/` directory) - Client-side JavaScript application that runs entirely in the browser using Service Workers for stream processing
2. **Flask Web Server** (`app.py`) - Server-side Python application that handles M3U8 downloads via HTTP API
3. **Desktop GUI** (`main.py`) - Standalone Windows application with PySimpleGUI interface

**Rationale**: Multiple interfaces allow users to choose based on their needs - browser for quick access without installation, web server for centralized downloading, and desktop app for offline usage.

## Browser-Based Architecture (m3u8-downloader/)

### Service Worker Pattern
- Uses Service Workers (`serviceWorker.js`) as a proxy layer to intercept network requests
- Implements stream-based downloading using ReadableStream API for memory-efficient processing
- MITM page (`mitm.html`) acts as an isolation layer between main process and Service Worker

**Pros**: 
- No server required, fully client-side
- Leverages browser's native streaming capabilities
- Works across platforms without installation

**Cons**:
- Limited to HTTPS contexts for Service Worker support
- Browser compatibility issues (Safari fallback required)
- CORS restrictions for cross-domain resources

### Streaming Download Architecture
- Uses MessageChannel API for communication between contexts
- TransformStream support detection with fallback to Blob downloads
- StreamSaver.js library manages the streaming download pipeline

## Flask Server Architecture (app.py)

### Request Processing Flow
1. User submits M3U8 URL via web interface
2. Server validates URL safety (prevents SSRF attacks)
3. Parses M3U8 playlist and downloads segments asynchronously
4. Merges segments using FFmpeg
5. Returns download status via polling

**Security Design**: 
- URL validation prevents access to private/internal networks
- Checks for private IP ranges, loopback, and reserved addresses
- Hostname resolution to IP validation

**Pros**:
- Centralized processing
- No client-side dependencies
- Server-side FFmpeg access

**Cons**:
- Server resource requirements
- Requires network access to server

## Desktop GUI Architecture (main.py)

### Threading Model
- Main GUI thread runs PySimpleGUI event loop
- Download operations executed in daemon threads to prevent UI blocking
- FFmpeg subprocess calls via `os.system()`

**Hardcoded Configuration**:
- FFmpeg path: `G:/ffmpeg-7.1.1-full_build/bin/ffmpeg.exe`
- Designed for packaging with PyInstaller as standalone .exe

**Pros**:
- No server required
- Simple user interface
- Offline capability

**Cons**:
- Platform-specific (Windows)
- Hardcoded FFmpeg path limits portability
- Blocking UI during downloads (popup-based feedback)

## Video Processing Pipeline

All implementations share a common video processing approach:

1. **M3U8 Parsing**: Extract playlist and segment URLs
2. **Segment Download**: Fetch individual .ts video chunks
3. **Decryption** (if needed): AES decryption for encrypted segments (`aes-decryptor.js`)
4. **Merging**: Use FFmpeg to combine segments into MP4
5. **Output**: Deliver final video file

## Frontend Technology

- **Vue.js 2.6.10**: Used in browser version for reactive UI
- **Vanilla JavaScript**: Service Worker and streaming logic
- **PySimpleGUI**: Desktop GUI framework

# External Dependencies

## Required External Tools

### FFmpeg
- **Purpose**: Video segment merging and transcoding
- **Usage**: Command-line execution for combining .ts files into MP4
- **Configuration**: 
  - Desktop app: Hardcoded path to local FFmpeg binary
  - Server version: Expected in system PATH or configurable location
- **Command pattern**: `ffmpeg -i "playlist.m3u8" -c copy -bsf:a aac_adtstoasc output.mp4`

## Python Dependencies

### Web Server (app.py)
- **Flask**: Web framework for HTTP server and routing
- **m3u8**: M3U8 playlist parsing library
- **requests**: HTTP client for downloading segments
- **werkzeug**: Utilities for secure filename handling

### Desktop Application (main.py)
- **PySimpleGUI**: Cross-platform GUI framework
- **requests**: HTTP operations
- **threading**: Asynchronous download operations

### Packaging
- **PyInstaller**: Bundles Python application into standalone Windows executable

## Browser Libraries

- **StreamSaver.js**: Client-side streaming download library
- **mux.js**: AAC to MP4 muxing in browser
- **Vue.js 2.6.10**: Reactive UI framework

## Third-Party Services

- **No external APIs**: Application is self-contained
- **Network Access**: Requires access to M3U8 video source URLs (user-provided)
- **CORS Considerations**: Browser version includes userscript (`m3u8-downloader.user.js`) for bypassing CORS restrictions via browser extension

## Storage

- **Local File System**: 
  - Flask server: `files/` directory for temporary and output storage
  - Desktop app: User-selected save location
  - Browser: Downloads folder via browser download API

## Security Mechanisms

- **SSRF Protection**: IP address validation in Flask server
- **Input Validation**: URL parsing and sanitization
- **Secure Filenames**: Werkzeug's `secure_filename()` utility