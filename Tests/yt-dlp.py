import yt_dlp
from yt_dlp.networking.impersonate import ImpersonateTarget
from typing import Any

def download_auto_subs(url:str,lang:str="en",output_dir:str="."):

    ydl_opts:dict[str,Any] = {
        "writeautomaticsub": True,   # Download auto-generated subs
        "subtitleslangs": [lang],    # Language code(s)
        "subtitlesformat": "json3",    # Format: vtt, srt, json3
        "skip_download": True,       # Don't download the video
        "outtmpl": f"{output_dir}/%(title)s.%(ext)s",
        "remote_components":["ejs:github"],
        "impersonate":ImpersonateTarget()
    }

    ydl = yt_dlp.YoutubeDL(ydl_opts) #type:ignore

    info = ydl.extract_info(url, download=True)
    print(f"Title: {info['title']}") #type:ignore
    print(f"Subtitles saved to: {output_dir}/")

def download_chat(url:str,output_dir:str="."):

    ydl_opts:dict[str,Any] = {
        "writesubtitles":True,
        "subtitleslangs": ["live_chat"],    # Language code(s)
        "skip_download": True,       # Don't download the video
        "outtmpl": f"{output_dir}/%(title)s.%(ext)s",
        "remote_components":["ejs:github"],
        "impersonate":ImpersonateTarget()
    }

    ydl = yt_dlp.YoutubeDL(ydl_opts) #type:ignore

    info = ydl.extract_info(url, download=True)
    print(f"Title: {info['title']}") #type:ignore
    print(f"Subtitles saved to: {output_dir}/")

# download_auto_subs("https://www.youtube.com/watch?v=OAqSqMozryU", lang="en")
download_chat("https://www.youtube.com/watch?v=OAqSqMozryU")