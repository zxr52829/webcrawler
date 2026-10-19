"""
压力验证：连续多次抓同一抖音视频，覆盖"直链"与"MSE(blob:)"两种播放路径。

抖音对同一视频不保证每次都用同一种播放方式（取决于加载时机、清晰度协商）。
以前只有直链能抓到，blob 场景导出全是 blob:。
这个测试反复抓，统计两种场景各出现几次，并断言：
  **只要页面是 blob 播放，就必须能通过 netcap 拿到真实流。**
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
ROUNDS = 4


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
    mod.DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "_stress_out"
    mod.DEFAULT_OUTPUT_DIR.mkdir(exist_ok=True)

    api = mod.Api()
    stats = {"direct": 0, "blob": 0, "no-video": 0}
    blob_ok = 0
    blob_fail = 0

    def finish() -> None:
        log("\n" + "=" * 58)
        log(f"场景统计: 直链 {stats['direct']} 次 / blob {stats['blob']} 次 / 无视频 {stats['no-video']} 次")
        if stats["blob"]:
            log(f"blob 场景下成功拿到真实流: {blob_ok} 次，失败: {blob_fail} 次")
        if failures:
            log(f"失败 {len(failures)} 项")
            for f in failures:
                log("  - " + f)
        else:
            log("压力验证全部通过")
        sys.stdout.flush()
        for w in (api._browser, api._console):
            try:
                if w is not None:
                    w.destroy()
            except Exception:  # noqa: BLE001
                pass

    def boot() -> None:
        try:
            time.sleep(1.0)
            api.ensure_browser(TARGET)
            if api._browser is None:
                failures.append("窗口未创建")
                finish()
                return

            for i in range(ROUNDS):
                log(f"\n──────── 第 {i + 1}/{ROUNDS} 轮 ────────")
                if i > 0:
                    # 重新加载，换一次播放协商
                    api._browser.evaluate_js("location.reload();")
                time.sleep(5.0 if i == 0 else 6.5)

                kind = api._browser.evaluate_js(
                    "(function(){var v=document.querySelector('video');"
                    "if(!v)return 'no-video';"
                    "var s=v.currentSrc||v.src||'';"
                    "return s.indexOf('blob:')===0?'blob':(s?'direct':'empty')})()"
                )
                kind = str(kind)
                log(f"  <video> src: {kind}")
                if kind == "blob":
                    stats["blob"] += 1
                elif kind == "direct":
                    stats["direct"] += 1
                else:
                    stats["no-video"] += 1

                r = api.crawl("video")
                if not r.get("ok"):
                    log(f"  crawl 失败: {str(r.get('error'))[:120]}")
                    failures.append(f"第{i+1}轮 crawl 失败")
                    continue

                urls = [x.get("url", "") for x in (r.get("preview") or [])]
                real = [u for u in urls if mod._is_real_stream(u)]
                blobs = [u for u in urls if str(u).startswith("blob:")]
                log(f"  结果: 真实流 {len(real)} / blob {len(blobs)}")

                if kind == "blob":
                    if real:
                        blob_ok += 1
                        log("  → blob 播放也能拿到真实流 ✓")
                    else:
                        blob_fail += 1
                        log("  → blob 播放仍未拿到真实流 ✗")
                        failures.append(f"第{i+1}轮 blob 场景未拿到真实流")

                check(f"第 {i+1} 轮有可用视频地址", len(real) > 0,
                      f"urls={urls[:3]}")

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

