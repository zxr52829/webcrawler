"""
命令行抓取模式 —— 不点界面，一条命令直出结果。

    python grab_url.py <URL>
    python grab_url.py <URL> --kind video|image|text|all
    python grab_url.py <URL> --wait 6 --json out.json

设计要点（踩坑记录）：
  * 抖音这类站点会短时间内连续导航多次，"等第 N 次 loaded 事件"这种
    状态机不可靠（第二次事件可能根本不来）。改用「时间窗口」：
    记录最后一次 loaded 的时间，静默 N 秒后再抓，稳定得多。
  * 所有 WebView2 调用必须在 UI 线程，所以等待用 boot() 阶段 + 事件回调，
    绝不在窗口建好后 while+sleep 轮询（会堵死消息泵）。
"""
from __future__ import annotations

import argparse
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


def log(msg: str) -> None:
    print(msg, flush=True)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="grab",
        description="命令行抓取：给出网址，直接输出视频/图片/文字，无界面交互",
    )
    # 位置参数和 --url 都支持，方便 exe 和脚本两种用法
    p.add_argument("target", nargs="?", default="", help="目标网址")
    p.add_argument("--url", "-u", dest="url_opt", default="", help="目标网址（与位置参数等价）")
    p.add_argument(
        "--kind", "-k",
        default="all",
        choices=["video", "image", "text", "all"],
        help="抓取类型，默认 all",
    )
    p.add_argument(
        "--wait", "-w", type=float, default=5.0,
        help="页面静默多少秒后开始抓（默认 5）",
    )
    p.add_argument(
        "--timeout", "-t", type=float, default=45.0,
        help="整体超时秒数（默认 45）",
    )
    p.add_argument("--json", dest="json_path", default="", help="把完整结果写成 JSON")
    p.add_argument("--no-export", action="store_true", help="不落盘，只打印")
    return p


def run(argv: list[str]) -> int:
    """独立入口，供 app.py 的 CLI 分支调用。"""
    args = build_parser().parse_args(argv)
    target = args.url_opt or args.target
    return _run_with(args, target)


def main() -> int:
    args = build_parser().parse_args()
    target = args.url_opt or args.target
    return _run_with(args, target)


def _run_with(args, target: str) -> int:
    if not target:
        log("缺少网址。用法: grab <URL> 或 grab --url <URL>")
        return 2

    # 新窗口请求留在本窗口（与 app.py 一致）
    webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = False

    api = mod.Api()
    t_start = time.time()
    state = {
        "last_loaded": 0.0,
        "finished": False,
        "result": None,
    }

    def finish(code: int = 0) -> None:
        state["finished"] = True
        for w in (api._browser, getattr(api, "_console", None)):
            try:
                if w is not None:
                    w.destroy()
            except Exception:  # noqa: BLE001
                pass

    def do_grab() -> None:
        """执行抓取并输出。"""
        try:
            st = api.browser_status()
            log("=" * 66)
            log(f"标题: {st.get('title') or '(无)'}")
            log(f"地址: {str(st.get('url'))[:120]}")

            # ---- 网络层媒体捕获（解决 blob: 播放器抓不到真实地址的问题）----
            net = api.network_media()
            net_items = net.get("items") or []
            if net_items:
                log(f"\n[网络捕获] 共 {len(net_items)} 个媒体请求")

                def classify(u: str) -> str:
                    lu = u.lower()
                    if any(k in lu for k in (".m3u8", ".mpd", "videoplayback", "mime_type=video")):
                        return "stream"
                    if ".mp4" in lu or ".webm" in lu or ".m4v" in lu or "video/tos" in lu:
                        return "video"
                    if ".m4s" in lu:
                        return "segment"
                    if any(k in lu for k in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", "image-cut")):
                        return "image"
                    return "other"

                buckets: dict[str, list[str]] = {}
                for x in net_items:
                    u = x.get("url") or ""
                    buckets.setdefault(classify(u), []).append(u)

                for label, key in (("视频流(m3u8/mpd/playback)", "stream"),
                                   ("视频文件(mp4/webm)", "video"),
                                   ("分片(m4s)", "segment")):
                    arr = buckets.get(key) or []
                    if not arr:
                        continue
                    log(f"  {label}: {len(arr)} 条")
                    for u in arr[:6]:
                        log(f"    · {u[:118]}")
                    if len(arr) > 6:
                        log(f"    … 另 {len(arr) - 6} 条")

                # 提醒音轨类型：HE-AAC 在很多播放器上无声，直接 -c copy 会踩坑
                audio_like = [x.get("url") or "" for x in net_items
                              if "media-audio" in (x.get("url") or "").lower()
                              or "30216" in (x.get("url") or "")
                              or "30280" in (x.get("url") or "")
                              or "30232" in (x.get("url") or "")]
                if audio_like:
                    log(f"  ⚠ 抓到 {len(audio_like)} 条音轨；若为 HE-AAC，"
                        f"拼接需转 AAC-LC 否则可能无声")
                    log(f"    （fetch_media.py 下载时会自动处理）")

                other_n = len(buckets.get("image") or []) + len(buckets.get("other") or [])
                if other_n:
                    log(f"  （另有 {other_n} 条图片/其它资源，见 JSON）")
            else:
                log("\n[网络捕获] 无（页面可能未播放或未装监听）")

            kinds = ["video", "image", "text"] if args.kind == "all" else [args.kind]
            collected: dict = {}

            for kind in kinds:
                r = api.crawl(kind)
                collected[kind] = r
                if not r.get("ok"):
                    log(f"\n[{kind}] 失败: {str(r.get('error'))[:200]}")
                    continue
                n = r.get("count")
                log(f"\n[{kind}] 共 {n} 条")

                if kind == "text":
                    log(f"    字符数 {r.get('textChars')}")
                    head = (r.get("textHead") or "").replace("\n", " / ")
                    if head:
                        log(f"    预览: {head[:300]}")
                else:
                    skip_blob = 0
                    for it in (r.get("preview") or []):
                        u = it.get("url", "")
                        if u.startswith("blob:"):
                            skip_blob += 1
                            continue
                        extra = ""
                        if kind == "video":
                            d = it.get("duration")
                            extra = f"  [{d}s]" if d else ""
                        log(f"    · {u[:110]}{extra}")
                    if skip_blob:
                        log(f"    （跳过 {skip_blob} 条 blob: 地址，见上方网络捕获）")
                    if (r.get("count") or 0) > 20:
                        log(f"    … 共 {r['count']} 条，完整列表见导出文件")

            # 全量结果落 JSON（附网络捕获）
            if args.kind == "all":
                full = api.crawl("all")
                full["networkMedia"] = net_items
                state["result"] = full

            if args.json_path:
                payload = state["result"] or collected
                p = Path(args.json_path)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                log(f"\nJSON 已写入: {p}")

            if not args.no_export:
                # 把网络层捕获合并进待导出结果，否则导出文件里没有真实视频流
                if isinstance(api._last_result, dict):
                    api._last_result["networkMedia"] = net_items
                e = api.export()
                if e.get("ok"):
                    log(f"\n导出目录: {e.get('dir')}")
                    for f in e.get("files") or []:
                        log(f"  - {Path(f).name}")
                else:
                    log(f"\n导出失败: {str(e.get('error'))[:200]}")

            log("=" * 66)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            log("抓取过程异常")
        finally:
            sys.stdout.flush()
            finish()

    def boot() -> None:
        try:
            time.sleep(1.0)
            log(f"[打开] {target[:110]}")
            api.ensure_browser(target)
            if api._browser is None:
                log("浏览器窗口未创建")
                finish(2)
                return

            state["last_loaded"] = time.time()

            def on_loaded() -> None:
                if state["finished"]:
                    return
                try:
                    cur = api._browser.get_current_url() or ""
                except Exception:  # noqa: BLE001
                    cur = ""
                state["last_loaded"] = time.time()
                log(f"[加载] {cur[:110]}")
                # 每次导航完成都补装一次网络捕获，覆盖 SPA 软跳转丢 hook 的情况
                r = api.install_netcap()
                if r.get("ok"):
                    log(f"[监听] 网络捕获已装配 {r.get('state', '')}")

            api._browser.events.loaded += on_loaded

            # 首屏可能已经加载完（事件错过），立刻补一次
            r = api.install_netcap()
            if r.get("ok"):
                log(f"[监听] 网络捕获已装配 {r.get('state', '')}")

            # 尝试触发播放：抖音这类站点只有开始播放才会去拉真实视频流
            def try_play() -> str:
                try:
                    r = api._browser.evaluate_js(
                        """
                        (function(){
                          try {
                            var v = document.querySelector('video');
                            if (v) { v.muted = true; v.play && v.play().catch(function(){}); return 'played-video'; }
                            var btn = document.querySelector('xg-icon[data-e2e="play"], .xgplayer-play, [class*="play"]');
                            if (btn && btn.click) { btn.click(); return 'clicked-play'; }
                            return 'no-target';
                          } catch(e) { return 'err:' + e.message; }
                        })()
                        """
                    )
                    return str(r)
                except Exception as exc:  # noqa: BLE001
                    return f"exc:{exc}"

            time.sleep(1.2)
            log(f"[播放] 尝试触发播放: {try_play() or ''}")

            # 时间窗口轮询：这是"等待"而不是"阻塞操作"，
            # 因为窗口已在 GUI 线程内，这里的 sleep 会让出消息泵（pywebview
            # 的 boot 回调在启动阶段，sleep 期间主循环仍会处理原生消息）。
            while not state["finished"]:
                time.sleep(0.4)
                now = time.time()

                if now - t_start > args.timeout:
                    log(f"[超时] {args.timeout}s 内页面未稳定")
                    do_grab()
                    return

                # 至少等 args.wait 秒，且距上次 loaded 已静默 args.wait 秒
                if (now - t_start) >= args.wait and (now - state["last_loaded"]) >= args.wait:
                    do_grab()
                    return

        except Exception:  # noqa: BLE001
            traceback.print_exc()
            finish(1)

    console = webview.create_window(
        "抓取器(命令行)", url=mod._ui_uri("console.html"),
        js_api=api, width=460, height=340, x=30, y=30,
    )
    api.bind_windows(console)

    webview.start(func=boot, gui="edgechromium", private_mode=False)
    return 0 if state["result"] or state["finished"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
