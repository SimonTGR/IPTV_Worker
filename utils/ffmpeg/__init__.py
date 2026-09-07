import os
import shutil

# Ensure local user ffmpeg path is on PATH if not already found
if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
    for cand in [
        os.path.expanduser(r"~\.local\bin\ffmpeg-master-latest-win64-gpl\bin"),
        r"C:\Users\tgr\.local\bin\ffmpeg-master-latest-win64-gpl\bin",
    ]:
        if os.path.isdir(cand):
            os.environ["PATH"] = cand + os.pathsep + os.environ.get("PATH", "")
            break

from .ffmpeg import ffmpeg_url, check_ffmpeg_installed_status
from .probe import probe_url, get_resolution_ffprobe, probe_url_sync

__all__ = [
    "ffmpeg_url",
    "get_resolution_ffprobe",
    "probe_url_sync",
    "check_ffmpeg_installed_status",
    "probe_url",
]
