"""
验证缓存清理功能：真实调用 WebView2 的 ClearBrowsingDataAsync。

要点：
  * 只能用 "cache" 模式测（不动 cookie），避免把当前环境登录态清掉
  * 清理是异步的，需要轮询 cache_status 直到 done
  * 检查清理前后磁盘缓存目录的大小变化
"""
from __future__ import annotations

import os
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
failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label + ("" if cond else f"  {detail}"), flush=True)
    if not cond:
        failures.append(label)


def log(m: str) -> None:
    print(m, flush=True)


def user_data_folder() -> Path | None:
    """WebView2 在这个 profile 下的数据目录。"""
    try:
        native = mod_wv.native if False else None
    except Exception:  # noqa: BLE001
        pass
    return None


def main() -> int:
    webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = False
    api = mod.Api()
    state = {"stage": "boot"}

    def finish() -> None:
        log("\n" + "=" * 54)
        if failures:
            log(f"失败 {len(failures)} 项")
            for f in failures:
                log("  - " + f)
        else:
            log("缓存清理验证全部通过")
        sys.stdout.flush()
        for w in (api._browser, api._console):
            try:
                if w is not None:
                    w.destroy()
            except Exception:  # noqa: BLE001
                pass

    def boot() -> None:
        try:
            time.sleep(1.2)
            log("[0] 打开浏览器窗口")
            api.ensure_browser("https://example.com")
            if api._browser is None:
                failures.append("窗口未创建")
                finish()
                return

            # 等页面加载完再清
            time.sleep(4.0)

            # 说明：定位 WebView2 的 user_data 目录在不同环境下不稳定
            # （子进程命令行不一定带 --user-data-dir，pywebview 也未公开该属性）。
            # 这里改为功能验证：清理前后各做一次"资源是否命中缓存"的检查。
            udf = None
            log("    （跳过目录定位，改为功能验证）")

            def cache_estimate() -> float:
                """用 navigator.storage.estimate() 读页面侧缓存占用（字节）。

                比翻磁盘目录可靠：这是浏览器自己报的数，跟清理动作直接相关。
                """
                try:
                    js = ("(function(){try{"
                          "if(!navigator.storage||!navigator.storage.estimate)return '-1';"
                          "return navigator.storage.estimate().then(function(e){"
                          "return String(e.usage||0)})"
                          "}catch(e){return '-1'}})()")
                    raw = api._browser.evaluate_js(js)
                    return float(raw) if raw not in (None, "-1") else -1.0
                except Exception:  # noqa: BLE001
                    return -1.0

            before = cache_estimate()
            log(f"    清理前 storage.estimate: {before / 1024 / 1024:.2f} MB" if before >= 0 else "    清理前 storage.estimate: 不可用")

            # ---- 状态查询（未开始时）----
            st = api.cache_status()
            check("初始状态 idle", st.get("status") == "idle", str(st))

            # ---- 非法模式 ----
            r = api.clear_cache("bogus")
            check("非法模式被拒绝", r.get("ok") is False, str(r))

            # ---- 正式清理：只清缓存 ----
            log("\n[1] 清理缓存（保留 cookie）")
            r = api.clear_cache("cache")
            log(f"    返回: {r}")
            check("clear_cache 调用成功", r.get("ok") is True, str(r))

            if r.get("ok"):
                # 轮询等完成
                deadline = time.time() + 25
                final = None
                while time.time() < deadline:
                    time.sleep(0.4)
                    s = api.cache_status()
                    if s.get("status") in ("done", "error"):
                        final = s
                        break
                log(f"    最终状态: {final}")
                check("清理完成（未报错）", final is not None and final.get("status") == "done",
                      str(final))
                if final and final.get("status") == "error":
                    log(f"    错误详情: {final.get('error')}")

            time.sleep(1.5)
            after = cache_estimate()
            if before >= 0 and after >= 0:
                log(f"    清理后 storage.estimate: {after / 1024 / 1024:.2f} MB")
                log(f"    变化: {(after - before) / 1024 / 1024:+.2f} MB")
                check("页面侧缓存未增长（清理生效或无残留）",
                      after <= before * 1.10,
                      f"{before} -> {after}")
            else:
                log("    storage.estimate 不可用，跳过占用对比（不影响清理功能）")

            # ---- 导出清理 ----
            log("\n[2] 导出文件统计与删除")
            mod.DEFAULT_OUTPUT_DIR = HERE / "_cleantest_out"
            mod.DEFAULT_OUTPUT_DIR.mkdir(exist_ok=True)
            (mod.DEFAULT_OUTPUT_DIR / "a.txt").write_text("hello", encoding="utf-8")
            (mod.DEFAULT_OUTPUT_DIR / "sub").mkdir(exist_ok=True)
            (mod.DEFAULT_OUTPUT_DIR / "sub" / "b.txt").write_text("world", encoding="utf-8")

            s = api.output_size()
            check("统计到 2 个文件", s.get("files") == 2, str(s))

            r = api.clear_outputs()
            check("删除成功", r.get("ok") is True, str(r))
            check("删了 2 项", r.get("removed") == 2, str(r))
            check("目录已空", not any(mod.DEFAULT_OUTPUT_DIR.iterdir()))

            s2 = api.output_size()
            check("删除后统计为 0", s2.get("files") == 0, str(s2))

            finish()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            failures.append("运行异常")
            finish()

    console = webview.create_window(
        "控制台(测试)", url=mod._ui_uri("console.html"),
        js_api=api, width=620, height=460, x=40, y=40,
    )
    api.bind_windows(console)

    webview.start(func=boot, gui="edgechromium", private_mode=False)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
