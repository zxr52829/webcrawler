"""
反射安全验证：复现 pywebview 的 get_functions 行为，确认卡死根因已修。

pywebview 会把 js_api 对象的所有非下划线属性递归暴露给前端：
  对每个属性 getattr；若是 method/function 就收进函数表；
  若是"非 callable 且有 __module__"的对象就递归进去。

旧实现把 Window 挂在 self.browser 上 → 递归进 Window.native
→ WinForms 窗体 → AccessibilityObject → 无限递归爆栈 → UI 线程卡死。

这个脚本模拟同样的遍历，断言：
  1) 暴露出来的函数清单是干净的（只有预期的 API 方法）
  2) 遍历过程绝不会触及 native / AccessibilityObject
"""
from __future__ import annotations

import inspect
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

import app as mod

failures: list[str] = []
visited: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + label + ("" if cond else f"  {detail}"))
    if not cond:
        failures.append(label)


def get_functions(obj, base_name="", functions=None, depth=0):
    """照抄 webview/util.py 的遍历逻辑（去掉 id 去重，保留递归结构）。"""
    if functions is None:
        functions = {}
    if depth > 6:                      # 加护栏，避免测试自己爆栈
        return functions

    for name in dir(obj):
        try:
            full_name = f"{base_name}.{name}" if base_name else name
            if name.startswith("_"):
                continue

            attr = getattr(obj, name)
            visited.append(full_name)

            if not getattr(attr, "_serializable", True):
                continue

            if inspect.ismethod(attr) or inspect.isfunction(attr):
                functions[full_name] = True
            elif inspect.isclass(attr) or (
                isinstance(attr, object) and not callable(attr) and hasattr(attr, "__module__")
            ):
                get_functions(attr, full_name, functions, depth + 1)
        except Exception:  # noqa: BLE001
            continue
    return functions


print("[1] Api 对象的可暴露属性")
inst = mod.Api()

# 模拟真实使用：挂上假的 window 对象（有 native 属性，模拟 WinForms 窗体）
class FakeNative:
    class AccessibilityObject:
        pass

class FakeWindow:
    native = FakeNative()
    def get_current_url(self): return "https://example.com"
    def load_url(self, u): pass
    def restore(self): pass
    def destroy(self): pass
    def evaluate_js(self, s): return None

inst._browser = FakeWindow()
inst._console = FakeWindow()

funcs = get_functions(inst)

check("暴露的函数非空", len(funcs) > 0, str(len(funcs)))
expected = {
    "ensure_browser", "navigate", "browser_back", "browser_forward",
    "browser_reload", "open_external", "browser_status",
    "scroll_to_bottom", "crawl", "export", "open_output_dir",
    "quit_app", "bind_windows",
}
missing = expected - set(funcs)
check("预期 API 方法都在", not missing, f"缺少: {missing}")

print("\n[2] 是否泄漏窗口引用")
leaks = [n for n in funcs if "native" in n.lower() or "accessibility" in n.lower()]
check("没有 native/AccessibilityObject 泄漏", not leaks, str(leaks[:6]))

bad_visited = [v for v in visited if "native" in v.lower() or "Accessibility" in v]
check("遍历过程未触及 native", not bad_visited, str(bad_visited[:6]))

print("\n[3] 私有属性是否被 dir 跳过")
# 注意：ensure_browser / browser_status 等公开方法名里也含 "_browser" 子串，
# 所以必须精确比对属性名本身，不能做子串匹配。
check("_browser 属性未被遍历", "_browser" not in visited,
      str([v for v in visited if v == "_browser"]))
check("_console 属性未被遍历", "_console" not in visited,
      str([v for v in visited if v == "_console"]))
check("_last_result 属性未被遍历", "_last_result" not in visited,
      str([v for v in visited if v == "_last_result"]))
# 确认公开方法确实在（证明遍历真的跑过，不是空跑）
check("公开方法 ensure_browser 在遍历中", "ensure_browser" in visited)

print("\n[4] _serializable 双保险")
check("Api._serializable is False", mod.Api._serializable is False)

print("\n[5] 功能未回归（无窗口时的错误路径）")
inst2 = mod.Api()
r = inst2.crawl("video")
check("无浏览器时 crawl 返回可读错误",
      r.get("ok") is False and "浏览器窗口" in (r.get("error") or ""), str(r))
r = inst2.browser_status()
check("无浏览器时 status 正常", r.get("opened") is False, str(r))
r = inst2.export()
check("无结果时 export 正常报错", r.get("ok") is False, str(r))

# 有窗口对象时 crawl 应能走到 evaluate_js（返回 None 会给出"页面无返回"）
r = inst.crawl("video")
check("有窗口但 evaluate 返回 None 时给出提示",
      r.get("ok") is False and "无返回" in (r.get("error") or ""), str(r))

print("\n[6] 遍历规模")
check(f"访问节点数 {len(visited)} 合理（< 200）", len(visited) < 200, str(len(visited)))

print("\n" + "=" * 52)
if failures:
    print(f"失败 {len(failures)} 项")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("反射安全验证全部通过 —— AccessibilityObject 递归已被根治")
