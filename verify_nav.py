"""
验证后退/前进在 WebView2 里真实可用。

场景：
  A 页 -> 点 target=_blank 跳到 B 页（现在会顶掉 A）
  -> 点「后退」应回到 A
  -> 点「前进」应回到 B

这条链是「链接顶掉当前页」的兜底手段，必须确认能用。
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

_real_open = webbrowser.open
external: list[str] = []
webbrowser.open = lambda u, *a, **k: (external.append(str(u)), True)[1]


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label + ("" if cond else f"  {detail}"), flush=True)
    if not cond:
        failures.append(label)


def log(m: str) -> None:
    print(m, flush=True)


PAGE_A = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>A页</title></head>
<body style="padding:20px"><h1>A 页</h1>
<a id="lnk" href="b.html" target="_blank">去 B 页</a></body></html>"""
PAGE_B = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>B页</title></head>
<body style="padding:20px"><h1>B 页</h1></body></html>"""


def main() -> int:
    work = HERE / "_navtest"
    work.mkdir(exist_ok=True)
    (work / "a.html").write_text(PAGE_A, encoding="utf-8")
    (work / "b.html").write_text(PAGE_B, encoding="utf-8")
    a_uri = (work / "a.html").as_uri()

    webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = False
    api = mod.Api()
    state = {"stage": 0}

    def finish() -> None:
        log("\n" + "=" * 52)
        if failures:
            log(f"失败 {len(failures)} 项")
            for f in failures:
                log("  - " + f)
        else:
            log("导航验证全部通过")
        log(f"外部浏览器调用: {len(external)}")
        sys.stdout.flush()
        for w in (api._browser, api._console):
            try:
                if w is not None:
                    w.destroy()
            except Exception:  # noqa: BLE001
                pass

    def cur() -> str:
        try:
            return api._browser.get_current_url() or ""
        except Exception:  # noqa: BLE001
            return ""

    def boot() -> None:
        try:
            time.sleep(1.5)
            api.ensure_browser(a_uri)
            if api._browser is None:
                failures.append("窗口未创建")
                finish()
                return

            def on_loaded() -> None:
                u = cur()
                log(f"    loaded: {u[-40:]}")

                if state["stage"] == 0:
                    if "a.html" not in u:
                        return
                    state["stage"] = 1
                    log("[1] 点 target=_blank 链接 -> 应到 b.html")
                    api._browser.evaluate_js("document.getElementById('lnk').click();")
                    return

                if state["stage"] == 1:
                    if "b.html" not in u:
                        return
                    state["stage"] = 2
                    check("链接在本窗口打开（b.html）", "b.html" in u, u[-60:])
                    check("未跳系统浏览器", len(external) == 0, str(external))
                    log("[2] 点后退 -> 应回 a.html")
                    # 后退是异步的，用事件接
                    api.browser_back()
                    return

                if state["stage"] == 2:
                    if "a.html" not in u:
                        return
                    state["stage"] = 3
                    check("后退回到 a.html", "a.html" in u, u[-60:])
                    log("[3] 点前进 -> 应回 b.html")
                    api.browser_forward()
                    return

                if state["stage"] == 3:
                    if "b.html" not in u:
                        return
                    state["stage"] = 4
                    check("前进回到 b.html", "b.html" in u, u[-60:])
                    finish()

            api._browser.events.loaded += on_loaded
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            failures.append("boot 异常")
            finish()

    console = webview.create_window(
        "控制台(测试)", url=mod._ui_uri("console.html"),
        js_api=api, width=640, height=460,
    )
    api.bind_windows(console)

    webview.start(func=boot, gui="edgechromium", private_mode=False)
    webbrowser.open = _real_open
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
