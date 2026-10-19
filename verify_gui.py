"""
GUI 端到端自动测试（最终版，时序已验证）。

时序要点（改这个文件前必读）：
  * webview.start(func=boot) 里的 boot() 运行在 UI 线程、GUI 循环已启动、
    但**窗口还没建**。此时 sleep 是安全的（没有已存在的消息泵可堵）。
  * 窗口创建之后，任何 while/sleep 轮询都会堵死 WebView2 消息泵，
    导致页面永远加载不完 → 必须纯事件驱动。
  * 给新窗口挂 loaded 回调的时机：创建后立刻挂，能收到。
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

import webview

import app as mod

HERE = Path(__file__).resolve().parent
TEST_URL = (HERE / "testpage.html").as_uri()
RESULT_FILE = HERE / "_gui_test_result.json"

failures: list[str] = []
report: dict = {}


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label + ("" if cond else f"  {detail}"), flush=True)
    if not cond:
        failures.append(label)


def log(msg: str) -> None:
    print(msg, flush=True)


def run_asserts(api: "mod.Api") -> None:
    st = api.browser_status()
    check("浏览器窗口已打开", st.get("ok") is True, str(st))
    log(f"    当前地址: {str(st.get('url'))[:90]}")

    title = ""
    try:
        title = api._browser.evaluate_js("document.title") or ""
    except Exception as exc:  # noqa: BLE001
        check("evaluate_js 可用", False, str(exc))
    if title:
        check("evaluate_js 可用且中文正常", "测试页面" in title, title[:80])

    log("\n[1] 抓视频")
    r = api.crawl("video")
    check("crawl ok", r.get("ok") is True, str(r)[:300])
    if r.get("ok"):
        urls = [x.get("url", "") for x in (r.get("preview") or [])]
        log(f"    抓到 {r.get('count')} 条")
        for u in urls[:8]:
            log(f"      - {u[:95]}")
        check("有视频条目", (r.get("count") or 0) > 0)
        check("含 m3u8 直链", any("m3u8" in u for u in urls), str(urls)[:300])
        check("含 mp4 直链", any(".mp4" in u for u in urls))
    report["video"] = r

    log("\n[2] 抓图片")
    r = api.crawl("image")
    check("crawl ok", r.get("ok") is True, str(r)[:300])
    if r.get("ok"):
        urls = [x.get("url", "") for x in (r.get("preview") or [])]
        log(f"    抓到 {r.get('count')} 条")
        for u in urls[:8]:
            log(f"      - {u[:95]}")
        check("有图片条目", (r.get("count") or 0) > 0)
        check("含 CSS 背景图", any("bgcase" in u for u in urls), str(urls)[:300])
        check("含懒加载图", any("lazy" in u for u in urls), str(urls)[:300])
        check("含 srcset 最大图", any("rs1/640" in u for u in urls), str(urls)[:300])
    report["image"] = r

    log("\n[3] 抓文字")
    r = api.crawl("text")
    check("crawl ok", r.get("ok") is True, str(r)[:300])
    if r.get("ok"):
        head = r.get("textHead") or ""
        log(f"    字符数: {r.get('textChars')}")
        log(f"    预览: {head[:90]}")
        check("有正文字符", (r.get("textChars") or 0) > 100)
        check("正文含关键词", "正文提取" in head or "测试段落" in head, head[:150])
    report["text"] = r

    log("\n[4] 抓全部")
    r = api.crawl("all")
    check("crawl ok", r.get("ok") is True, str(r)[:300])
    if r.get("ok"):
        vc, ic = r.get("videoCount") or 0, r.get("imageCount") or 0
        log(f"    视频 {vc} / 图片 {ic} / 文字块 {(r.get('count') or 0) - vc - ic}")
        check("三类都非空", vc > 0 and ic > 0)
    report["all"] = r

    log("\n[5] 导出")
    e = api.export()
    check("导出 ok", e.get("ok") is True, str(e)[:300])
    if e.get("ok"):
        files = e.get("files") or []
        log(f"    写出 {len(files)} 个文件到 {e.get('dir')}")
        for f in files:
            log(f"      - {Path(f).name}")
        check("文件确实存在", all(Path(f).exists() for f in files))
    report["export"] = e


def main() -> int:
    api = mod.Api()
    state = {"done": False}

    def finish() -> None:
        report["failures"] = failures
        try:
            RESULT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            log(f"\n报告已写入 {RESULT_FILE}")
        except Exception:  # noqa: BLE001
            traceback.print_exc()

        log("\n" + "=" * 52)
        if failures:
            log(f"失败 {len(failures)} 项")
            for f in failures:
                log("  - " + f)
        else:
            log("GUI 端到端全部通过")
        log("关闭窗口")
        sys.stdout.flush()

        for w in (api._browser, api._console):
            try:
                if w is not None:
                    w.destroy()
            except Exception:  # noqa: BLE001
                pass

    def boot() -> None:
        # 此处 sleep 安全：窗口尚未创建，没有消息泵会被堵。
        try:
            time.sleep(1.5)
            log("[0] 创建浏览器窗口 → 测试页")
            r = api.ensure_browser(TEST_URL)
            check("ensure_browser ok", r.get("ok") is True, str(r))
            if api._browser is None:
                failures.append("浏览器窗口未创建")
                finish()
                return

            def on_loaded() -> None:
                if state["done"]:
                    return
                try:
                    url = api._browser.get_current_url() or ""
                except Exception:  # noqa: BLE001
                    url = ""
                log(f"[0b] loaded: {url[:80]}")
                if "testpage.html" not in url:
                    return
                state["done"] = True
                try:
                    run_asserts(api)
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
                    failures.append("断言抛异常")
                finally:
                    finish()

            api._browser.events.loaded += on_loaded
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            failures.append("boot 抛异常")
            finish()

    console = webview.create_window(
        "控制台(测试)", url=(HERE / "console.html").resolve().as_uri(),
        js_api=api, width=820, height=620,
    )
    api.bind_windows(console)

    webview.start(func=boot, gui="edgechromium", private_mode=False)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())

