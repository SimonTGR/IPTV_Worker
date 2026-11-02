import os
import sys
import time
import datetime
import subprocess
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT_DIR = Path(__file__).resolve().parent
os.chdir(ROOT_DIR)

print("=" * 68)
print(" 🚀 IPTV 本地网络真实精准测速与自动云端同步程序")
print(f"    工作目录: {ROOT_DIR}")
print("=" * 68)
print()

# 1. 智能检测与加载本地 FFmpeg / FFprobe
import shutil

ffmpeg_candidates = [
    Path(r"C:\Users\tgr\.local\bin\ffmpeg-master-latest-win64-gpl\bin"),
    ROOT_DIR / "bin",
    ROOT_DIR / "ffmpeg" / "bin",
    ROOT_DIR,
    Path(os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links")),
    Path(os.path.expandvars(r"%USERPROFILE%\.local\bin\ffmpeg-master-latest-win64-gpl\bin")),
]
found_ffmpeg = False
if shutil.which("ffmpeg") and shutil.which("ffprobe"):
    found_ffmpeg = True
    print("✅ [1/5] 已检测到系统全局 FFmpeg / FFprobe 支持")
else:
    for p in ffmpeg_candidates:
        if p.is_dir() and (p / "ffmpeg.exe").is_file() and (p / "ffprobe.exe").is_file():
            os.environ["PATH"] = str(p) + os.pathsep + os.environ.get("PATH", "")
            found_ffmpeg = True
            print(f"✅ [1/5] 已加载本地 FFmpeg 路径: {p}")
            break

if not found_ffmpeg:
    print("⚠️ [1/5] 未检测到 FFmpeg 路径，将使用基础网络测速模式")

# 检查 Python 核心依赖
missing_deps = []
for mod in ["requests", "aiohttp", "tqdm", "pytz", "m3u8", "opencc", "ipdb", "bs4", "pydantic"]:
    try:
        __import__(mod)
    except ImportError:
        missing_deps.append(mod)

if missing_deps:
    print(f"⚠️ 检测到缺少 Python 依赖: {missing_deps}，正在自动为您安装...")
    subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], check=False)

# 2. 同步云端最新代码与配置
print()
print("📥 [2/5] 正在检查与同步云端最新代码与配置...")
try:
    subprocess.run(["git", "fetch", "origin", "main"], capture_output=True, text=True, check=False)
    subprocess.run(["git", "merge", "origin/main", "--no-edit", "-m", "merge: 同步远程最新提交"], capture_output=True, text=True, check=False)
    print("✅ 云端代码同步完成")
except Exception as e:
    print(f"⚠️ 云端同步提示: {e}，继续执行本地测速...")

# 3. 运行模式选择 (支持 5 秒倒计时自动执行模式 1)
print()
print("=" * 68)
print(" 请选择测速与同步模式：")
print("   [1] 核心 44 频道本地极速精准测速 + 画质优选 (推荐，约 50 秒)")
print("   [2] 全量订阅源深度抓取与大范围测速 (耗时较长，约 15~20 分钟)")
print("=" * 68)

mode_choice = "1"
try:
    import msvcrt
    print("👉 请输入选项 [1 或 2] (按 Enter 或等待 5 秒默认执行 [1]): ", end="", flush=True)
    t0 = time.time()
    input_chars = []
    while time.time() - t0 < 5.0:
        if msvcrt.kbhit():
            ch = msvcrt.getwche()
            if ch in ("\r", "\n"):
                break
            input_chars.append(ch)
            break
        time.sleep(0.05)
    print()
    if input_chars:
        mode_choice = input_chars[0].strip() or "1"
except Exception:
    mode_choice = "1"

if mode_choice != "2":
    mode_choice = "1"

print(f"\n🚀 已确认执行模式 [{mode_choice}]...")

start_time = time.time()

if mode_choice == "1":
    print()
    print("🚀 [3/5] 开始执行核心频道本地多线程精准测速 (吞吐量 + 1080P/720P 画质校验)...")
    from local_speedtest import run_local_speedtest
    run_local_speedtest()
else:
    print()
    print("🚀 [3/5] 开始执行全量订阅源抓取与深度测速...")
    ret = subprocess.run([sys.executable, "-X", "utf8", "main.py"])
    if ret.returncode != 0:
        print(f"\n❌ 全量测速异常，退出码: {ret.returncode}")
    else:
        from sync_channels import run_sync
        run_sync()

# 4. 重新构建与格式校验 (由 local_speedtest 或 sync_channels 内部完成)
print()
print("📦 [4/5] 正在重新验证分发格式与更新时间...")
try:
    from sync_channels import run_sync
    run_sync()
    print("✅ 分发列表格式验证完成")
except Exception as e:
    print(f"⚠️ 格式验证遇到异常: {e}")

# 5. 自动提交并推送到 GitHub 仓库
print()
print("🚀 [5/5] 正在将最新测速播放列表推送到 GitHub 仓库...")
now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

subprocess.run(["git", "add", "-A"], check=False)
diff_proc = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=False)

if diff_proc.stdout.strip():
    subprocess.run(["git", "commit", "-m", f"update: 本地真实网络精准测速推送 [{now_str}]"], check=False)
    print("✅ 本地更新已自动提交")
else:
    print("ℹ️ 本地文件已为最新提交状态。")

# 检查是否有待推送提交
unpushed_proc = subprocess.run(["git", "cherry", "-v"], capture_output=True, text=True, check=False)
has_unpushed = bool(unpushed_proc.stdout.strip())

if has_unpushed:
    print("📤 检测到存在待推送的更新，正在推送到 GitHub 远程仓库...")
    push_success = False
    for attempt in range(1, 4):
        push_proc = subprocess.run(["git", "push", "origin", "main"], capture_output=True, text=True, check=False)
        if push_proc.returncode == 0:
            push_success = True
            print("✅ 成功推送到 GitHub 远程仓库！")
            break
        else:
            err_info = push_proc.stderr.strip() or push_proc.stdout.strip()
            print(f"⚠️ 第 {attempt}/3 次推送遇到提示: {err_info}")
            if attempt < 3:
                time.sleep(2)
    if not push_success:
        print("❌ 3 次推送均未能连接到 GitHub，可能受到国内网络环境影响。请检查网络代理或稍后再试。")
else:
    print("✅ 远程仓库已是最新状态，无需重复推送。")

# 6. 刷新 CDN 边缘缓存
print()
print("🔄 正在请求刷新 CDN 边缘缓存...")
for p_url in [
    "https://purge.jsdelivr.net/gh/SimonTGR/IPTV_Worker@main/public_output/live.m3u",
    "https://purge.jsdelivr.net/gh/SimonTGR/IPTV_Worker@main/public_output/live.txt",
    "https://purge.jsdelivr.net/gh/SimonTGR/IPTV_Worker@main/public_output/full.m3u",
    "https://purge.jsdelivr.net/gh/SimonTGR/IPTV_Worker@main/output/user_result.m3u",
    "https://purge.jsdelivr.net/gh/SimonTGR/IPTV_Worker@main/output/user_result.txt",
    "https://purge.jsdelivr.net/gh/SimonTGR/IPTV_Worker@main/public_output/status.json",
]:
    try:
        req = urllib.request.Request(p_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            pass
    except Exception:
        pass
print("✅ CDN 缓存已发送刷新请求！")

elapsed = time.time() - start_time
mins = int(elapsed // 60)
secs = int(elapsed % 60)

print()
print("=" * 68)
print(f" 🎉 全流程处理完成！总耗时: {mins}分{secs}秒")
print(f" 🕘 当前列表更新时间: {now_str}")
print(" 📺 播放器订阅地址（在播放器点击刷新按钮即可加载）：")
print("    👉 GitHub 直连源: https://raw.githubusercontent.com/SimonTGR/IPTV_Worker/main/public_output/live.m3u")
print("    👉 CDN 镜像加速:  https://cdn.jsdelivr.net/gh/SimonTGR/IPTV_Worker@main/public_output/live.m3u")
print("=" * 68)
try:
    if sys.stdin and sys.stdin.isatty():
        input("\n按回车键退出窗口...")
except Exception:
    pass
