"""
按正确请求头下载抓取器导出的媒体链接。

为什么需要专门的下载器：
  B 站 CDN 有防盗链，**User-Agent 和 Referer 必须同时带**，缺一个就是 403。
  实测：
    只带 UA      -> 403
    只带 Referer -> 403
    两个都带     -> 200/206 成功
  浏览器直接粘贴、普通下载器、curl 默认行为都会缺头，于是"打不开"。

用法：
  python fetch_media.py <链接或文件路径> [更多…] [-o 输出目录]
  python fetch_media.py "*_视频流.txt"        # 批量下清单里的所有链接
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 按域名给 Referer —— 这是成败关键
REFERER_BY_HOST = {
    "bilibili.com": "https://www.bilibili.com/",
    "bilivideo.cn": "https://www.bilibili.com/",
    "hdslb.com": "https://www.bilibili.com/",
    "douyin.com": "https://www.douyin.com/",
    "douyinvod.com": "https://www.douyin.com/",
    "douyinpic.com": "https://www.douyin.com/",
    "byteimg.com": "https://www.douyin.com/",
    "bytednsdoc.com": "https://www.douyin.com/",
}
DEFAULT_REFERER = "https://www.bilibili.com/"


def referer_for(url: str) -> str:
    """决定该带哪个 Referer。

    B 站的 CDN 域名五花八门（bilivideo.cn / hdslb.com /
    xxx.edge.mountaintoys.cn / upos-*.bilivideo.com …），
    硬编码域名表容易漏。所以域名表没命中时，再看 URL 内容特征判断。
    """
    full = url.lower()
    host = (urlparse(url).hostname or "").lower()

    for key, ref in REFERER_BY_HOST.items():
        if host.endswith(key):
            return ref

    # 域名未命中 → 按内容特征判断
    if ("douyinvod" in full or "douyinpic" in full or "byteimg" in full
            or "media-video" in full or "media-audio" in full
            or "aweme" in full):
        return "https://www.douyin.com/"
    if ("upgcxcode" in full or ".m4s" in full or "bilivideo" in full
            or "hdslb" in full):
        return "https://www.bilibili.com/"

    return DEFAULT_REFERER


def safe_name(url: str, fallback: str) -> str:
    """从 URL 里取一个像样的文件名。

    抖音/CDN 的路径常以 / 结尾（media-video-avc1/），取不到文件名，
    这时从路径倒数几段里挑有意义的，再不行才用序号。
    """
    path = urlparse(url).path
    segs = [s for s in path.split("/") if s]
    name = ""
    if segs:
        # 末段有扩展名才直接用
        last = segs[-1]
        if "." in last:
            name = last
        else:
            # 抖音形如 .../media-video-avc1/ 或 .../xxx-1-100023.m4s
            # 往回找带扩展名或能当名字的段
            for s in reversed(segs[-3:]):
                if "." in s or s.startswith(("media-", "upgcxcode")):
                    name = s
                    break
            if not name:
                name = segs[-1]
    name = re.sub(r'[<>:"/\\|?*]', "_", name or fallback)
    # 去掉过长的哈希段，保留可读部分
    if len(name) > 80:
        name = name[:80]
    return name or fallback


def guess_ext(url: str, name: str) -> str:
    """决定扩展名：优先看名字里的后缀，其次看 URL 特征。"""
    lower = name.lower()
    for ext in (".m4s", ".mp4", ".m3u8", ".mpd", ".webm", ".ts",
                ".jpg", ".jpeg", ".png", ".webp", ".aac", ".mp3"):
        if lower.endswith(ext):
            return ""
    host = urlparse(url).hostname or ""
    if "bilivideo" in host or "mountaintoys" in host:
        return ".m4s"
    if "douyinvod" in host or "media-video" in url or "media-audio" in url:
        return ".mp4"
    return ".bin"


def download(url: str, dest: Path, referer: str = "") -> tuple[bool, str]:
    ref = referer or referer_for(url)
    headers = {
        "User-Agent": UA,
        "Referer": ref,
        "Origin": ref.rstrip("/"),
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as r, dest.open("wb") as fh:
            total = 0
            while True:
                chunk = r.read(1024 * 512)
                if not chunk:
                    break
                fh.write(chunk)
                total += len(chunk)
        return True, f"{total / 1024 / 1024:.2f} MB"
    except urllib.error.HTTPError as e:
        hint = ""
        if e.code == 403:
            hint = "（403 = 防盗链，检查 UA/Referer；也可能链接已过期）"
        return False, f"HTTP {e.code} {hint}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def probe_streams(path: Path) -> dict:
    """返回 {'v': {...}|None, 'a': {...}|None}。"""
    out: dict = {"v": None, "a": None}
    for key in ("v", "a"):
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", f"{key}:0",
             "-show_entries", "stream=codec_name,profile,width,height",
             "-of", "default=noprint_wrappers=1", str(path)],
            capture_output=True, text=True, errors="replace",
        )
        if r.stdout.strip():
            info = {}
            for line in r.stdout.strip().splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    info[k] = v
            out[key] = info
    return out


def try_merge(first: Path, second: Path, out_dir: Path) -> Path | None:
    """把两条流拼成一个能播的 mp4。

    注意：抖音/B站有时给的是「纯视频轨 + 纯音频轨」，
    有时给的是「两个各自完整的音视频文件」（不同清晰度）。
    必须先判别，否则会把两个视频流拼在一起丢画质。
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None

    a = probe_streams(first)
    b = probe_streams(second)

    both_av = a["v"] and a["a"] and b["v"] and b["a"]
    if both_av:
        # 两个都是完整文件 —— 不是 DASH 双轨，不该拼。
        # 挑分辨率/码率高的留作成品，另一条丢弃。
        def score(info: dict) -> tuple[int, str]:
            v = info["v"] or {}
            try:
                w = int(v.get("width") or 0)
                h = int(v.get("height") or 0)
            except ValueError:
                w = h = 0
            return (w * h, v.get("codec_name") or "")

        best, worst = (first, second) if score(a) >= score(b) else (second, first)
        best_info = a if best is first else b
        v = best_info["v"] or {}
        print(f"\n两条都是完整音视频文件，保留高分辨率的那条：")
        print(f"  {best.name}  {v.get('width')}x{v.get('height')} "
              f"{v.get('codec_name')}")
        print(f"  （丢弃 {worst.name}）")

        info = best_info
        ap = info["a"]
        vp = best
        vinfo = v
        ainfo = ap or {}
        # 已是完整文件，只需按需转码，不需要再拼
        return transcode_if_needed(vp, vinfo, ainfo, out_dir)

    # 真正的 DASH 双轨
    if a["v"] and b["a"]:
        vp, ap = first, second
        vinfo, ainfo = a["v"], b["a"]
    elif b["v"] and a["a"]:
        vp, ap = second, first
        vinfo, ainfo = b["v"], a["v"]
    else:
        return None

    print(f"\n拼接: 视频 {vp.name} ({vinfo.get('codec_name')} "
          f"{vinfo.get('width')}x{vinfo.get('height')})")
    print(f"      音频 {ap.name} ({ainfo.get('codec_name')} / {ainfo.get('profile')})")

    vcodec = _vcodec_args(vinfo)
    acodec = _acodec_args(ainfo)

    final = _unique_path(out_dir, "merged")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
           "-i", str(vp), "-i", str(ap)] + vcodec + acodec + \
          ["-movflags", "+faststart", str(final)]
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if r.returncode != 0 or not final.exists():
        print(f"      拼接失败: {r.stderr[-200:]}")
        return None

    _report(final)
    return final


def _unique_path(out_dir: Path, stem: str) -> Path:
    final = out_dir / f"{stem}.mp4"
    n = 1
    while final.exists():
        final = out_dir / f"{stem}_{n}.mp4"
        n += 1
    return final


def _vcodec_args(vinfo: dict) -> list[str]:
    name = (vinfo.get("codec_name") or "").lower()
    if name in ("av1", "hevc"):
        print(f"      视频是 {name}，转为 H.264")
        return ["-c:v", "libx264", "-preset", "medium", "-crf", "20",
                "-pix_fmt", "yuv420p"]
    return ["-c:v", "copy"]


def _acodec_args(ainfo: dict) -> list[str]:
    profile = (ainfo.get("profile") or "").upper()
    if "HE" in profile:
        print("      音频是 HE-AAC，转为 AAC-LC")
    # HE-AAC 必须重编；LC 也重编一次确保各播放器都认
    return ["-c:a", "aac", "-b:a", "128k"]


def _report(path: Path) -> None:
    info = probe_streams(path)
    v = info["v"] or {}
    a = info["a"] or {}
    print(f"      结果: {v.get('codec_name')} {v.get('width')}x{v.get('height')}"
          f" + {a.get('codec_name')}/{a.get('profile')}"
          f"  {path.stat().st_size / 1024 / 1024:.2f} MB")


def transcode_if_needed(src: Path, vinfo: dict, ainfo: dict,
                        out_dir: Path) -> Path | None:
    """已是完整文件时：只在编码不兼容时重编码，否则原样保留。"""
    ffmpeg = shutil.which("ffmpeg")
    vname = (vinfo.get("codec_name") or "").lower()
    aprofile = (ainfo.get("profile") or "").upper()
    need_v = vname in ("av1", "hevc")
    need_a = "HE" in aprofile

    if not need_v and not need_a:
        print("      编码已兼容，无需转码")
        _report(src)
        return src

    final = _unique_path(out_dir, src.stem + "_兼容版")
    args: list[str] = []
    args += _vcodec_args(vinfo) if need_v else ["-c:v", "copy"]
    args += ["-c:a", "aac", "-b:a", "128k"]
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
           "-i", str(src)] + args + ["-movflags", "+faststart", str(final)]
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if r.returncode != 0 or not final.exists():
        print(f"      转码失败: {r.stderr[-200:]}")
        return src
    _report(final)
    return final


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="fetch_media",
        description="按正确请求头下载抓取器导出的媒体链接（解决 B 站 403）",
    )
    ap.add_argument("inputs", nargs="+", help="URL、URL 列表文件，或用引号包住的通配符")
    ap.add_argument("-o", "--out", default="", help="输出目录，默认 Downloads/网页抓取结果/下载")
    ap.add_argument("--dry-run", action="store_true", help="只检查可用性，不落盘")
    ap.add_argument("--no-merge", action="store_true",
                    help="下载两条轨后不自动拼接（默认会自动拼）")
    args = ap.parse_args()

    out_dir = Path(args.out) if args.out else (
        Path.home() / "Downloads" / "网页抓取结果" / "下载")
    out_dir.mkdir(parents=True, exist_ok=True)

    # 收集 URL：参数可能是 URL，也可能是含 URL 的文本文件
    urls: list[str] = []
    page_url = ""
    for item in args.inputs:
        p = Path(item)
        if p.is_file():
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                s = line.strip()
                if s.startswith("#"):
                    # 抓取器导出的文件里带 "# 页面: https://..."
                    if "页面:" in s:
                        cand = s.split("页面:", 1)[1].strip()
                        if cand.startswith("http"):
                            page_url = cand
                    continue
                if s.startswith("http"):
                    urls.append(s)
        elif item.startswith("http"):
            urls.append(item)
        else:
            print(f"跳过无法识别: {item}")

    urls = list(dict.fromkeys(urls))
    if not urls:
        print("没有可下载的链接")
        return 1

    # 有页面 URL 就用它做 Referer（最接近"从 B 站页面点开"的合法场景）
    fixed_referer = ""
    if page_url:
        host = (urlparse(page_url).hostname or "").lower()
        if "bilibili" in host:
            fixed_referer = "https://www.bilibili.com/"
        elif "douyin" in host:
            fixed_referer = "https://www.douyin.com/"
        print(f"页面: {page_url}")
        print(f"Referer: {fixed_referer}（取自导出文件的页面记录）\n")

    print(f"共 {len(urls)} 条链接，输出目录: {out_dir}\n")

    ok = 0
    bad = 0
    downloaded: list[Path] = []
    for i, u in enumerate(urls, 1):
        host = urlparse(u).hostname or "?"
        base = safe_name(u, f"file{i}")
        name = base + guess_ext(u, base)
        dest = out_dir / name
        print(f"[{i}/{len(urls)}] {host}")
        print(f"  {u[:100]}…")

        if args.dry_run:
            headers = {"User-Agent": UA, "Referer": fixed_referer or referer_for(u)}
            req = urllib.request.Request(u, headers=headers, method="HEAD")
            try:
                with urllib.request.urlopen(req, timeout=25) as r:
                    print(f"  ✓ 可访问 status={r.status} "
                          f"len={r.headers.get('Content-Length')}")
                    ok += 1
            except urllib.error.HTTPError as e:
                print(f"  ✗ HTTP {e.code}")
                bad += 1
            except Exception as e:  # noqa: BLE001
                print(f"  ✗ {type(e).__name__}")
                bad += 1
            continue

        success, info = download(u, dest, fixed_referer)
        if success:
            print(f"  ✓ {info} -> {dest.name}")
            ok += 1
            downloaded.append(dest)
        else:
            print(f"  ✗ {info}")
            bad += 1
            if dest.exists():
                dest.unlink(missing_ok=True)

    print(f"\n完成: 成功 {ok} / 失败 {bad}")
    if not (ok and not args.dry_run):
        return 0 if bad == 0 else 1

    print(f"位置: {out_dir}")

    # 两条轨就自动拼起来，省得用户手动 ffmpeg
    if len(downloaded) == 2 and not args.no_merge:
        merged = try_merge(downloaded[0], downloaded[1], out_dir)
        if merged:
            print(f"\n已自动拼接: {merged}")
            return 0

    if len(downloaded) == 2:
        print("\n抓到的是 DASH 双轨（视频轨 + 音频轨），手动拼接：")
        print('  ffmpeg -i video.mp4 -i audio.mp4 -c:v copy -c:a aac -b:a 128k out.mp4')
        print("  （音频用 aac 重编，避免 HE-AAC 在部分播放器上无声）")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

