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

print("=" * 65)
print(" 🚀 正在启动 IPTV 本地真实网络精准测速与自动同步程序")
print(f"    当前工作目录: {ROOT_DIR}")
print("=" * 65)
print()

# 1. 环境变量配置：优先注入本地 FFmpeg / FFprobe
local_ffmpeg_path = Path(r"C:\Users\tgr\.local\bin\ffmpeg-master-latest-win64-gpl\bin")
if local_ffmpeg_path.is_dir():
    os.environ["PATH"] = str(local_ffmpeg_path) + os.pathsep + os.environ.get("PATH", "")
    print("✅ [1/5] 已加载本地硬件加速 FFmpeg / FFprobe 支持")
else:
    print("⚠️ [1/5] 未检测到专用 FFmpeg 路径，使用系统默认 PATH")

# 2. 同步云端最新代码与配置
print("📥 [2/5] 正在检查与同步云端最新代码与配置...")
try:
    subprocess.run(["git", "fetch", "origin", "main"], capture_output=True, text=True, check=False)
    subprocess.run(["git", "merge", "origin/main", "--no-edit", "-m", "merge: 同步远程最新提交"], capture_output=True, text=True, check=False)
    print("✅ 云端代码同步完成")
except Exception as e:
    print(f"⚠️ 云端同步提示: {e}，继续执行本地测速...")

# 3. 运行本地测速与画质/码率分析
print()
print("🚀 [3/5] 开始执行全量真实网络测速（优先筛选 1080p / 高码率 / 高下行带宽）...")
print("   (提示：正在对所有频道进行实际流媒体切片采样，请耐心等待几分钟)")
print()

start_time = time.time()
ret = subprocess.run([sys.executable, "-X", "utf8", "main.py"])

if ret.returncode != 0:
    print(f"\n❌ 测速过程中出现异常，退出码: {ret.returncode}")
    input("\n按回车键退出窗口...")
    sys.exit(ret.returncode)

# 4. 重新构建公共发布列表 (public_output)
print()
print("📦 [4/5] 正在重新生成公开分发格式 (public_output)...")
try:
    sys.path.insert(0, str(ROOT_DIR))
    from cloud.publication import build_public_playlists
    status = build_public_playlists(str(ROOT_DIR), media_probe=lambda b: True)
    print(f"✅ 公开分发列表生成成功 (共 {status.get('direct_channel_count', 0)} 个有效频道)")
except Exception as e:
    print(f"⚠️ 生成 public_output 遇到异常: {e}")

# 5. 自动提交并推送到 GitHub 仓库
print()
print("🚀 [5/5] 正在将最新测速播放列表推送到 GitHub 仓库...")
now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

subprocess.run(["git", "add", "-f", "output/", "public_output/", "config/"], check=False)
diff_proc = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=False)

if diff_proc.stdout.strip():
    subprocess.run(["git", "commit", "-m", f"update: 本地真实网络精准测速推送 [{now_str}]"], check=False)
    push_proc = subprocess.run(["git", "push", "origin", "main"], capture_output=True, text=True, check=False)
    if push_proc.returncode == 0:
        print("✅ 成功推送到 GitHub 远程仓库！")
    else:
        print(f"⚠️ 推送到 GitHub 遇到警告:\n{push_proc.stderr}")
else:
    print("ℹ️ 播放列表内容无变化，已保持最新。")

# 6. 刷新 CDN 边缘缓存
print()
print("🔄 正在请求刷新 CDN 边缘缓存...")
for p_url in [
    "https://purge.jsdelivr.net/gh/SimonTGR/IPTV_Worker@main/output/user_result.m3u",
    "https://purge.jsdelivr.net/gh/SimonTGR/IPTV_Worker@main/public_output/live.m3u"
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
print("=" * 65)
print(f" 🎉 全流程处理完成！总耗时: {mins}分{secs}秒")
print(" 📺 你的播放器订阅地址（即刻刷新即可享受最新 1080p 超清源）：")
print("    👉 主力推荐 (直连透传): https://ghproxy.net/https://raw.githubusercontent.com/SimonTGR/IPTV_Worker/main/output/user_result.m3u")
print("    👉 CDN加速源:           https://cdn.jsdmirror.com/gh/SimonTGR/IPTV_Worker@main/output/user_result.m3u")
print("=" * 65)
try:
    if sys.stdin and sys.stdin.isatty():
        input("按回车键退出窗口...")
except Exception:
    pass
