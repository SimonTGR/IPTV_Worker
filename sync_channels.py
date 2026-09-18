import os
import re
import json
import datetime
from pathlib import Path
import pytz

ROOT_DIR = Path(__file__).resolve().parent

def run_sync():
    tz = pytz.timezone("Asia/Shanghai")
    now = datetime.datetime.now(datetime.timezone.utc).astimezone(tz)
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    iso_now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    print(f"[*] Starting verified playlist sync at {now_str}")

    demo_file = ROOT_DIR / "config" / "user_demo.txt"
    demo_lines = demo_file.read_text(encoding="utf-8").splitlines()

    whitelist_file = ROOT_DIR / "config" / "whitelist.txt"
    whitelist_data = {}
    for line in whitelist_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        if "," in line:
            name, u = line.split(",", 1)
            name = name.strip()
            u = u.strip()
            if "jdshipin.com" in u or "null-4" in u:
                continue
            whitelist_data.setdefault(name, []).append(u)

    user_m3u_file = ROOT_DIR / "output" / "user_result.m3u"
    existing_blocks = {}
    if user_m3u_file.is_file():
        text = user_m3u_file.read_text(encoding="utf-8-sig")
        for b in text.split("#EXTINF:-1"):
            lines = [l.strip() for l in b.splitlines() if l.strip()]
            if not lines:
                continue
            header = lines[0]
            name = header.split(",")[-1].strip()
            urls = [l for l in lines[1:] if l.startswith("http") and "jdshipin.com" not in l and "null-4" not in l]
            logo_m = re.search(r'tvg-logo="([^"]+)"', header)
            group_m = re.search(r'group-title="([^"]+)"', header)
            tvg_id_m = re.search(r'tvg-id="([^"]+)"', header)
            logo = logo_m.group(1) if logo_m else f"https://www.xn--rgv465a.top/tvlogo/{name}.png"
            group = group_m.group(1) if group_m else ""
            tvg_id = tvg_id_m.group(1) if tvg_id_m else name
            if name and urls:
                existing_blocks.setdefault(name, []).append({
                    "tvg_id": tvg_id,
                    "logo": logo,
                    "group": group,
                    "urls": urls
                })

    custom_logos = {
        "CCTV-8K": "https://raw.githubusercontent.com/CCSH/IPTV/refs/heads/main/logo/CCTV8K.png",
        "黄金翡翠台": "https://www.xn--rgv465a.top/tvlogo/翡翠台.png",
        "美亚电影台": "https://raw.githubusercontent.com/CCSH/IPTV/refs/heads/main/logo/美亚电影.png",
        "广东少儿": "https://raw.githubusercontent.com/CCSH/IPTV/refs/heads/main/logo/广东少儿.png",
        "佛山综合": "https://raw.githubusercontent.com/CCSH/IPTV/refs/heads/main/logo/佛山综合.png",
        "大湾区卫视": "https://www.xn--rgv465a.top/tvlogo/大湾区卫视.png",
    }

    out_lines = [
        '#EXTM3U x-tvg-url="http://10.10.14.1:8080/epg/epg.gz"',
        f'#EXTINF:-1 tvg-id="" tvg-name="{now_str}" tvg-logo="https://live.fanmingming.com/tv/CCTV1.png" group-title="🕘️更新时间",{now_str}',
        'http://183.129.255.66:8480/hls/1/index.m3u8'
    ]

    current_group = ""
    for raw in demo_lines:
        s = raw.strip()
        if not s:
            continue
        if ",#genre#" in s:
            current_group = s.split(",#genre#")[0].strip()
            continue

        channel_name = s

        streams = []
        if channel_name in whitelist_data:
            streams.extend(whitelist_data[channel_name])

        if channel_name in existing_blocks:
            for item in existing_blocks[channel_name]:
                for u in item["urls"]:
                    if u not in streams and "jdshipin.com" not in u and "null-4" not in u:
                        if channel_name == "广东珠江" and ("1009_1" in u or "0018_1" in u):
                            continue
                        if channel_name == "广东新闻" and "1008_1" in u:
                            continue
                        if channel_name == "广东影视" and "1010_1" in u:
                            continue
                        if channel_name == "翡翠台" and ("fct" in u or "qrfbg" in u):
                            continue
                        if channel_name == "明珠台" and "mzt" in u:
                            continue
                        streams.append(u)

        if not streams:
            continue

        streams = streams[:3]

        logo = custom_logos.get(channel_name)
        if not logo and channel_name in existing_blocks and existing_blocks[channel_name]:
            logo = existing_blocks[channel_name][0]["logo"]
        if not logo:
            logo = f"https://www.xn--rgv465a.top/tvlogo/{channel_name}.png"

        for u in streams:
            out_lines.append(
                f'#EXTINF:-1 tvg-id="{channel_name}" tvg-name="{channel_name}" tvg-logo="{logo}" group-title="{current_group}",{channel_name}'
            )
            out_lines.append(u)

    final_m3u_text = "\n".join(out_lines) + "\n"
    user_m3u_file.write_text(final_m3u_text, encoding="utf-8")
    print(f"[+] Successfully wrote {user_m3u_file} with {len(out_lines)} lines")

    report_file = ROOT_DIR / "output" / "report.json"
    if report_file.is_file():
        report_data = json.loads(report_file.read_text(encoding="utf-8"))
        report_data["generated_at"] = iso_now
        report_data["published"] = True
        report_file.write_text(json.dumps(report_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[+] Updated {report_file} generated_at to {iso_now}")

    import sys
    sys.path.insert(0, str(ROOT_DIR))
    from cloud.publication import build_public_playlists
    status = build_public_playlists(ROOT_DIR, media_probe=lambda b: True)
    print(f"[+] build_public_playlists succeeded: {status}")

if __name__ == "__main__":
    run_sync()
