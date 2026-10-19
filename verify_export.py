"""导出逻辑离线验证：不启动 GUI，直接喂假数据给 Api.export，检查文件真的写出来。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# Windows 控制台默认 GBK，打不出 ✓/✗，强制切 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

# 用临时目录当输出目录，避免污染真实 Downloads
tmp = Path(tempfile.mkdtemp(prefix="crawler_test_"))

import app as mod  # noqa: E402

mod.DEFAULT_OUTPUT_DIR = tmp

failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}  {detail}")
        failures.append(label)


print(f"[输出目录] {tmp}\n")

api = mod.Api()

# ---- 无数据导出 ----
print("[0] 空结果导出")
r = api.export()
check("返回 ok=False", r.get("ok") is False, str(r))
check("报错信息可读", "还没有抓取结果" in (r.get("error") or ""), str(r))

META = {"url": "https://example.com/a/b", "title": '带/非法:字符*的标题<>"|?', "domain": "example.com",
        "collectedAt": "2026-01-01T00:00:00Z"}

# ---- 视频 ----
print("\n[1] 视频导出")
api._last_result = {
    "_kind": "video",
    "meta": META,
    "items": [
        {"url": "https://example.com/v1.mp4", "title": "视频一", "poster": "https://example.com/p1.jpg",
         "duration": 93, "width": 1280, "height": 720, "source": "video-tag"},
        {"url": "https://example.com/live.m3u8", "title": "直播流", "poster": "",
         "duration": None, "width": 0, "height": 0, "source": "inline-script"},
    ],
}
r = api.export()
check("导出成功", r.get("ok") is True, str(r))
files = [Path(f) for f in r.get("files", [])]
check("产出 2 个文件", len(files) == 2, str(files))
check("csv 存在", any(f.suffix == ".csv" for f in files), str(files))
check("urls.txt 存在", any(f.name.endswith("_urls.txt") for f in files), str(files))
check("文件名已清洗非法字符", all(not set(f.name) & set('<>:"/\\|?*') for f in files), str(files))

csv_file = next(f for f in files if f.suffix == ".csv")
content = csv_file.read_text(encoding="utf-8-sig")
check("csv 含表头", "序号" in content and "URL" in content)
check("csv 含 m3u8", "live.m3u8" in content)
check("时长 None 不写成 None", "None" not in content, content[:200])

url_file = next(f for f in files if f.name.endswith("_urls.txt"))
url_lines = [l for l in url_file.read_text(encoding="utf-8").splitlines() if l.strip()]
check("urls.txt 两行", len(url_lines) == 2, str(url_lines))

# ---- 图片 ----
print("\n[2] 图片导出")
api._last_result = {
    "_kind": "image",
    "meta": META,
    "items": [
        {"url": "https://example.com/i1.jpg", "alt": "图一", "width": 240, "height": 160,
         "visible": True, "lazy": False, "source": "img-tag"},
        {"url": "https://example.com/bg.png", "alt": "", "width": 0, "height": 0,
         "visible": True, "lazy": True, "source": "css-bg"},
    ],
}
r = api.export()
check("导出成功", r.get("ok") is True, str(r))
files = [Path(f) for f in r.get("files", [])]
csv_file = next(f for f in files if f.suffix == ".csv")
content = csv_file.read_text(encoding="utf-8-sig")
check("csv 含 alt 列", "alt" in content)
check("csv 含懒加载标记为是", "是" in content)
check("csv 含 css-bg 来源", "css-bg" in content)

# ---- 文字 ----
print("\n[3] 文字导出")
api._last_result = {
    "_kind": "text",
    "meta": META,
    "plain": "第一段正文\n第二段正文\n" + "填充" * 50,
    "chars": 208,
    "blocks": [{"tag": "p", "text": "第一段正文"}],
}
r = api.export()
check("导出成功", r.get("ok") is True, str(r))
files = [Path(f) for f in r.get("files", [])]
check("产出 txt", len(files) == 1 and files[0].suffix == ".txt", str(files))
body = files[0].read_text(encoding="utf-8")
check("含标题头", "标题:" in body)
check("含地址", "example.com/a/b" in body)
check("含正文", "第一段正文" in body)
check("中文未乱码", "填充填充" in body)

# ---- 全部 ----
print("\n[4] 全部导出")
api._last_result = {
    "_kind": "all",
    "meta": META,
    "video": [{"url": "https://example.com/v.mp4"}],
    "image": [{"url": "https://example.com/i.jpg"}],
    "text": {"plain": "正文内容在这里", "blocks": [{"tag": "p", "text": "正文内容在这里"}]},
}
r = api.export()
check("导出成功", r.get("ok") is True, str(r))
files = [Path(f) for f in r.get("files", [])]
check("产出 4 个文件", len(files) == 4, str([f.name for f in files]))
check("有 json", any(f.suffix == ".json" for f in files))
check("有正文 txt", any("正文" in f.name for f in files))
check("有视频链接 txt", any("视频" in f.name for f in files))
check("有图片链接 txt", any("图片" in f.name for f in files))
jf = next(f for f in files if f.suffix == ".json")
import json as _json
data = _json.loads(jf.read_text(encoding="utf-8"))
check("json 可解析", isinstance(data, dict))
check("json 保留中文", "正文内容在这里" in jf.read_text(encoding="utf-8"))

# ---- 文件名清洗单元测试 ----
print("\n[5] 文件名清洗")
cases = [
    ('a/b*c?d"e<f>g|h', "不含非法字符"),
    ("  .  ", "不返回空"),
    ("", "空串有兜底"),
    ("x" * 200, "超长被截断"),
]
for raw, label in cases:
    got = mod._safe_name(raw)
    ok = bool(got) and not (set(got) & set('<>:"/\\|?*')) and len(got) <= 60
    check(f"{label}: {raw[:20]!r} -> {got[:30]!r}", ok, got)

# ---- crawl 的 kind 校验 ----
print("\n[6] crawl 参数校验")
r = api.crawl("bogus_kind")
check("未知 kind 直接拒绝", r.get("ok") is False and "未知抓取类型" in (r.get("error") or ""), str(r))

print("\n" + "=" * 52)
if failures:
    print(f"失败 {len(failures)} 项 ✗")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("全部通过 ✓  导出链路可用")
sys.exit(0)

