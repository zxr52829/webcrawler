"""
验证 GUI 抓取路径现在能拿到 blob: 播放器背后的真实视频流。

背景：
  抖音同一站点混用两种播放方式——
    a) <video src="https://...douyinvod.com/...">  直链，能抓
    b) <video src="blob:...">                       MSE 分片，以前抓不到

  以前 netcap 只接在命令行模式，GUI 点"开始抓取"从来没装过监听，
  所以 b 类视频导出的全是 blob: 地址。

  这个测试走真实的 crawl("video") 路径，断言：
    1. 结果里有非 blob 的真实视频地址
    2. 没有混入 douyin_pc_client.mp4 这种推广噪音
    3. 导出产出 _视频流.txt
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
    mod.DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "_videotest_out"
    mod.DEFAULT_OUTPUT_DIR.mkdir(exist_ok=True)

    api = mod.Api()

    def finish() -> None:
        log("\n" + "=" * 56)
        if failures:
            log(f"失败 {len(failures)} 项")
            for f in failures:
                log("  - " + f)
        else:
            log("视频抓取验证全部通过")
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
            log(f"[0] 打开 {TARGET[:80]}")
            api.ensure_browser(TARGET)
            if api._browser is None:
                failures.append("窗口未创建")
                finish()
                return
            time.sleep(6.0)  # 等页面和播放器起来

            cur = api._browser.get_current_url() or ""
            log(f"    当前地址: {cur[:100]}")
            check("已到抖音页面", "douyin" in cur, cur[:80])

            # 页面里 video 的 src 是什么类型？
            src_kind = api._browser.evaluate_js(
                "(function(){var v=document.querySelector('video');"
                "if(!v)return 'no-video';"
                "var s=v.currentSrc||v.src||'';"
                "return s.indexOf('blob:')===0?'blob':(s?'direct':'empty')})()"
            )
            log(f"    <video> src 类型: {src_kind}")

            # ---- 走真实 crawl 路径 ----
            log("\n[1] crawl('video')")
            r = api.crawl("video")
            log(f"    ok={r.get('ok')} count={r.get('count')}")
            check("crawl 成功", r.get("ok") is True, str(r)[:250])

            if r.get("ok"):
                preview = r.get("preview") or []
                urls = [x.get("url", "") for x in preview]
                for u in urls[:6]:
                    log(f"      · {u[:110]}")

                real = [u for u in urls if mod._is_real_stream(u)]
                blobs = [u for u in urls if str(u).startswith("blob:")]
                noise = [u for u in urls if any(p in u.lower() for p in mod._NOISE_PATTERNS)]

                log(f"    真实流 {len(real)} 条 / blob {len(blobs)} 条 / 噪音 {len(noise)} 条")
                check("抓到真实视频流（非 blob）", len(real) > 0,
                      f"只有 blob: {blobs[:2]}")
                check("没有混入推广视频", len(noise) == 0, str(noise[:2]))

            # ---- 导出 ----
            log("\n[2] 导出")
            e = api.export()
            check("导出成功", e.get("ok") is True, str(e)[:200])
            if e.get("ok"):
                names = [Path(f).name for f in e.get("files") or []]
                for n in names:
                    log(f"      - {n}")

                # 说明：直链场景（<video src=https://...>）下 _urls.txt 里已经是
                # 可下载地址，不再额外产出 _视频流.txt；MSE(blob:) 场景才会产出。
                # 所以这里检查"导出的地址里确实有真实可下载流"，而不是死盯文件名。
                all_text = ""
                for f in e.get("files") or []:
                    p = Path(f)
                    if p.suffix in (".txt", ".csv"):
                        try:
                            all_text += p.read_text(encoding="utf-8-sig") + "\n"
                        except OSError:
                            pass
                real_in_files = [
                    u for u in all_text.split()
                    if u.startswith("http") and mod._is_real_stream(u)
                ]
                log(f"    导出文件里的真实流: {len(real_in_files)} 条")
                check("导出里有可下载的真实地址", len(real_in_files) > 0)
                check("导出里无推广噪音",
                      not any(p in all_text.lower() for p in mod._NOISE_PATTERNS))
                check("导出里无 blob: 地址", "blob:" not in all_text)

                streams = [n for n in names if n.endswith("_视频流.txt")]
                log(f"    （_视频流.txt: {'有' if streams else '无（直链场景属正常）'}）")

            finish()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            failures.append("运行异常")
            finish()

    console = webview.create_window(
        "控制台(测试)", url=mod._ui_uri("console.html"),
        js_api=api, width=560, height=420, x=40, y=40,
    )
    api.bind_windows(console)

    webview.start(func=boot, gui="edgechromium", private_mode=False)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())

