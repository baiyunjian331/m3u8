import os

m3u8_url = input("请输入 M3U8 链接：\n")
output_path = input("请输入保存文件名（如 movie.mp4）：\n")

ffmpeg_cmd = f'ffmpeg -headers "User-Agent: ExoPlayer" -i "{m3u8_url}" -c copy -bsf:a aac_adtstoasc "{output_path}"'

print(f"开始下载：{output_path}")
os.system(ffmpeg_cmd)
print("✅ 下载完成！文件已保存在当前目录。")
