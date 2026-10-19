"""
网页素材抓取器 —— 控制台 + 浏览器双窗口，抓视频/图片/文字。

架构决策（踩坑记录，改动前请先读）：
  1) 不用 iframe 嵌目标站：绝大多数站点发 X-Frame-Options/CSP 拒绝显示。
  2) 不用"启动时同时创建两个窗口 + js_api"：实测这种组合下，浏览器窗口的
     loaded 事件会整体丢失，导航完不成就死锁。
  3) 采用"先建控制台，GUI 启动后再动态创建浏览器窗口"：
     loaded 事件正常、evaluate_js 正常、crawl 正常。
  4) WebView2 的 COM 只能在 UI 线程访问，所以 API 方法必须是 pywebview
     从 UI 线程回调进来的，不能自己开线程去调。

提取逻辑见 extract.js，注入当前页面执行 —— 抓的是你眼前那个 DOM。
"""

from __future__ import annotations

import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import webview

APP_TITLE = "网页素材抓取器"
APP_VERSION = "1.0"
BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
SRC_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = Path.home() / "Downloads" / "网页抓取结果"
START_URL = "https://www.bing.com/"


def _asset(name: str) -> Path | None:
    for d in (SRC_DIR, BASE_DIR):
        p = d / name
        if p.exists():
            return p
    return None


def _ui_uri(name: str) -> str:
    p = _asset(name)
    if p is None:
        raise FileNotFoundError(f"界面文件缺失: {name}")
    return p.resolve().as_uri()


def _load_extract_source() -> str:
    p = _asset("extract.js")
    if p is None:
        return "function(){return {ok:false,error:'extract.js 未找到'};}"
    try:
        return p.read_text(encoding="utf-8")
    except OSError as exc:
        return "function(){return {ok:false,error:%s};}" % json.dumps(str(exc))


EXTRACT_SOURCE = _load_extract_source()


def _load_netcap_source() -> str:
    p = _asset("netcap.js")
    if p is None:
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


NETCAP_SOURCE = _load_netcap_source()


def _safe_name(text: str, fallback: str = "result") -> str:
    bad = '<>:"/\\|?*'
    out = "".join("_" if ch in bad else ch for ch in (text or ""))
    out = " ".join(out.split()).strip(" .")
    return (out or fallback)[:60]


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


# 站点自带客户端推广视频，不是用户要看的内容，导出时要滤掉
_NOISE_PATTERNS = (
    "douyin_pc_client.mp4",
    "/download/douyin_pc_client",
    "client_download",
)

# B 站 DASH 轨道的 codecid。
# ⚠ 注意：这些数字**不是**固定的"清晰度代号"，不同视频/view 会指向不同编码，
#   实测 BV1epJY6NEVw 的 100023 是 AV1 852x480，而不是传说中的 1080P H.264。
#   所以这里只做"分类"（视频轨/音频轨）和"相对排序"，不硬贴分辨率标签，
#   避免误导用户。真实规格以下载后 ffprobe 为准。
BILI_VIDEO_CODEC_ORDER = {
    "100026": 240,
    "100025": 360,
    "100024": 480,
    "100022": 720,
    "100023": 1080,
    "100027": 1080,
    "100028": 1080,
    "100029": 2160,
}
BILI_AUDIO_CODEC_ORDER = {
    "30216": 64,
    "30232": 132,
    "30250": 128,
    "30280": 192,
}


def bili_track_info(url: str) -> dict | None:
    """解析 B 站 m4s 轨道 URL，返回 {kind, codecid, rank, label}。

    B 站 DASH 把视频和音频拆成两个独立的完整轨道，
    文件名形如 42806806465-1-100023.m4s（avid-序号-codecid）。

    label 只标注 codecid 与轨道类型，不宣称具体分辨率 —— 因为
    codecid 与分辨率的对应关系随视频而变（实测 100023 可能是 AV1 480p）。
    """
    if not url or ".m4s" not in url.lower():
        return None
    try:
        base = url.split("?")[0].rsplit("/", 1)[-1]
        stem = base[:-4] if base.lower().endswith(".m4s") else base
        codecid = stem.rsplit("-", 1)[-1]
    except Exception:  # noqa: BLE001
        return None

    if codecid in BILI_VIDEO_CODEC_ORDER:
        return {
            "kind": "video",
            "codecid": codecid,
            "rank": BILI_VIDEO_CODEC_ORDER[codecid],
            "label": f"视频轨 {codecid}",
        }
    if codecid in BILI_AUDIO_CODEC_ORDER:
        return {
            "kind": "audio",
            "codecid": codecid,
            "rank": BILI_AUDIO_CODEC_ORDER[codecid],
            "label": f"音频轨 {codecid}",
        }
    # 未知 codecid：按前缀归类，排最后
    if codecid.isdigit() and len(codecid) == 6:
        if codecid.startswith("10"):
            return {"kind": "video", "codecid": codecid, "rank": 0,
                    "label": f"视频轨 {codecid}(未知)"}
        if codecid.startswith("30"):
            return {"kind": "audio", "codecid": codecid, "rank": 0,
                    "label": f"音频轨 {codecid}(未知)"}
    return None


def _is_real_stream(url: str) -> bool:
    """判断网络捕获到的 URL 是不是真正的视频流（而非推广/静态资源）。"""
    if not url:
        return False
    lu = url.lower()
    if any(pat in lu for pat in _NOISE_PATTERNS):
        return False
    if lu.startswith(("blob:", "data:")):
        return False
    # B 站 m4s 轨道：只认能解析出 codecid 的
    if ".m4s" in lu:
        return bili_track_info(url) is not None
    keys = (
        ".m3u8", ".mpd", "videoplayback", "mime_type=video",
        "douyinvod", "/video/tos/", "media-video", "media-audio",
        "aweme/v1/play", ".mp4",
    )
    return any(k in lu for k in keys)


def select_streams(urls: list[str]) -> dict:
    """从候选地址里选出最佳可下载组合。

    返回 {"video": {...}|None, "audio": {...}|None, "others": [...]}

    规则：
      * B 站 m4s：视频取分辨率最高的一条，音频取码率最高的一条
      * 其它站点（抖音等）：直接取完整流，不需要配对
    """
    video: dict | None = None
    audio: dict | None = None
    others: list[str] = []

    for u in dict.fromkeys(urls):  # 保序去重
        info = bili_track_info(u)
        if info:
            cand = {"url": u, **info}
            if info["kind"] == "video":
                if video is None or info["rank"] > video["rank"]:
                    video = cand
            else:
                if audio is None or info["rank"] > audio["rank"]:
                    audio = cand
        elif _is_real_stream(u):
            others.append(u)

    return {"video": video, "audio": audio, "others": others}


class Api:
    """暴露给控制台前端的全部接口。

    ⚠ 重要约束（改这个类之前必读）：
    pywebview 会把 js_api 对象的所有非下划线属性递归暴露给前端
    （见 webview/util.py 的 get_functions）。它用 getattr 逐个取值，
    遇到"非 callable 且有 __module__"的对象就钻进去递归。
    如果把 WebView2 的 Window 对象挂在公开属性上（self.browser / self.console），
    它会顺着 Window.native → WinForms 窗体 → AccessibilityObject 无限递归，
    报 "AccessibilityObject.Bounds.Empty.Empty..." 并直接把 UI 线程卡死。

    所以：窗口引用一律用下划线私有属性（dir() 遍历时会被跳过）。
    """

    # 双保险：即便将来误加了公开属性，也让 pywebview 跳过本对象
    _serializable = False

    def __init__(self) -> None:
        # 私有：不参与 pywebview 的反射暴露，避免 AccessibilityObject 递归爆栈
        self._browser: webview.Window | None = None
        self._console: webview.Window | None = None
        self._last_result: dict | None = None
        self._cache_state: dict | None = None

    # ---------- 窗口引用 ----------
    def bind_windows(self, console: webview.Window | None) -> None:
        """由 main() 调用，登记控制台窗口。"""
        self._console = console

    # ---------- 浏览器窗管理 ----------
    def ensure_browser(self, url: str = "") -> dict:
        """按需创建浏览器窗口。已存在则直接导航。"""
        if self._browser is not None:
            if url:
                try:
                    self._browser.load_url(url)
                except Exception as exc:  # noqa: BLE001
                    return {"ok": False, "error": str(exc)}
            try:
                self._browser.restore()
            except Exception:  # noqa: BLE001
                pass
            return {"ok": True, "created": False}

        try:
            win = webview.create_window(
                "浏览器 —— 在这里打开你要抓的网页",
                url=url or START_URL,
                width=1180,
                height=860,
                x=120,
                y=40,
                min_size=(700, 500),
            )
            self._browser = win
            return {"ok": True, "created": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def install_netcap(self) -> dict:
        """把网络捕获器注入当前页，用于捞出 blob 背后的真实媒体流。"""
        w, err = self._win()
        if err:
            return err
        if not NETCAP_SOURCE:
            return {"ok": False, "error": "netcap.js 缺失"}
        try:
            r = w.evaluate_js(NETCAP_SOURCE.strip())
            return {"ok": True, "state": str(r)[:80]}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def network_media(self) -> dict:
        """读回捕获到的媒体 URL 清单。"""
        w, err = self._win()
        if err:
            return err
        try:
            raw = w.evaluate_js(
                "(function(){try{return JSON.stringify(window.__crawlerNet?window.__crawlerNet.urls():[])}catch(e){return '[]'}})()"
            )
            if isinstance(raw, str):
                try:
                    items = json.loads(raw)
                except json.JSONDecodeError:
                    items = []
            elif isinstance(raw, list):
                items = raw
            else:
                items = []
            return {"ok": True, "items": items, "count": len(items)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "items": []}

    def _win(self) -> tuple[webview.Window | None, dict | None]:
        if self._browser is None:
            return None, {"ok": False, "error": "浏览器窗口还没打开，先点「打开浏览器」"}
        return self._browser, None

    # ---------- 浏览器控制 ----------
    def navigate(self, url: str) -> dict:
        url = (url or "").strip()
        if not url:
            return {"ok": False, "error": "地址为空"}
        if not url.startswith(("http://", "https://", "file://")):
            if "." in url and " " not in url:
                url = "https://" + url
            else:
                from urllib.parse import quote
                url = "https://www.bing.com/search?q=" + quote(url)
        return self.ensure_browser(url)

    def browser_back(self) -> dict:
        w, err = self._win()
        if err:
            return err
        try:
            w.evaluate_js("history.back();")
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def browser_forward(self) -> dict:
        w, err = self._win()
        if err:
            return err
        try:
            w.evaluate_js("history.forward();")
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def browser_reload(self) -> dict:
        w, err = self._win()
        if err:
            return err
        try:
            w.evaluate_js("location.reload();")
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def open_external(self) -> dict:
        w, err = self._win()
        if err:
            return err
        try:
            url = w.get_current_url()
            if url:
                os.startfile(url)  # noqa: S606 用户主动点击
                return {"ok": True, "url": url}
            return {"ok": False, "error": "当前没有打开的页面"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def browser_status(self) -> dict:
        if self._browser is None:
            return {"ok": False, "error": "浏览器未打开", "opened": False}
        try:
            url = self._browser.get_current_url() or ""
            try:
                title = self._browser.evaluate_js("document.title") or ""
            except Exception:  # noqa: BLE001
                title = ""
            return {"ok": True, "opened": True, "url": url, "title": title}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "opened": True}

    def scroll_to_bottom(self) -> dict:
        """触发懒加载：分步滚到底再回顶。"""
        w, err = self._win()
        if err:
            return err
        try:
            w.evaluate_js(
                """
                (function(){
                  var guard = 0, y = 0;
                  var t = setInterval(function(){
                    y = Math.min(y + Math.max(500, innerHeight), document.body.scrollHeight);
                    window.scrollTo(0, y);
                    if (++guard > 80 || y >= document.body.scrollHeight - innerHeight) {
                      clearInterval(t); window.scrollTo(0, 0);
                    }
                  }, 150);
                })();
                """
            )
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    # ---------- 抓取 ----------
    def _prime_video_streams(self) -> dict:
        """为抓视频做准备：装网络捕获 + 触发播放 + 等分片请求。

        为什么必须这么做：
          抖音对同一站点的视频用两种播放方式 ——
            a) <video src="https://...douyinvod.com/...">  直链，extract.js 直接能读
            b) <video src="blob:...">                      MSE 分片，真实流只在网络里
          方式 b 下 extract.js 只能拿到 blob:，必须靠 netcap.js 监听网络请求。
          而且只有真的开始播放，浏览器才会去拉分片。
        """
        result = {"installed": False, "played": "", "networkCount": 0}
        w, err = self._win()
        if err:
            return result

        # 1) 装监听（SPA 软跳转后可能重复注入，netcap.js 自带幂等判断）
        if NETCAP_SOURCE:
            try:
                w.evaluate_js(NETCAP_SOURCE.strip())
                result["installed"] = True
            except Exception:  # noqa: BLE001
                pass

        # 2) 触发播放：让 MSE 去请求分片
        try:
            played = w.evaluate_js(
                """
                (function(){
                  try {
                    var v = document.querySelector('video');
                    if (v) {
                      v.muted = true;
                      if (v.paused && v.play) { v.play().catch(function(){}); return 'play'; }
                      return v.currentSrc && v.currentSrc.indexOf('blob:') === 0 ? 'already-blob' : 'already';
                    }
                    var btn = document.querySelector('xg-icon[data-e2e="play"], .xgplayer-play, [class*="play"]');
                    if (btn && btn.click) { btn.click(); return 'clicked'; }
                    return 'none';
                  } catch(e) { return 'err:' + e.message; }
                })()
                """
            )
            result["played"] = str(played)
        except Exception:  # noqa: BLE001
            pass

        # 3) 给它一点时间去拉分片
        time.sleep(1.2)

        # 4) 读回捕获到的媒体
        net = self.network_media()
        result["networkCount"] = len(net.get("items") or [])
        return result

    def crawl(self, kind: str) -> dict:
        # 先校验参数，再检查窗口状态：否则非法 kind 会被误报成"窗口没开"
        src = EXTRACT_SOURCE.strip()
        if kind == "text-selection":
            call, real_kind = "(%s)('selection')" % src, "text"
        elif kind == "all":
            call, real_kind = "(%s)('all')" % src, "all"
        elif kind in ("video", "image", "text"):
            call, real_kind = "(%s)(%s)" % (src, json.dumps(kind)), kind
        else:
            return {"ok": False, "error": f"未知抓取类型: {kind}"}

        w, err = self._win()
        if err:
            return err

        # 抓视频（或全部）前先布好网络捕获，否则 blob: 播放器的真实流抓不到
        prime: dict = {}
        if real_kind in ("video", "all"):
            prime = self._prime_video_streams()

        try:
            raw = w.evaluate_js(call)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"注入失败: {type(exc).__name__}: {exc}"}

        if raw is None:
            return {"ok": False, "error": "页面无返回。可能还在加载，或当前页禁止脚本注入（PDF / 特殊 CSP）。"}
        if isinstance(raw, str):
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                return {"ok": False, "error": "返回值无法解析: " + raw[:300]}
        elif isinstance(raw, dict):
            data = raw
        else:
            return {"ok": False, "error": f"未知返回类型 {type(raw).__name__}"}

        if not data.get("ok"):
            return {"ok": False, "error": data.get("error") or "提取失败"}

        # 把网络层捕获到的真实媒体流合进结果
        net_items: list = []
        if real_kind in ("video", "all"):
            net = self.network_media()
            net_items = net.get("items") or []
            data["networkMedia"] = net_items
            data["primeInfo"] = prime

            # 用真实流替换/补全 blob: 条目，让导出直接可用
            if real_kind == "video":
                items = data.get("items") or []
                real = [u["url"] for u in net_items
                        if isinstance(u, dict) and _is_real_stream(u.get("url", ""))]
                if real:
                    # B 站是 DASH 双轨：只保留最佳的一条视频轨和一条音频轨，
                    # 避免把 14 条分片全塞进结果（用户根本用不上）。
                    picked = select_streams(real)

                    kept = [it for it in items if not str(it.get("url", "")).startswith("blob:")]
                    existing = {it.get("url") for it in kept}
                    title = data.get("meta", {}).get("title", "")

                    if picked["video"] or picked["audio"]:
                        # 有 DASH 轨道：按轨道写入，带清晰度标注
                        data["dashTracks"] = {
                            "video": picked["video"],
                            "audio": picked["audio"],
                        }
                        for track in (picked["video"], picked["audio"]):
                            if not track:
                                continue
                            u = track["url"]
                            if u in existing:
                                continue
                            kept.append({
                                "url": u,
                                "title": f"{title} [{track['kind']}:{track['label']}]",
                                "poster": "", "duration": None,
                                "width": 0, "height": 0,
                                "mime": track["kind"],
                                "source": "network-dash",
                            })
                            existing.add(u)

                    # 非 DASH 的完整流（抖音等）照常追加
                    for u in picked["others"]:
                        if u in existing:
                            continue
                        kept.append({"url": u, "title": title,
                                     "poster": "", "duration": None, "width": 0, "height": 0,
                                     "mime": "", "source": "network"})
                        existing.add(u)

                    data["items"] = kept
                    data["count"] = len(kept)

        # 统一清掉站点自带的客户端推广视频（预览和导出用同一份数据）
        if real_kind == "video":
            clean = [it for it in (data.get("items") or [])
                     if not any(p in str(it.get("url", "")).lower() for p in _NOISE_PATTERNS)]
            if len(clean) != len(data.get("items") or []):
                data["items"] = clean
                data["count"] = len(clean)

        data["_kind"] = real_kind
        data["_ts"] = _timestamp()
        self._last_result = data

        items = data.get("items") or []
        return {
            "ok": True,
            "kind": real_kind,
            "count": data.get("count", len(items)),
            "meta": data.get("meta", {}),
            "preview": items[:200],
            "textChars": data.get("chars", 0) if real_kind == "text" else 0,
            "textHead": (data.get("plain") or "")[:300] if real_kind == "text" else "",
            "videoCount": len(data.get("video") or []) if real_kind == "all" else 0,
            "imageCount": len(data.get("image") or []) if real_kind == "all" else 0,
        }

    # ---------- 导出 ----------
    def export(self) -> dict:
        payload = self._last_result
        if not payload:
            return {"ok": False, "error": "还没有抓取结果，先点一次抓取"}

        kind = payload.get("_kind") or payload.get("kind") or "result"
        meta = payload.get("meta") or {}
        title = meta.get("title") or "无标题"

        try:
            DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return {"ok": False, "error": f"无法创建输出目录: {exc}"}

        stem = f"{kind}_{_safe_name(title)}_{_timestamp()}"
        written: list[str] = []

        try:
            if kind == "video":
                items = payload.get("items") or []
                # 过滤站点客户端推广视频，避免混进结果
                items = [it for it in items if not any(
                    p in str(it.get("url", "")).lower() for p in _NOISE_PATTERNS)]

                path = DEFAULT_OUTPUT_DIR / f"{stem}.csv"
                with path.open("w", newline="", encoding="utf-8-sig") as fh:
                    w = csv.writer(fh)
                    w.writerow(["序号", "URL", "标题", "封面", "时长秒", "宽", "高", "来源"])
                    for i, it in enumerate(items, 1):
                        w.writerow([i, it.get("url", ""), it.get("title", ""), it.get("poster", ""),
                                    it.get("duration") if it.get("duration") is not None else "",
                                    it.get("width", ""), it.get("height", ""), it.get("source", "")])
                written.append(str(path))

                # 可直接下载的地址（去掉 blob:，它们单独标注）
                up = DEFAULT_OUTPUT_DIR / f"{stem}_urls.txt"
                usable = [x.get("url", "") for x in items
                          if x.get("url") and not str(x.get("url")).startswith("blob:")]
                up.write_text("\n".join(usable), encoding="utf-8")
                written.append(str(up))

                # 网络捕获到的真实流（MSE / DASH 场景下这是唯一可用的地址）
                net = payload.get("networkMedia") or []
                all_urls = [(x.get("url") or "") if isinstance(x, dict) else str(x) for x in net]
                picked = select_streams([u for u in all_urls if u])
                lines: list[str] = []
                if picked["video"]:
                    lines.append("# " + picked["video"]["label"])
                    lines.append(picked["video"]["url"])
                if picked["audio"]:
                    lines.append("# " + picked["audio"]["label"])
                    lines.append(picked["audio"]["url"])
                for u in picked["others"]:
                    lines.append(u)
                if lines:
                    sp = DEFAULT_OUTPUT_DIR / f"{stem}_视频流.txt"
                    page = (payload.get("meta") or {}).get("url") or ""
                    # 文件头写明必须带的请求头，否则用户直接粘链接会 403
                    header = [
                        "# 下载这些地址必须同时带 User-Agent 和 Referer，缺一个就是 403",
                        f"# 页面: {page}" if page else "# 页面: (未知)",
                        "#   Referer(B站): https://www.bilibili.com/",
                        "#   Referer(抖音): https://www.douyin.com/",
                        "# 用浏览器直接粘贴/下载器会在 Referer 上翻车，必须走脚本：",
                        "#   python fetch_media.py \"<本文件路径>\"",
                    ]
                    sp.write_text("\n".join(header + lines), encoding="utf-8")
                    written.append(str(sp))

            elif kind == "image":
                items = payload.get("items") or []
                path = DEFAULT_OUTPUT_DIR / f"{stem}.csv"
                with path.open("w", newline="", encoding="utf-8-sig") as fh:
                    w = csv.writer(fh)
                    w.writerow(["序号", "URL", "alt", "宽", "高", "可见", "懒加载", "来源"])
                    for i, it in enumerate(items, 1):
                        w.writerow([i, it.get("url", ""), it.get("alt", ""), it.get("width", ""),
                                    it.get("height", ""), "是" if it.get("visible") else "否",
                                    "是" if it.get("lazy") else "否", it.get("source", "")])
                written.append(str(path))
                up = DEFAULT_OUTPUT_DIR / f"{stem}_urls.txt"
                up.write_text("\n".join(x.get("url", "") for x in items if x.get("url")), encoding="utf-8")
                written.append(str(up))

            elif kind == "text":
                plain = payload.get("plain") or ""
                path = DEFAULT_OUTPUT_DIR / f"{stem}.txt"
                head = [f"标题: {meta.get('title','')}", f"地址: {meta.get('url','')}",
                        f"抓取时间: {meta.get('collectedAt','')}", f"字数: {payload.get('chars', len(plain))}",
                        "-" * 60, ""]
                path.write_text("\n".join(head) + plain, encoding="utf-8")
                written.append(str(path))

            else:  # all
                path = DEFAULT_OUTPUT_DIR / f"{stem}.json"
                path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                written.append(str(path))
                t = payload.get("text") or {}
                if t.get("plain"):
                    tp = DEFAULT_OUTPUT_DIR / f"{stem}_正文.txt"
                    tp.write_text(t["plain"], encoding="utf-8")
                    written.append(str(tp))
                for sub, label in (("video", "视频"), ("image", "图片")):
                    arr = payload.get(sub) or []
                    if not arr:
                        continue
                    urls = [x.get("url", "") for x in arr
                            if x.get("url") and not str(x.get("url")).startswith("blob:")]
                    # 视频清单同样滤掉客户端推广
                    if sub == "video":
                        urls = [u for u in urls
                                if not any(p in u.lower() for p in _NOISE_PATTERNS)]
                    if urls:
                        up = DEFAULT_OUTPUT_DIR / f"{stem}_{label}链接.txt"
                        up.write_text("\n".join(urls), encoding="utf-8")
                        written.append(str(up))

                # 网络层捕获：blob 播放器背后的真实媒体流，通常是全页最有价值的一份
                net = payload.get("networkMedia") or []
                all_urls = [(x.get("url") or "") if isinstance(x, dict) else str(x) for x in net]
                picked = select_streams([u for u in all_urls if u])
                lines2: list[str] = []
                if picked["video"]:
                    lines2.append("# " + picked["video"]["label"])
                    lines2.append(picked["video"]["url"])
                if picked["audio"]:
                    lines2.append("# " + picked["audio"]["label"])
                    lines2.append(picked["audio"]["url"])
                for u in picked["others"]:
                    lines2.append(u)
                if lines2:
                    sp = DEFAULT_OUTPUT_DIR / f"{stem}_视频流.txt"
                    page2 = (payload.get("meta") or {}).get("url") or ""
                    header2 = [
                        "# 下载这些地址必须同时带 User-Agent 和 Referer，缺一个就是 403",
                        f"# 页面: {page2}" if page2 else "# 页面: (未知)",
                        "#   Referer(B站): https://www.bilibili.com/",
                        "#   Referer(抖音): https://www.douyin.com/",
                        "# 用浏览器直接粘贴/下载器会在 Referer 上翻车，必须走脚本：",
                        "#   python fetch_media.py \"<本文件路径>\"",
                    ]
                    sp.write_text("\n".join(header2 + lines2), encoding="utf-8")
                    written.append(str(sp))
        except OSError as exc:
            return {"ok": False, "error": f"写入失败: {exc}"}

        return {"ok": True, "files": written, "dir": str(DEFAULT_OUTPUT_DIR)}

    def open_output_dir(self) -> dict:
        try:
            DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(str(DEFAULT_OUTPUT_DIR))  # noqa: S606
            return {"ok": True, "dir": str(DEFAULT_OUTPUT_DIR)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    # ---------- 缓存清理 ----------
    def clear_cache(self, mode: str = "cache") -> dict:
        """清理浏览器缓存。

        mode:
          "cache"  —— 只清媒体/磁盘缓存与 CacheStorage，保留登录状态（cookie）
          "all"    —— 连 cookie、localStorage、IndexedDB 一起清，会退出登录

        线程模型（踩坑记录）：
          pywebview 把 js_api 调用放在**单独线程**执行（webview/util.py 的
          js_bridge_call），而 CoreWebView2 只能在 UI 线程访问，直接取
          core.Profile 会抛 "CoreWebView2 can only be accessed from the UI thread"。
          所以这里必须用 WinForms 控件的 Invoke 把自己弹回 UI 线程。
        """
        w, err = self._win()
        if err:
            return err

        if mode not in ("cache", "all"):
            return {"ok": False, "error": f"未知清理模式: {mode}"}

        try:
            import clr
            from webview.util import interop_dll_path
            clr.AddReference(interop_dll_path("Microsoft.Web.WebView2.Core.dll"))
            from Microsoft.Web.WebView2.Core import CoreWebView2BrowsingDataKinds as K
            from System import Action, Func, Object
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"加载 WebView2 接口失败: {exc}"}

        try:
            native = w.native
            ctrl = native.webview  # WinForms 的 WebView2 控件
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"取 WebView2 控件失败: {exc}"}

        if mode == "cache":
            # 只清缓存类：不动 cookie，登录状态保留
            kinds = K.DiskCache | K.CacheStorage | K.BrowsingHistory | K.DownloadHistory
        else:
            kinds = K.AllProfile

        self._cache_state = {"status": "running", "mode": mode}

        # 这个函数会在 UI 线程里执行
        def on_ui_thread() -> None:
            try:
                core = ctrl.CoreWebView2
                if core is None:
                    self._cache_state = {"status": "error", "mode": mode,
                                         "error": "WebView2 尚未就绪"}
                    return
                profile = core.Profile

                # 关键：绝不能在这里 Wait()。
                # ClearBrowsingDataAsync 的续体要在 UI 线程跑，而我们现在就在
                # UI 线程上同步等它 → 自己等自己，永久死锁（实测卡死）。
                # 正确做法：发出去就走，结果由前端轮询 + 超时判定。
                profile.ClearBrowsingDataAsync(kinds)
                self._cache_state = {
                    "status": "issued",
                    "mode": mode,
                    "issuedAt": time.time(),
                }
            except Exception as exc:  # noqa: BLE001
                self._cache_state = {"status": "error", "mode": mode,
                                     "error": f"{type(exc).__name__}: {exc}"}

        try:
            # 弹回 UI 线程执行
            from System import Action as SysAction
            ctrl.Invoke(SysAction(on_ui_thread))
            # 状态以 on_ui_thread 实际写入的为准
            cur = getattr(self, "_cache_state", None) or {}
            if cur.get("status") == "error":
                return {"ok": False, "error": cur.get("error", "清理失败"), "mode": mode}
            return {"ok": True, "status": "running", "mode": mode}
        except Exception as exc:  # noqa: BLE001
            self._cache_state = {"status": "error", "mode": mode,
                                 "error": f"Invoke 失败: {exc}"}
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def cache_status(self) -> dict:
        """查询清理进度。前端轮询用。

        ClearBrowsingDataAsync 的 Task 不能在 UI 线程等待（会死锁），
        所以这里用"发出时间 + 静默时长"判定完成：超过 1.2 秒没有报错
        就认为清理已生效。清理本身是幂等且无返回值的，这个判定足够可靠。
        """
        st = getattr(self, "_cache_state", None)
        if not st:
            return {"ok": True, "status": "idle"}

        if st.get("status") == "issued":
            elapsed = time.time() - (st.get("issuedAt") or 0)
            if elapsed >= 1.2:
                self._cache_state = {"status": "done", "mode": st.get("mode")}
                return {"ok": True, **self._cache_state}
            return {"ok": True, "status": "running", "mode": st.get("mode")}

        return {"ok": True, **st}

    def clear_outputs(self) -> dict:
        """删除「网页抓取结果」目录下的导出文件。"""
        if not DEFAULT_OUTPUT_DIR.exists():
            return {"ok": True, "removed": 0, "dir": str(DEFAULT_OUTPUT_DIR), "note": "目录不存在"}

        removed = 0
        failed: list[str] = []
        try:
            for item in DEFAULT_OUTPUT_DIR.iterdir():
                try:
                    if item.is_dir():
                        import shutil
                        shutil.rmtree(item)
                    else:
                        item.unlink()
                    removed += 1
                except OSError as exc:
                    failed.append(f"{item.name}: {exc}")
        except OSError as exc:
            return {"ok": False, "error": f"读取目录失败: {exc}"}

        return {
            "ok": True,
            "removed": removed,
            "failed": failed,
            "dir": str(DEFAULT_OUTPUT_DIR),
        }

    def output_size(self) -> dict:
        """统计导出目录占用空间，供界面显示。"""
        if not DEFAULT_OUTPUT_DIR.exists():
            return {"ok": True, "bytes": 0, "files": 0}
        total = 0
        files = 0
        try:
            for item in DEFAULT_OUTPUT_DIR.rglob("*"):
                if item.is_file():
                    total += item.stat().st_size
                    files += 1
        except OSError:
            pass
        return {"ok": True, "bytes": total, "files": files}

    def quit_app(self) -> None:
        for w in (self._browser, self._console):
            try:
                if w is not None:
                    w.destroy()
            except Exception:  # noqa: BLE001
                pass


def main() -> int:
    # 关键：不要让网页里 target="_blank" / window.open 的链接跳到系统浏览器。
    # pywebview 默认 OPEN_EXTERNAL_LINKS_IN_BROWSER=True，会把新窗口请求交给
    # webbrowser.open()，于是点搜索结果就弹到 Edge/Chrome 去了。
    # 设为 False 后，新窗口请求会在当前 WebView2 里直接导航，保持单窗口体验。
    # （util.ImmutableDict 允许修改已有键，所以这样赋值是官方支持的用法。）
    webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = False

    # 命令行模式：exe --url <网址> [--kind video] [--wait 5] [--json out.json]
    # 不开界面交互，抓完直接打印结果并落盘。
    if any(a in sys.argv for a in ("--url", "-u")) or "--cli" in sys.argv:
        import grab_url
        return grab_url.run(sys.argv[1:])

    api = Api()

    console = webview.create_window(
        APP_TITLE,
        url=_ui_uri("console.html"),
        js_api=api,
        width=1000,
        height=780,
        min_size=(840, 640),
        background_color="#0f1115",
    )
    api.bind_windows(console)

    # 关键：浏览器窗口在 GUI 启动后再创建（见文件头架构决策 3）。
    def boot() -> None:
        api.ensure_browser(START_URL)

    webview.start(func=boot, gui="edgechromium", private_mode=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

