"""
验证"抓太早导致 blob"的假设。

推断：你抓到 blob 的那次，很可能是在视频还没协商好清晰度时就点了抓取。
此时 <video> 存在但 currentSrc 还是 blob:。

做法：在多个不同时刻各抓一次，记录 src 类型和能否拿到真实流。
     重点看早期时刻（1s/2s/3s）和晚期时刻（8s/12s）的差别。
"""
from __future__ import annotations

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

failures: list[str] = []
TARGET = sys.argv[1] if len(sys.argv) > 1 else ""
DELAYS = [1.5, 2.5, 4.0, 8.0, 12.0]


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label + ("" if cond else f"  {detail}"), flush=True)
    if not cond:
        failures.append(label)


def log(m: str) -> None:
    print(m, flush=True)


def main() -> int:
    if not TARGET:
        print("用法: python " + Path(sys.argv[0]).name + " <视频页面URL>")
        print("  例: python " + Path(sys.argv[0]).name
              + " https://www.bilibili.com/video/BVxxxxxxxx/")
        print("  需要一个公开的视频页面；脚本会在真实浏览器里打开它再抓取。")
        return 2
    webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = False
    mod.DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "_timing_out"
    mod.DEFAULT_OUTPUT_DIR.mkdir(exist_ok=True)

    api = mod.Api()
    rows: list[tuple] = []

    def finish() -> None:
        log("\n" + "=" * 62)
        log(f"{'时刻':>6} | {'src类型':>8} | {'真实流':>6} | {'blob':>4} | 结论")
        log("-" * 62)
        for t, kind, real, blob in rows:
            verdict = "可用" if real else ("失败" if kind == "blob" else "无")
            log(f"{t:>5.1f}s | {kind:>8} | {real:>6} | {blob:>4} | {verdict}")

        if failures:
            log(f"\n失败 {len(failures)} 项")
            for f in failures:
                log("  - " + f)
        else:
            log("\n时序验证全部通过（每次抓取都拿到了可用地址）")
        sys.stdout.flush()
        for w in (api._browser, api._console):
            try:
                if w is not None:
                    w.destroy()
            except Exception:  # noqa: BLE001
                pass

    def get_kind() -> str:
        try:
            k = api._browser.evaluate_js(
                "(function(){var v=document.querySelector('video');"
                "if(!v)return 'no-video';"
                "var s=v.currentSrc||v.src||'';"
                "return s.indexOf('blob:')===0?'blob':(s?'direct':'empty')})()"
            )
            return str(k)
        except Exception:  # noqa: BLE001
            return "err"

    def boot() -> None:
        try:
            time.sleep(1.0)
            api.ensure_browser(TARGET)
            if api._browser is None:
                failures.append("窗口未创建")
                finish()
                return

            for i, delay in enumerate(DELAYS):
                if i > 0:
                    api._browser.evaluate_js("location.reload();")
                log(f"\n── 等待 {delay}s 后抓取 ──")
                time.sleep(delay)

                kind = get_kind()
                r = api.crawl("video")
                if not r.get("ok"):
                    log(f"  crawl 失败: {str(r.get('error'))[:100]}")
                    rows.append((delay, kind, 0, 0))
                    continue

                urls = [x.get("url", "") for x in (r.get("preview") or [])]
                real = len([u for u in urls if mod._is_real_stream(u)])
                blob = len([u for u in urls if str(u).startswith("blob:")])
                rows.append((delay, kind, real, blob))
                log(f"  src={kind}  真实流={real}  blob={blob}")

                check(f"{delay}s 时抓取有可用地址", real > 0, f"kind={kind} urls={urls[:2]}")

            finish()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            failures.append("运行异常")
            finish()

    console = webview.create_window(
        "控制台(测试)", url=mod._ui_uri("console.html"),
        js_api=api, width=520, height=400, x=40, y=40,
    )
    api.bind_windows(console)

    webview.start(func=boot, gui="edgechromium", private_mode=False)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())

