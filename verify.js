/**
 * 离线验证 extract.js 的提取逻辑。
 * 用 jsdom 载入 testpage.html，注入 extract.js，跑 video/image/text/all 四种类型，
 * 断言关键条目都能被抓到。这样不用开 GUI 就能确认核心逻辑没写错。
 */
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const dir = __dirname;
const html = fs.readFileSync(path.join(dir, "testpage.html"), "utf8");
const extractSrc = fs.readFileSync(path.join(dir, "extract.js"), "utf8");

// jsdom 里补几个 extract.js 依赖的环境
const dom = new JSDOM(html, {
  url: "https://test.local/testpage.html",
  runScripts: "dangerously",
  pretendToBeVisual: true,
});
const { window } = dom;

// jsdom 不实现这些，给个最小桩，够 extract.js 跑通
window.HTMLElement.prototype.getBoundingClientRect = function () {
  return { width: 240, height: 160, top: 0, left: 0, right: 240, bottom: 160 };
};

const need = `
  var document = window.document;
  var location = window.location;
  var getComputedStyle = window.getComputedStyle.bind(window);
  var innerHeight = 900;
  var URL = window.URL;
  var isFinite = window.isFinite;
  var Date = window.Date;
  var Object = window.Object;
  var Math = window.Math;
  var String = window.String;
  var parseInt = window.parseInt;
  var parseFloat = window.parseFloat;
`;

// extract.js 是一个包裹在括号里的函数表达式 "(function(kind){...})"。
// 注意：不能写成 "return " + 源码，因为源码开头的 /* 注释块会把 return 语句吞掉。
// 正确做法是在沙箱里先赋值给变量，再取出来。
const sandbox = new Function(
  "window",
  need +
    "\nvar __fn = " + extractSrc.trim() + ";\n" +
    "return __fn;"
);

let fn;
try {
  fn = sandbox(window);
} catch (e) {
  console.error("[FATAL] extract.js 求值失败:", e.message);
  process.exit(1);
}

if (typeof fn !== "function") {
  console.error("[FATAL] extract.js 没有返回函数，实际类型:", typeof fn);
  process.exit(1);
}
console.log("[OK] extract.js 语法通过，已返回可调用函数\n");

let failed = 0;
function check(label, cond, detail) {
  if (cond) {
    console.log(`  ✓ ${label}`);
  } else {
    console.log(`  ✗ ${label}  ${detail || ""}`);
    failed++;
  }
}

/* ---------- 视频 ---------- */
console.log("[1] 视频提取");
const v = fn("video");
check("ok=true", v.ok === true, JSON.stringify(v).slice(0, 200));
check("抓到条目", (v.items || []).length > 0, `count=${(v.items || []).length}`);
const vurls = (v.items || []).map(x => x.url);
check("抓到 mov_bbb.mp4", vurls.some(u => u.includes("mov_bbb.mp4")), vurls.join(" | "));
check("抓到 webm source", vurls.some(u => u.includes("mov_bbb.webm")));
check("抓到 flower.mp4", vurls.some(u => u.includes("flower.mp4")));
check("从内联脚本抓到 m3u8",
  vurls.some(u => u.includes("stream_720p.m3u8")),
  vurls.filter(u => u.includes("m3u8")).join(" | "));
check("从内联脚本抓到 mp4 直链", vurls.some(u => u.includes("trailer.mp4")));
check("URL 已转绝对路径", vurls.every(u => u.startsWith("http")), vurls[0]);
check("有 poster 字段", (v.items || []).some(x => x.poster && x.poster.startsWith("http")));
check("去重生效", new Set(vurls).size === vurls.length, `unique=${new Set(vurls).size} total=${vurls.length}`);
console.log(`  → 视频共 ${v.count} 条\n`);

/* ---------- 图片 ---------- */
console.log("[2] 图片提取");
const im = fn("image");
check("ok=true", im.ok === true);
check("抓到条目", (im.items || []).length > 0, `count=${(im.items || []).length}`);
const iurls = (im.items || []).map(x => x.url);
check("抓到普通 img", iurls.some(u => u.includes("img1")), iurls.join(" | "));
check("抓到 data-src 懒加载", iurls.some(u => u.includes("lazy1")));
check("抓到 data-original 懒加载", iurls.some(u => u.includes("lazy2")));
check("抓到 CSS 背景图", (im.items || []).some(x => x.source === "css-bg" && x.url.includes("bgcase")),
  JSON.stringify((im.items || []).filter(x => x.source === "css-bg")));
check("抓到 srcset 最大图", iurls.some(u => u.includes("rs1/640/400")), iurls.filter(u => u.includes("rs1")).join(" | "));
check("不含 data: 内联图", !iurls.some(u => u.startsWith("data:")), iurls.filter(u => u.startsWith("data:")).join(","));
check("去重生效", new Set(iurls).size === iurls.length);
console.log(`  → 图片共 ${im.count} 条\n`);

/* ---------- 文字 ---------- */
console.log("[3] 文字提取");
const t = fn("text");
check("ok=true", t.ok === true);
check("有 plain 正文", (t.plain || "").length > 50, `len=${(t.plain || "").length}`);
check("blocks 非空", (t.blocks || []).length > 0, `blocks=${(t.blocks || []).length}`);
check("抓到 h1/h2 标题", (t.blocks || []).some(b => b.tag === "h1" || b.tag === "h2"),
  JSON.stringify((t.blocks || []).slice(0, 4)));
check("抓到段落", (t.blocks || []).some(b => b.tag === "p"));
check("抓到列表项", (t.blocks || []).some(b => b.tag === "li"));
check("抓到引用", (t.blocks || []).some(b => b.tag === "blockquote"));
check("中文正文包含关键句", (t.plain || "").includes("正文提取") || (t.plain || "").includes("测试段落"));
check("chars 统计合理", t.chars > 100, `chars=${t.chars}`);
check("没有重复 h2（去重）",
  (t.blocks || []).filter(b => b.tag === "h2").length <= 6,
  `h2 count=${(t.blocks || []).filter(b => b.tag === "h2").length}`);
console.log(`  → 文字 ${t.chars} 字符 / ${t.blocks.length} 块\n`);

/* ---------- 全部 ---------- */
console.log("[4] 全部提取");
const a = fn("all");
check("ok=true", a.ok === true);
check("含 video", (a.video || []).length > 0);
check("含 image", (a.image || []).length > 0);
check("含 text", (a.text && a.text.blocks || []).length > 0);
check("count 汇总正确", a.count === a.video.length + a.image.length + a.text.blocks.length,
  `${a.count} vs ${a.video.length + a.image.length + a.text.blocks.length}`);
console.log(`  → 合计 ${a.count}\n`);

/* ---------- 未知类型 ---------- */
console.log("[5] 边界情况");
const bad = fn("nonexistent_kind");
check("未知类型不抛异常", bad && bad.ok === false);
const m = fn("video").meta || {};
check("meta 带 url", !!m.url, JSON.stringify(m));
check("meta 带 domain", !!m.domain, JSON.stringify(m));

console.log("\n" + "=".repeat(52));
if (failed === 0) {
  console.log("全部通过 ✓  extract.js 逻辑可用");
  process.exit(0);
} else {
  console.log(`失败 ${failed} 项 ✗`);
  process.exit(1);
}
