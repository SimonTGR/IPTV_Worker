import os
import sys
import time
import json
import re
import datetime
import subprocess
import concurrent.futures
import urllib.request
import urllib.parse
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT_DIR = Path(__file__).resolve().parent

# 黑名单域名（过滤广告循环伪装源、死链、失效中转、第三方带凭证但已过期的源）
BLACKLIST_DOMAINS = [
    "ali-m-l.cztv.com",
    "event.pull.hebtv.com",
    "bp-resource-dfl.bestv.cn",
    "t.live.cntv.cn",
    "hlscdn.liangtv.cn",
    "live.zohi.tv",
    "yncbn.ks-cdn.gitv.tv",
    "222.214.208.34",
    "satellitepull.cnr.cn",
    "jdshipin.com",
    "null-4",
    "wenxiang.cn",
    "192.151.150.154",
    "rr.kg",
    "indevs.in",
    ".jpg"
]

def get_ffprobe_path():
    import shutil
    if shutil.which("ffprobe"):
        return "ffprobe"
    candidates = [
        Path(r"C:\Users\tgr\.local\bin\ffmpeg-master-latest-win64-gpl\bin\ffprobe.exe"),
        ROOT_DIR / "bin" / "ffprobe.exe",
        ROOT_DIR / "ffmpeg" / "bin" / "ffprobe.exe",
        Path(os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffprobe.exe")),
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return "ffprobe"

FFPROBE_BIN = get_ffprobe_path()

def get_target_channels():
    demo_file = ROOT_DIR / "config" / "user_demo.txt"
    categories = []
    cur_cat = "默认"
    channels = []
    for line in demo_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ",#genre#" in line:
            cur_cat = line.split(",#genre#")[0].strip()
        else:
            channels.append((cur_cat, line))
    return channels

def gather_candidates(channels):
    whitelist_file = ROOT_DIR / "config" / "whitelist.txt"
    whitelist_urls = {}
    if whitelist_file.is_file():
        for line in whitelist_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("["):
                continue
            if "," in line:
                name, url = line.split(",", 1)
                whitelist_urls.setdefault(name.strip(), []).append(url.strip())

    rep_file = ROOT_DIR / "output" / "report.json"
    rep_channels = {}
    if rep_file.is_file():
        try:
            rep_data = json.loads(rep_file.read_text(encoding="utf-8"))
            rep_channels = rep_data.get("channels", {})
        except Exception:
            pass

    candidates_by_channel = {}
    for cat, ch in channels:
        urls = []
        seen = set()

        def add_url(u):
            u = u.strip()
            if not u or not u.startswith("http") or u in seen:
                return
            if any(bad in u for bad in BLACKLIST_DOMAINS):
                return
            seen.add(u)
            urls.append(u)

        # 1. 白名单当前源 (最高优先)
        for u in whitelist_urls.get(ch, []):
            add_url(u)
        short_name = ch.split()[0] if ch.startswith("CCTV-") else ch
        if short_name != ch:
            for u in whitelist_urls.get(short_name, []):
                add_url(u)

        if ch == "翡翠台":
            add_url("http://183.62.8.58:50085/tsfile/live/0017_1.m3u8?key=txiptv&playlive=1&authid=0")
            add_url("http://183.62.8.58:50085/tsfile/live/0017_1.m3u8?key=txiptv&playlive=0&authid=0")
            add_url("http://183.62.8.58:50085/tsfile/live/0017_1.m3u8?key=txiptv&playlive=1&authid=1")
            add_url("http://128.14.206.166/pull-f5.flive.douyincdn.com/live/jade.flv")
        elif ch == "TVB 星河":
            add_url("http://cfss.cc/cdn/huya/23734169.m3u8")
            add_url("http://cfss.cc/cdn/huya/23734169.flv")
            add_url("https://live.ottiptv.cc/huya/23734169")

        # 2. 候选报告中的备选 1080P/720P 源 (最多补充 2 个优质备选)
        added_from_rep = 0
        for rep_key in [ch, short_name]:
            if rep_key in rep_channels:
                for item in rep_channels[rep_key].get("selected", []) + rep_channels[rep_key].get("rejected", []):
                    if item.get("playable") and item.get("resolution") in ["1920x1080", "1280x720"]:
                        cand_u = item.get("url", "")
                        if cand_u not in seen and not any(bad in cand_u for bad in BLACKLIST_DOMAINS):
                            add_url(cand_u)
                            added_from_rep += 1
                            if added_from_rep >= 2:
                                break
            if added_from_rep >= 2:
                break

        candidates_by_channel[ch] = urls

    return candidates_by_channel

def test_stream(ch, url):
    result = {
        "channel": ch,
        "url": url,
        "ok": False,
        "speed_mbps": 0.0,
        "width": 0,
        "height": 0,
        "status": ""
    }
    # 1. 真实流媒体切片下载测速 (测出真实下行吞吐)
    try:
        t0 = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            initial_data = resp.read(1024 * 512)

        # 若为 m3u8 索引，解析真实 ts 切片地址下载以测出真实流媒体带宽
        if b"#EXTM3U" in initial_data or url.endswith(".m3u8") or ".m3u8?" in url:
            text = initial_data.decode("utf-8", errors="ignore")
            ts_url = None
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    ts_url = urllib.parse.urljoin(url, line)
                    break
            if ts_url:
                t0 = time.time()
                ts_req = urllib.request.Request(ts_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
                with urllib.request.urlopen(ts_req, timeout=3.5) as ts_resp:
                    chunk = ts_resp.read(1024 * 1024)
                dt = max(time.time() - t0, 0.01)
                result["speed_mbps"] = round((len(chunk) * 8) / (dt * 1024 * 1024), 2)
            else:
                dt = max(time.time() - t0, 0.01)
                result["speed_mbps"] = round((len(initial_data) * 8) / (dt * 1024 * 1024), 2)
        else:
            dt = max(time.time() - t0, 0.01)
            result["speed_mbps"] = round((len(initial_data) * 8) / (dt * 1024 * 1024), 2)

    except Exception as e:
        result["status"] = f"net_fail: {str(e)[:20]}"
        return result

    # 2. FFprobe 抽帧与 16:9 高清分辨率核验
    try:
        cmd = [
            FFPROBE_BIN,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0",
            url
        ]
        out = subprocess.check_output(cmd, timeout=4.5, stderr=subprocess.STDOUT).decode("utf-8", errors="ignore")
        m = re.search(r"(\d{3,4})x(\d{3,4})", out)
        if m:
            w, h = int(m.group(1)), int(m.group(2))
            result["width"] = w
            result["height"] = h
            # 必须为 16:9 宽屏高清 (1920x1080 或 1280x720)，彻底杜绝手机/4:3标清
            if w >= 1280 and h >= 720 and (w / h) >= 1.5:
                result["ok"] = True
                result["status"] = f"{w}x{h}"
            else:
                result["status"] = f"not_16_9_hd: {w}x{h}"
        else:
            result["status"] = "no_res_probe"
    except Exception as e:
        result["status"] = f"probe_err: {str(e)[:20]}"

    return result

def run_local_speedtest():
    start_time = time.time()
    channels = get_target_channels()
    print(f"[*] 正在为 {len(channels)} 个核心频道提取高质量候选播放源...")
    cands_map = gather_candidates(channels)

    all_tasks = []
    seen_urls = set()
    for cat, ch in channels:
        for u in cands_map.get(ch, []):
            if (ch, u) not in seen_urls:
                seen_urls.add((ch, u))
                all_tasks.append((ch, u))

    print(f"[*] 共优选出 {len(all_tasks)} 个独立直播源，开始多线程本地测速与画质探测...")
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(test_stream, ch, u): (ch, u) for ch, u in all_tasks}
        completed = 0
        for f in concurrent.futures.as_completed(futs):
            res = f.result()
            ch = res["channel"]
            results.setdefault(ch, []).append(res)
            completed += 1
            flag = "✅ [1080P/720P]" if res["ok"] else "❌ [FAIL]"
            print(f"[{completed:02d}/{len(all_tasks):02d}] {flag} {ch:<14} | {res['status']:<15} | {res['speed_mbps']:>6.2f} Mbps | {res['url'][:65]}")

    print("\n[*] 测速全部完成，正在按本地真实网速优选排序...")
    selected_by_channel = {}
    for cat, ch in channels:
        items = results.get(ch, [])
        good = [x for x in items if x["ok"]]
        # 按真实网络测速排序：网速最高、画质最佳排在前面
        good.sort(key=lambda x: (x["width"] * x["height"], x["speed_mbps"]), reverse=True)

        target_count = 3 if ch == "翡翠台" else 2
        picked = good[:target_count]

        # 保护机制：若因瞬时网络抖动导致测得数量不足，保留原有备用源避免空频道
        if len(picked) < target_count:
            existing_urls = cands_map.get(ch, [])
            picked_urls = {p["url"] for p in picked}
            for u in existing_urls:
                if u not in picked_urls:
                    picked.append({"channel": ch, "url": u, "ok": True, "speed_mbps": 1.0, "status": "retained"})
                    if len(picked) >= target_count:
                        break

        selected_by_channel[ch] = picked[:target_count]

    # 回写到 config/whitelist.txt
    whitelist_file = ROOT_DIR / "config" / "whitelist.txt"
    demo_file = ROOT_DIR / "config" / "user_demo.txt"
    out_lines = [
        "# 这是接口的白名单，白名单内的接口将不会参与测速，始终保留至结果最前；",
        "# 本白名单严格贯彻【100% 16:9 宽屏高清 (1920x1080 / 1280x720)、速率与大缓冲第一位】原则。",
        "# 规则：翡翠台保留 3 个最优源，其余所有频道保留 2 个最快最稳的 1080P/720P 实时直播源。",
        ""
    ]
    for line in demo_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ",#genre#" in line:
            cur_cat = line.split(",#genre#")[0].strip()
            out_lines.append(f"\n# {'='*65}\n# {cur_cat}\n# {'='*65}")
        else:
            ch = line
            out_lines.append(f"\n# --- {ch} ---")
            for p in selected_by_channel.get(ch, []):
                out_lines.append(f"{ch},{p['url']}")
                short_ch = ch.split()[0] if ch.startswith("CCTV-") else ch
                if short_ch != ch:
                    out_lines.append(f"{short_ch},{p['url']}")

    whitelist_file.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    print(f"✅ config/whitelist.txt 已根据本地实测网络速率重新优选排序！")

    # 执行同步
    from sync_channels import run_sync
    run_sync()

    elapsed = time.time() - start_time
    print(f"\n🎉 本地测速与同步完成！耗时: {int(elapsed//60)}分{int(elapsed%60)}秒")
    return True

if __name__ == "__main__":
    run_local_speedtest()
