"""
验证清理面板的界面与交互（通过页面 JS 驱动，避开合成鼠标不可靠的问题）。

检查：
  1. 清理按钮存在且可点击
  2. 点开后遮罩显示
  3. 三个功能按钮都在
  4. 导出目录统计能刷新
  5. 非法/正常操作都有反馈文案
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


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label + ("" if cond else f"  {detail}"), flush=True)
    if not cond:
        failures.append(label)


def log(m: str) -> None:
    print(m, flush=True)


def main() -> int:
    webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = False
    mod.DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "_uitest_out"
    mod.DEFAULT_OUTPUT_DIR.mkdir(exist_ok=True)
    (mod.DEFAULT_OUTPUT_DIR / "demo.txt").write_text("x" * 2048, encoding="utf-8")

    api = mod.Api()

    def finish() -> None:
        log("\n" + "=" * 54)
        if failures:
            log(f"失败 {len(failures)} 项")
            for f in failures:
                log("  - " + f)
        else:
            log("清理面板验证全部通过")
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
            log("[0] 打开浏览器")
            api.ensure_browser("https://example.com")
            time.sleep(3.5)

            console = api._console
            if console is None:
                failures.append("控制台窗口缺失")
                finish()
                return

            def js(code: str):
                return console.evaluate_js(code)

            # 1. 按钮存在
            log("\n[1] 界面元素检查")
            check("清理按钮存在", js("!!document.getElementById('btnClean')"))
            check("遮罩层存在", js("!!document.getElementById('cleanMask')"))
            check("三个动作按钮齐全",
                  js("!!document.getElementById('btnClearCache')"
                     "&&!!document.getElementById('btnClearAll')"
                     "&&!!document.getElementById('btnClearOut')"))
            check("统计文案元素存在", js("!!document.getElementById('outHint')"))

            # 2. 初始遮罩隐藏
            log("\n[2] 遮罩显隐")
            hidden = js("!document.getElementById('cleanMask').classList.contains('show')")
            check("初始未显示", hidden is True, str(hidden))

            # 3. 点击触发显示（走真实 click 事件）
            js("document.getElementById('btnClean').click();")
            time.sleep(1.2)
            shown = js("document.getElementById('cleanMask').classList.contains('show')")
            check("点击后遮罩显示", shown is True, str(shown))

            # 4. 导出目录统计刷新（refreshOutputSize 是 async）
            hint = js("document.getElementById('outHint').textContent")
            log(f"    统计文案: {str(hint)[:80]}")
            check("统计文案已更新（含文件数或大小）",
                  bool(hint) and ("个文件" in str(hint) or "暂无" in str(hint)),
                  str(hint)[:100])

            # 5. 删除导出（先绕过 confirm）
            log("\n[3] 删除导出文件")
            js("window.confirm = function(){return true};")
            js("document.getElementById('btnClearOut').click();")
            time.sleep(2.0)
            remain = list(mod.DEFAULT_OUTPUT_DIR.iterdir()) if mod.DEFAULT_OUTPUT_DIR.exists() else []
            check("导出文件已删除", len(remain) == 0, str([p.name for p in remain]))

            msg = js("document.getElementById('cleanMsg').textContent")
            log(f"    反馈文案: {str(msg)[:80]}")
            check("有操作反馈", bool(msg), str(msg)[:80])

            hint2 = js("document.getElementById('outHint').textContent")
            log(f"    刷新后统计: {str(hint2)[:80]}")
            check("统计已刷新为无文件", "暂无" in str(hint2), str(hint2)[:100])

            # 6. 清理缓存（走真实链路）
            log("\n[4] 清理缓存")
            js("window.confirm = function(){return true};")
            js("document.getElementById('btnClearCache').click();")
            time.sleep(3.5)
            msg2 = js("document.getElementById('cleanMsg').textContent")
            log(f"    反馈: {str(msg2)[:80]}")
            check("缓存清理有反馈", bool(msg2) and ("清理" in str(msg2) or "失败" in str(msg2)),
                  str(msg2)[:100])

            # 7. 关闭面板
            js("document.getElementById('btnCloseClean').click();")
            time.sleep(0.8)
            closed = js("!document.getElementById('cleanMask').classList.contains('show')")
            check("关闭后遮罩隐藏", closed is True, str(closed))

            finish()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            failures.append("运行异常")
            finish()

    console = webview.create_window(
        "控制台(测试)", url=mod._ui_uri("console.html"),
        js_api=api, width=760, height=560, x=40, y=40,
    )
    api.bind_windows(console)

    webview.start(func=boot, gui="edgechromium", private_mode=False)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
