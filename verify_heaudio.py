"""
端到端验证：从用户给的两条抖音流下载、拼接、确认有声音。

这个视频的音轨是 HE-AACv2，用 -c copy 拼出来播放器没声。
验证脚本必须证明：最终文件的音频是 AAC-LC 且音量正常。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

FAILS: list[str] = []
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Referer": "https://www.douyin.com/"}

VIDEO = (
    "https://v11-weba.douyinvod.com/6b81ba556c396db15fadec0e5cd62d6e/6ad71907/video/tos/cn/"
    "tos-cn-vd-0026c000-ce/oY5IZPMAPia9BBi5IlAlAgYP0hnAXbPNDADms/media-video-avc1/"
    "?a=6383&ch=0&cr=8&dr=0&er=1&lr=default&cd=0%7C0%7C0%7C3&cv=1&br=542&bt=542&cs=0&ds=3"
    "&mime_type=video_mp4&qs=1"
    "&rc=NGc5ZjppNzpnPDc4NDs3NEBpajR5c3c5cjp2ZTMzbGkzNEBiNDMvXi80NjMxNTFhMi9hYSNmZjZhMmQ0ZS5hLS1kLWJzcw%3D%3D"
    "&btag=c0000e00028000&cquery=100w_100o&dy_q=1792394961&l=2026101915292159948E26DCAFEDF6CDF0"
)
AUDIO = (
    "https://v11-weba.douyinvod.com/6e5e1e3a417144313aebdca09e31f2ab/6ad71907/video/tos/cn/"
    "tos-cn-ve-15c000-ce/oo2sDMPRalBlP5XxAAPAhDpuPnbIiPAIBA9ig/media-audio-und-mp4a/"
    "?a=6383&ch=0&cr=8&dr=0&er=1&lr=default&cd=0%7C0%7C0%7C3&cv=1&br=53&bt=53&cs=4&ds=3"
    "&mime_type=video_mp4&qs=0"
    "&rc=aGlmNmdoNjZmaGc5Zjs8NkBpajR5c3c5cjp2ZTMzbGkzNEAwL2IyXjBjLjReNl9iXzYwYSNmZjZhMmQ0ZS5hLS1kLWJzcw%3D%3D"
    "&btag=c0000e00028000&cquery=100w_100o&dy_q=1792394961&l=2026101915292159948E26DCAFEDF6CDF0"
)

OUT = Path.home() / "Downloads" / "网页抓取结果" / "抖音_羊肉面音轨测试"


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label + ("" if cond else f"  {detail}"), flush=True)
    if not cond:
        FAILS.append(label)


def log(m: str) -> None:
    print(m, flush=True)


def fetch(url: str, dest: Path) -> int:
    req = urllib.request.Request(url, headers=HEADERS)
    n = 0
    with urllib.request.urlopen(req, timeout=120) as r, dest.open("wb") as fh:
        while True:
            c = r.read(524288)
            if not c:
                break
            fh.write(c)
            n += len(c)
    return n


def ffprobe_field(path: Path, stream: str, field: str) -> str:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", stream,
         "-show_entries", f"stream={field}", "-of",
         "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, errors="replace",
    )
    return r.stdout.strip()


def volume(path: Path) -> tuple[str, str]:
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-af", "volumedetect",
         "-f", "null", "-"],
        capture_output=True, text=True, errors="replace",
    )
    mean = maxv = ""
    for line in r.stderr.splitlines():
        if "mean_volume" in line:
            mean = line.split("mean_volume:")[-1].strip()
        if "max_volume" in line:
            maxv = line.split("max_volume:")[-1].strip()
    return mean, maxv


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    vp = OUT / "v.mp4"
    ap = OUT / "a.mp4"

    log("[1] 下载两条流")
    log(f"  视频 {fetch(VIDEO, vp) / 1024 / 1024:.2f} MB")
    log(f"  音频 {fetch(AUDIO, ap) / 1024 / 1024:.2f} MB")

    aprofile = ffprobe_field(ap, "a:0", "profile")
    log(f"\n[2] 原始音频 profile: {aprofile}")
    check("确认是 HE-AAC（复现问题前提）", "HE" in aprofile.upper(), aprofile)

    mean0, max0 = volume(ap)
    log(f"    原始音量: mean={mean0} max={max0}")
    check("原始音频确实有声音", mean0 and float(mean0.split()[0]) > -50, mean0)

    # ---- 走工具逻辑：HE 就转 LC ----
    log("\n[3] 按工具逻辑拼接")
    final = OUT / "最终输出.mp4"
    if "HE" in aprofile.upper():
        log("    HE-AAC → 转 AAC-LC")
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-i", str(vp), "-i", str(ap),
               "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
               "-movflags", "+faststart", str(final)]
    else:
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-i", str(vp), "-i", str(ap), "-c", "copy",
               "-movflags", "+faststart", str(final)]

    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    check("拼接成功", r.returncode == 0 and final.exists(), r.stderr[-200:])

    if not final.exists():
        log("\n失败，无法继续")
        return 1

    # ---- 验证输出 ----
    log("\n[4] 验证输出文件")
    vcodec = ffprobe_field(final, "v:0", "codec_name")
    acodec = ffprobe_field(final, "a:0", "codec_name")
    aprofile2 = ffprobe_field(final, "a:0", "profile")
    dur = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(final)],
        capture_output=True, text=True,
    ).stdout.strip()

    log(f"    视频: {vcodec}")
    log(f"    音频: {acodec} / {aprofile2}")
    log(f"    时长: {dur}s")
    log(f"    大小: {final.stat().st_size / 1024 / 1024:.2f} MB")

    check("视频是 h264", vcodec == "h264", vcodec)
    check("音频是 aac", acodec == "aac", acodec)
    check("音频是 LC 而非 HE", "LC" in aprofile2.upper() and "HE" not in aprofile2.upper(),
          aprofile2)
    check("时长接近 183 秒", dur and abs(float(dur) - 182.8) < 3, dur)

    mean1, max1 = volume(final)
    log(f"    输出音量: mean={mean1} max={max1}")
    check("输出有声音（mean > -50dB）",
          bool(mean1) and float(mean1.split()[0]) > -50, mean1)
    check("音量与原始一致（差值 < 1dB）",
          bool(mean1) and bool(mean0)
          and abs(float(mean1.split()[0]) - float(mean0.split()[0])) < 1.0,
          f"{mean0} -> {mean1}")

    # 清理中间
    for p in (vp, ap):
        p.unlink(missing_ok=True)

    log("\n" + "=" * 54)
    if FAILS:
        log(f"失败 {len(FAILS)} 项")
        for f in FAILS:
            log("  - " + f)
        return 1
    log(f"全部通过 —— 有声音的成品: {final}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
