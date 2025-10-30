import os
import PySimpleGUI as sg
import requests
import threading

FFMPEG_PATH = "G:/ffmpeg-7.1.1-full_build/bin/ffmpeg.exe"  # 用户提供路径

def download_m3u8(m3u8_url, output_path):
    if not m3u8_url.strip():
        sg.popup_error("请输入 M3U8 链接")
        return
    if not output_path.strip():
        sg.popup_error("请选择保存位置")
        return

    sg.popup("开始下载... 请稍等")
    # 调用 FFmpeg 下载命令
    cmd = f'"{FFMPEG_PATH}" -headers "User-Agent: ExoPlayer" -i "{m3u8_url}" -c copy -bsf:a aac_adtstoasc "{output_path}"'
    os.system(cmd)
    sg.popup("下载完成！")

def main():
    sg.theme("DarkBlue")
    layout = [
        [sg.Text("M3U8链接", size=(10, 1)), sg.InputText(key="-URL-")],
        [sg.Text("保存为", size=(10, 1)), sg.InputText(key="-OUT-"), sg.FileSaveAs(file_types=(("MP4 Video", "*.mp4"),))],
        [sg.Button("开始下载"), sg.Button("退出")]
    ]
    window = sg.Window("M3U8 视频下载器", layout)

    while True:
        event, values = window.read()
        if event == sg.WIN_CLOSED or event == "退出":
            break
        elif event == "开始下载":
            m3u8_url = values["-URL-"]
            output_path = values["-OUT-"]
            threading.Thread(target=download_m3u8, args=(m3u8_url, output_path), daemon=True).start()

    window.close()

if __name__ == '__main__':
    main()