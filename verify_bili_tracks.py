"""B 站 DASH 轨道解析与选择逻辑的单元验证。"""
from __future__ import annotations

import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

import app as mod

failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label + ("" if cond else f"  {detail}"))
    if not cond:
        failures.append(label)


BASE = "https://x.mcdn.bilivideo.cn:8082/v1/resource/upgcxcode/65/64/42806806465/"
URLS = {
    "1080p": BASE + "42806806465-1-100023.m4s?e=abc",
    "720p":  BASE + "42806806465-1-100022.m4s?e=abc",
    "480p":  BASE + "42806806465-1-100024.m4s?e=abc",
    "a64k":  BASE + "42806806465-1-30216.m4s?e=abc",
    "a192k": BASE + "42806806465-1-30280.m4s?e=abc",
    "weird": BASE + "42806806465-1-web.m4s?e=abc",
}

print("[1] 轨道解析")
for name, u in URLS.items():
    info = mod.bili_track_info(u)
    print(f"    {name:6} -> {info}")

check("1080p 识别为视频", mod.bili_track_info(URLS["1080p"])["kind"] == "video")
check("100023 排序值 1080", mod.bili_track_info(URLS["1080p"])["rank"] == 1080)
check("100022 排序值 720", mod.bili_track_info(URLS["720p"])["rank"] == 720)
check("192k 识别为音频", mod.bili_track_info(URLS["a192k"])["kind"] == "audio")
check("30280 排序值 192", mod.bili_track_info(URLS["a192k"])["rank"] == 192)
check("web 伪轨道解析为 None", mod.bili_track_info(URLS["weird"]) is None)
check("非 m4s 解析为 None", mod.bili_track_info("https://a.com/x.mp4") is None)
# label 不应宣称具体分辨率（实测 100023 可能是 AV1 480p）
check("label 不含分辨率断言",
      "1080" not in mod.bili_track_info(URLS["1080p"])["label"],
      mod.bili_track_info(URLS["1080p"])["label"])

print("\n[2] _is_real_stream")
check("1080p 是真流", mod._is_real_stream(URLS["1080p"]) is True)
check("web 伪轨道不是", mod._is_real_stream(URLS["weird"]) is False)
check("抖音客户端推广不是",
      mod._is_real_stream("https://lf3-static.bytednsdoc.com/x/download/douyin_pc_client.mp4") is False)
check("抖音真实流是",
      mod._is_real_stream("https://v11.douyinvod.com/x/video/tos/cn/y/?mime_type=video_mp4") is True)
check("blob 不是", mod._is_real_stream("blob:https://www.bilibili.com/abc") is False)

print("\n[3] select_streams 选最佳")
urls = list(URLS.values()) + [
    "https://v11.douyinvod.com/x/video/tos/cn/y/?mime_type=video_mp4",
    "https://lf3-static.bytednsdoc.com/x/download/douyin_pc_client.mp4",
]
picked = mod.select_streams(urls)
v = picked["video"]
a = picked["audio"]
print(f"    video: {v['label'] if v else None}")
print(f"    audio: {a['label'] if a else None}")
print(f"    others: {len(picked['others'])}")

check("选到视频轨", v is not None)
check("选中 1080P 而非 720P/480P", v and v["codecid"] == "100023", str(v))
check("选到音频轨", a is not None)
check("选中 192K 而非 64K", a and a["codecid"] == "30280", str(a))
check("抖音完整流进 others", any("douyinvod" in u for u in picked["others"]))
check("推广视频被排除",
      not any("douyin_pc_client" in u for u in picked["others"]),
      str(picked["others"]))

print("\n[4] 只有音频的场景")
p2 = mod.select_streams([URLS["a64k"]])
check("无视频轨时 video=None", p2["video"] is None)
check("音频仍有", p2["audio"] is not None)

print("\n[5] 空输入")
p3 = mod.select_streams([])
check("空输入不崩", p3["video"] is None and p3["audio"] is None and p3["others"] == [])

print("\n" + "=" * 52)
if failures:
    print(f"失败 {len(failures)} 项")
    for f in failures:
        print("  - " + f)
    sys.exit(1)
print("轨道逻辑全部通过")
