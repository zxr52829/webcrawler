"""验证导出的视频流地址是否真的可下载。"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

out_dir = Path.home() / "Downloads" / "网页抓取结果"
files = sorted(out_dir.glob("*_视频流.txt"), key=lambda p: p.stat().st_mtime)
if not files:
    print("找不到视频流文件")
    raise SystemExit(1)

target = files[-1]
print(f"文件: {target.name}")
urls = [line.strip() for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
print(f"地址数: {len(urls)}\n")

for i, u in enumerate(urls, 1):
    req = urllib.request.Request(
        u,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            "Referer": "https://www.douyin.com/",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            head = r.read(64)
            print(f"[{i}] status={r.status}")
            print(f"    Content-Type   = {r.headers.get('Content-Type')}")
            print(f"    Content-Length = {r.headers.get('Content-Length')}")
            print(f"    前16字节        = {head[:16].hex()}")
    except Exception as e:  # noqa: BLE001
        print(f"[{i}] 失败 {type(e).__name__}: {e}")
