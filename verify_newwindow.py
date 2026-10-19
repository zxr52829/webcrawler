"""
验证：网页内 target="_blank" / window.open 的链接不再跳到系统浏览器。

做法：
  1. 起一个本地页面，里面有 target="_blank" 链接和一个 window.open 按钮
  2. 装一个钩子记录 webbrowser.open 是否被调用（正常情况绝不该被调用）
  3. 在 WebView2 里点击/触发，检查浏览窗口 URL 是否在自己窗口内变化
"""
from __future__ import annotations

import sys
import time
import traceback
import webbrowser
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

import webview

import app as mod

HERE = Path(__file__).resolve().parent
failures: list[str] = []
external_calls: list[str] = []

# ---- 拦截 webbrowser.open，记录是否被调用 ----
_real_open = webbrowser.open


def _spy_open(url, *a, **kw):
    external_calls.append(str(url))
    return True


webbrowser.open = _spy_open


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label + ("" if cond else f"  {detail}"), flush=True)
    if not cond:
        failures.append(label)


def log(m: str) -> None:
    print(m, flush=True)


# 本地测试页：含 target=_blank 链接与 window.open
PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>_blank 测试</title></head>
<body style="font-family:sans-serif;padding:20px">
<h1>新窗口行为测试</h1>
<p><a id="blankLink" href="target_page.html" target="_blank">target=_blank 链接</a></p>
<button id="btnOpen" onclick="window.open('target_page.html','_blank')">window.open 按钮</button>
</body></html>"""

TARGET = "<!DOCTYPE html><html><head><meta charset='utf-8'><title>目标页</title></head><body><h1>目标页面已打开</h1></body></html>"


def main() -> int:
    work = HERE / "_blanktest"
    work.mkdir(exist_ok=True)
    (work / "src.html").write_text(PAGE, encoding="utf-8")
    (work / "target_page.html").write_text(TARGET, encoding="utf-8")
    src_uri = (work / "src.html").as_uri()

    # 应用真实配置
    webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = False
    log(f"OPEN_EXTERNAL_LINKS_IN_BROWSER = {webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER']}")

    api = mod.Api()
    state = {"done": False, "stage": 0}

    def finish() -> None:
        log("\n" + "=" * 52)
        if failures:
            log(f"失败 {len(failures)} 项")
            for f in failures:
                log("  - " + f)
        else:
            log("新窗口行为验证全部通过")
        log(f"外部浏览器被调用次数: {len(external_calls)} {external_calls[:3]}")
        sys.stdout.flush()
        for w in (api._browser, api._console):
            try:
                if w is not None:
                    w.destroy()
            except Exception:  # noqa: BLE001
                pass

    def boot() -> None:
        try:
            time.sleep(1.5)
            log("[0] 创建浏览器窗口")
            api.ensure_browser(src_uri)
            if api._browser is None:
                failures.append("浏览器窗口未创建")
                finish()
                return

            def on_loaded() -> None:
                if state["done"]:
                    return
                try:
                    cur = api._browser.get_current_url() or ""
                except Exception:  # noqa: BLE001
                    cur = ""
                log(f"    loaded: {cur[:80]}")

                if state["stage"] == 0:
                    if "src.html" not in cur:
                        return
                    state["stage"] = 1
                    # 点击 target=_blank 链接
                    log("[1] 点击 target=_blank 链接")
                    try:
                        api._browser.evaluate_js(
                            "document.getElementById('blankLink').click();"
                        )
                    except Exception as exc:  # noqa: BLE001
                        check("点击链接", False, str(exc))
                        finish()
                    return

                if state["stage"] == 1:
                    if "target_page.html" in cur:
                        state["stage"] = 2
                        check("新窗口请求留在本窗口内（URL 已变为目标页）",
                              "target_page.html" in cur, cur[:100])
                        check("未调用系统浏览器", len(external_calls) == 0,
                              str(external_calls[:3]))
                        # 再测 window.open
                        log("[2] 回到源页，测试 window.open")
                        api._browser.load_url(src_uri)
                    return

                if state["stage"] == 2 and "src.html" in cur:
                    state["stage"] = 3
                    try:
                        api._browser.evaluate_js(
                            "document.getElementById('btnOpen').click();"
                        )
                    except Exception as exc:  # noqa: BLE001
                        check("执行 window.open", False, str(exc))
                    return

                if state["stage"] == 3 and "target_page.html" in cur:
                    state["done"] = True
                    check("window.open 也留在本窗口", "target_page.html" in cur, cur[:100])
                    check("全程未调用系统浏览器", len(external_calls) == 0,
                          str(external_calls[:3]))
                    finish()

            api._browser.events.loaded += on_loaded
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            failures.append("boot 异常")
            finish()

    console = webview.create_window(
        "控制台(测试)", url=mod._ui_uri("console.html"),
        js_api=api, width=700, height=500,
    )
    api.bind_windows(console)

    webview.start(func=boot, gui="edgechromium", private_mode=False)
    webbrowser.open = _real_open
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
