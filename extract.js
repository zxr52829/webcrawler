/*
 * 页面内容提取器 —— 注入到 WebView2 当前页面执行。
 * 所有结果统一返回 {ok, kind, items, meta} 结构，由 Python 侧落到界面/文件。
 * 关键约束：绝不抛异常到宿主，任何分支失败都必须返回可读的错误信息。
 */
(function (kind) {
  "use strict";

  // ---------- 通用工具 ----------
  function abs(u) {
    if (!u) return "";
    try { return new URL(u, location.href).href; } catch (e) { return String(u); }
  }

  function txt(el) {
    if (!el) return "";
    return (el.innerText || el.textContent || "").replace(/\u00a0/g, " ").trim();
  }

  function isVisible(el) {
    if (!el) return false;
    var r = el.getBoundingClientRect();
    if (r.width < 8 || r.height < 8) return false;
    var s = getComputedStyle(el);
    if (s.display === "none" || s.visibility === "hidden" || parseFloat(s.opacity) === 0) return false;
    return true;
  }

  function uniqBy(list, key) {
    var seen = Object.create(null), out = [];
    for (var i = 0; i < list.length; i++) {
      var k = list[i][key];
      if (!k || seen[k]) continue;
      seen[k] = 1;
      out.push(list[i]);
    }
    return out;
  }

  // 从任意 URL 猜媒体类型
  function guessMedia(url, mimeHint) {
    var m = (mimeHint || "").toLowerCase();
    var path = "";
    try { path = new URL(url, location.href).pathname.toLowerCase(); } catch (e) { path = String(url).toLowerCase(); }
    if (m.indexOf("video") === 0) return "video";
    if (m.indexOf("audio") === 0) return "audio";
    if (m.indexOf("image") === 0) return "image";
    if (/\.(m3u8|mpd)$/.test(path)) return "video";
    if (/\.(mp4|webm|ogv|mov|m4v|flv|mkv|ts)$/.test(path)) return "video";
    if (/\.(mp3|m4a|aac|wav|ogg|flac|opus)$/.test(path)) return "audio";
    if (/\.(jpg|jpeg|png|gif|webp|avif|bmp|svg|ico)$/.test(path)) return "image";
    return "other";
  }

  // ---------- 视频 ----------
  function collectVideos() {
    var out = [];

    // 1. <video> 标签及其 source 子节点
    var vids = document.querySelectorAll("video");
    for (var i = 0; i < vids.length; i++) {
      var v = vids[i];
      var poster = v.getAttribute("poster");
      var sources = v.querySelectorAll("source");
      var srcs = [];
      if (v.getAttribute("src")) srcs.push(v.getAttribute("src"));
      if (v.currentSrc) srcs.push(v.currentSrc);
      for (var j = 0; j < sources.length; j++) {
        if (sources[j].getAttribute("src")) srcs.push(sources[j].getAttribute("src"));
      }
      for (var k = 0; k < srcs.length; k++) {
        out.push({
          url: abs(srcs[k]),
          title: txt(v.getAttribute("title") ? v : (v.closest("figure") || v.parentElement)) || document.title,
          poster: poster ? abs(poster) : "",
          duration: isFinite(v.duration) ? Math.round(v.duration) : null,
          width: v.videoWidth || v.clientWidth || 0,
          height: v.videoHeight || v.clientHeight || 0,
          mime: v.getAttribute("type") || "",
          source: "video-tag"
        });
      }
    }

    // 2. 页面内联脚本里的直链（覆盖 iframe 播放器场景）
    var re = /(?:https?:)?\/\/[^\s"'\\<>()]+?\.(?:mp4|m3u8|webm|mov|m4v|mpd|flv)(?:\?[^\s"'\\<>()]*)?/gi;
    var scripts = document.querySelectorAll("script:not([src])");
    for (var s = 0; s < scripts.length && s < 200; s++) {
      var body = scripts[s].textContent || "";
      if (body.length > 400000) body = body.slice(0, 400000);
      var m;
      while ((m = re.exec(body)) !== null) {
        out.push({ url: abs(m[0]), title: document.title, poster: "", duration: null, width: 0, height: 0, mime: "", source: "inline-script" });
        if (out.length > 800) break;
      }
    }

    // 3. a 标签指向的媒体直链
    var links = document.querySelectorAll("a[href]");
    for (var l = 0; l < links.length; l++) {
      var href = links[l].getAttribute("href") || "";
      if (/\.(mp4|m3u8|webm|mov|m4v|mpd)(\?|$)/i.test(href)) {
        out.push({ url: abs(href), title: txt(links[l]) || document.title, poster: "", duration: null, width: 0, height: 0, mime: "", source: "link" });
      }
    }

    return uniqBy(out, "url");
  }

  // ---------- 图片 ----------
  function collectImages() {
    var out = [];
    var imgs = document.querySelectorAll("img");
    for (var i = 0; i < imgs.length; i++) {
      var im = imgs[i];

      // 懒加载常见属性：data-* 优先于已污染成占位图的 src
      var lazyAttrs = ["data-src", "data-original", "data-lazy-src", "data-echo", "data-url"];
      var picked = "";

      for (var c = 0; c < lazyAttrs.length; c++) {
        var v = im.getAttribute(lazyAttrs[c]);
        if (v && !/^data:/i.test(v)) { picked = v; break; }
      }

      // srcset 单独处理：取描述符里最大的那张，而不是列表里最后一个
      var srcset = im.getAttribute("srcset") || im.getAttribute("data-srcset");
      if (srcset) {
        var best = "", bestScore = -1;
        var parts = srcset.split(",");
        for (var p = 0; p < parts.length; p++) {
          var seg = parts[p].trim();
          if (!seg) continue;
          var bits = seg.split(/\s+/);
          var u2 = bits[0];
          if (!u2 || /^data:/i.test(u2)) continue;
          var score = 1;
          if (bits[1]) {
            var wm = /^(\d+)w$/.exec(bits[1]);
            var xm = /^([\d.]+)x$/.exec(bits[1]);
            if (wm) score = parseInt(wm[1], 10);
            else if (xm) score = parseFloat(xm[1]) * 1000;
          }
          if (score > bestScore) { bestScore = score; best = u2; }
        }
        // 有 srcset 时，它通常比 src 分辨率更高，优先采用
        if (best) picked = best;
      }

      // 都拿不到才退回 src / currentSrc
      if (!picked) {
        var plain = im.currentSrc || im.getAttribute("src") || "";
        if (plain && !/^data:/i.test(plain)) picked = plain;
      }

      if (!picked) continue;

      var isLazy = !im.currentSrc || im.currentSrc.indexOf("data:") === 0;
      out.push({
        url: abs(picked),
        alt: im.getAttribute("alt") || "",
        width: im.naturalWidth || im.clientWidth || parseInt(im.getAttribute("width") || "0", 10) || 0,
        height: im.naturalHeight || im.clientHeight || parseInt(im.getAttribute("height") || "0", 10) || 0,
        visible: isVisible(im),
        lazy: isLazy,
        source: "img-tag"
      });
    }

    // CSS 背景图（不少站点图片走 background-image）
    var walker = document.querySelectorAll("*");
    var bgRe = /url\((['"]?)([^'")]+)\1\)/i;
    for (var w = 0; w < walker.length && w < 4000; w++) {
      var el = walker[w];
      var bg = getComputedStyle(el).backgroundImage;
      if (!bg || bg === "none") continue;
      var bm = bgRe.exec(bg);
      if (!bm) continue;
      var u = bm[2];
      if (/^data:/i.test(u)) continue;
      out.push({ url: abs(u), alt: "", width: 0, height: 0, visible: isVisible(el), lazy: false, source: "css-bg" });
    }

    // meta og:image
    var og = document.querySelector('meta[property="og:image"], meta[name="og:image"]');
    if (og && og.content) {
      out.push({ url: abs(og.content), alt: "og:image", width: 0, height: 0, visible: true, lazy: false, source: "meta-og" });
    }

    return uniqBy(out, "url");
  }

  // ---------- 文字 ----------
  function collectText(mode) {
    var root = document.querySelector("article") ||
               document.querySelector("main") ||
               document.querySelector('[role="main"]') ||
               document.body;

    var container = root;
    if (mode === "selection") {
      var sel = window.getSelection ? String(window.getSelection()) : "";
      if (sel && sel.trim()) {
        return {
          kind: "text",
          title: document.title,
          blocks: [{ tag: "selection", text: sel.trim() }],
          plain: sel.trim(),
          chars: sel.trim().length
        };
      }
      // 没选中就退回正文
    }

    var blocks = [];
    var tags = "h1,h2,h3,h4,h5,h6,p,li,blockquote,pre,td,th,dd,dt,figcaption";
    var nodes = container.querySelectorAll(tags);
    var acc = [];
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      // 跳过嵌套表格/列表里的重复
      if (n.closest("pre") && n.tagName.toLowerCase() !== "pre") continue;
      var t = txt(n);
      if (!t || t.length < 2) continue;
      if (n.tagName.toLowerCase() === "li" && n.querySelector("li")) continue;
      blocks.push({ tag: n.tagName.toLowerCase(), text: t });
      acc.push(t);
      if (blocks.length >= 3000) break;
    }

    // 兜底：整页可见文本
    if (acc.length === 0) {
      var whole = txt(container) || txt(document.body);
      if (whole) blocks.push({ tag: "body", text: whole });
      acc.push(whole);
    }

    var plain = acc.join("\n");
    return {
      kind: "text",
      title: document.title,
      url: location.href,
      blocks: blocks,
      plain: plain,
      chars: plain.length,
      words: (plain.match(/[\u4e00-\u9fa5]/g) || []).length + (plain.match(/[A-Za-z0-9_]+/g) || []).length
    };
  }

  // ---------- 主分派 ----------
  try {
    var meta = {
      url: location.href,
      title: document.title,
      domain: location.hostname,
      collectedAt: new Date().toISOString(),
      readyState: document.readyState
    };

    if (kind === "video") {
      var vs = collectVideos();
      return { ok: true, kind: "video", items: vs, meta: meta, count: vs.length };
    }
    if (kind === "image") {
      var is = collectImages();
      return { ok: true, kind: "image", items: is, meta: meta, count: is.length };
    }
    if (kind === "text" || kind === "selection") {
      var t = collectText(kind);
      t.meta = meta;
      t.ok = true;
      t.count = t.blocks.length;
      return t;
    }
    if (kind === "all") {
      var all = {
        ok: true,
        kind: "all",
        meta: meta,
        video: collectVideos(),
        image: collectImages(),
        text: collectText("article")
      };
      all.count = all.video.length + all.image.length + all.text.blocks.length;
      return all;
    }

    // 未知类型：明确报错，不要静默返回 undefined
    return {
      ok: false,
      kind: String(kind),
      error: "未知抓取类型: " + kind,
      meta: meta
    };
  } catch (e) {
    return { ok: false, kind: kind, error: String((e && e.stack) || e), meta: { url: location.href, title: document.title } };
  }
})