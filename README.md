# get-m3u8 Python 版

本项目参考 [caiweiming/get-m3u8](https://github.com/caiweiming/get-m3u8) 的交互体验，提供多端统一的 m3u8 下载能力：

- **Web 面板**：访问 `app.py` 启动的 Flask 服务，即可使用与 get-m3u8 类似的任务管理界面，支持范围下载、边下边存、AES-128 解密和强制保存。
- **命令行工具**：`python download.py <url>` 直接从终端创建任务，并实时显示进度、速度、ETA。
- **桌面脚本**：`python main.py` 提供交互式问答流程，适合不熟悉命令行的用户。

## 功能亮点

- 公网地址校验，阻止内网/环回地址。
- 自动解析并下载媒体分片，可选范围下载、手动重试指定分片。
- 支持 AES-128 解密、边下边存或下载完成后一次性写入。
- 可选择 TS 或 MP4 输出（需安装 ffmpeg）。
- 强制保存能力，可随时输出已下载的 TS 文件。

## 快速开始

1. 安装依赖：

   ```bash
   pip install -r requirements.txt
   ```

2. 启动 Web 面板：

   ```bash
   flask --app app run
   ```

3. 创建命令行任务：

   ```bash
   python download.py https://example.com/video.m3u8 my-video --format mp4
   ```

4. 使用桌面模式：

   ```bash
   python main.py
   ```

下载结果默认保存在项目根目录的 `files/` 文件夹中。

## 许可

本项目延续上游 MIT 许可，可自由修改和分发。
