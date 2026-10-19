/*
 * 媒体网络捕获器 —— 补 extract.js 的短板。
 *
 * 为什么需要它：
 *   抖音/B站这类站点用 MSE 播放，<video src> 是 blob:xxx，无法直接下载。
 *   真实视频流藏在网络请求里（.m3u8 / .mp4 / videoplayback 等）。
 *
 * 做法：
 *   1. 包一层 window.fetch 和 XMLHttpRequest，把 URL 记进环形缓冲
 *   2. 装 PerformanceObserver，从 resource timing 里捞出所有已加载资源
 *      这一路覆盖"在我装监听之前就已经发出的请求"
 *   3. 提供一个读取接口，Python 侧调 evaluate_js 把这些 URL 取回来
 *
 * 所有数据挂在 window.__crawlerNet 上，不污染页面其它状态。
 */
(function () {
  "use strict";
  if (window.__crawlerNetInstalled) return "already";
  window.__crawlerNetInstalled = true;

  var CAP = 1200;              // 环形缓冲上限，防止长页面吃内存
  var seen = Object.create(null);
  var list = [];               // [{url, type, method, status, ts}]

  window.__crawlerNet = {
    version: 1,
    urls: function () { return list.slice(); },
    clear: function () { list.length = 0; seen = Object.create(null); },
    count: function () { return list.length; }
  };

  // 只记可能是媒体的 URL，避免把几百个 js/css 全塞进来
  var MEDIA_RE = /\.(m3u8|mpd|mp4|m4s|webm|mov|m4v|flv|ts|mp3|m4a|aac|opus|jpg|jpeg|png|webp|gif|avif)(\?|#|$)/i;
  var STREAM_HINT_RE = /(videoplayback|video\/tos|aweme\/v1\/play|\/play\/|mime_type=video|hls|dash)/i;

  function push(url, meta) {
    if (!url || typeof url !== "string") return;
    if (url.indexOf("blob:") === 0 || url.indexOf("data:") === 0) return;
    var abs;
    try { abs = new URL(url, location.href).href; } catch (e) { return; }
    if (!MEDIA_RE.test(abs) && !STREAM_HINT_RE.test(abs)) return;
    var key = abs.split("#")[0];
    if (seen[key]) return;
    seen[key] = 1;
    list.push({
      url: abs,
      type: (meta && meta.type) || "",
      method: (meta && meta.method) || "",
      status: (meta && meta.status) || 0,
      ts: Date.now()
    });
    if (list.length > CAP) list.shift();
  }

  /* ---------- 1. 包 fetch ---------- */
  try {
    var rawFetch = window.fetch;
    if (typeof rawFetch === "function") {
      window.fetch = function (input, init) {
        var u = "";
        try {
          u = typeof input === "string" ? input : (input && input.url) || "";
        } catch (e) { u = ""; }
        var method = (init && init.method) || "GET";
        var p = rawFetch.apply(this, arguments);
        push(u, { method: method, type: "fetch" });
        // 流式响应头到手后再记一次（有些流是 206/分片）
        try {
          p.then(function (res) {
            if (res && res.url) push(res.url, { method: method, type: res.type || "fetch", status: res.status });
          }).catch(function () {});
        } catch (e) {}
        return p;
      };
    }
  } catch (e) {}

  /* ---------- 2. 包 XHR ---------- */
  try {
    var openDesc = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function (method, url) {
      try { push(url, { method: method, type: "xhr" }); } catch (e) {}
      return openDesc.apply(this, arguments);
    };
    var sendDesc = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function () {
      var xhr = this;
      try {
        xhr.addEventListener("loadend", function () {
          push(xhr.responseURL, { method: "xhr", type: "xhr", status: xhr.status });
        });
      } catch (e) {}
      return sendDesc.apply(this, arguments);
    };
  } catch (e) {}

  /* ---------- 3. PerformanceObserver：捞历史资源 ---------- */
  try {
    function harvest() {
      var entries = performance.getEntriesByType("resource") || [];
      for (var i = 0; i < entries.length; i++) {
        var en = entries[i];
        if (!en || !en.name) continue;
        push(en.name, { type: en.initiatorType || "resource" });
      }
    }
    harvest();
    if (typeof PerformanceObserver === "function") {
      var po = new PerformanceObserver(function (l) {
        var es = l.getEntries() || [];
        for (var i = 0; i < es.length; i++) {
          if (es[i] && es[i].name) push(es[i].name, { type: es[i].initiatorType || "resource" });
        }
      });
      try { po.observe({ entryTypes: ["resource"] }); } catch (e) {}
    }
    // 定期补扫，应付异步分片
    setInterval(harvest, 2000);
  } catch (e) {}

  return "installed:" + list.length;
})()
