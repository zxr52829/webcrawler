# 网页素材抓取器

在真实浏览器里打开任意网页，一键抓取**视频直链、图片、正文**，并自动处理抖音/B站的 DASH 双轨、AV1 编码、HE-AAC 无声、防盗链 403 这些坑。

打包成单个 19.5 MB 的 exe，不需要装 Python。

```
双击 exe → 浏览器窗口打开目标网页 → 控制台选类型 → 开始抓取 → 导出
```

## 为什么用它

市面上的抓取工具碰上视频基本就废了，因为这个领域有一堆反直觉的坑：

| 坑 | 表现 | 本项目怎么处理 |
|---|---|---|
| MSE 播放 | `<video src>` 是 `blob:`，抓不到真地址 | 注入网络监听（包装 fetch/XHR + PerformanceObserver）捞出真实分片 |
| DASH 双轨 | 视频和音频是两个独立文件 | 自动识别并拼接 |
| AV1 编码 | B 站默认给 AV1，Windows 播放器黑屏 | 自动转 H.264 |
| HE-AAC 音频 | 有音轨但播放器没声音 | 自动转 AAC-LC |
| 防盗链 | 链接粘到浏览器/下载器就 403 | 专用下载器自动带 UA + Referer |
| 两个完整文件 | 被误当成"视频+音频"拼，画质错乱 | 按分辨率择优保留 |

## 快速开始

### 用现成的 exe

从 [Releases](https://github.com/zxr52829/webcrawler/releases/latest) 下载 `webcrawler.exe`，双击运行。

需要 **WebView2 运行时**。Win10/11 一般自带；没有的话装一下 [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)。

### 从源码跑

```bash
git clone https://github.com/zxr52829/webcrawler.git
cd webcrawler
pip install -r requirements.txt
python app.py
```

### 自己打包 exe

```bash
pip install pyinstaller
python -m PyInstaller build.spec --noconfirm --clean
```

产物在 `dist/网页素材抓取器.exe`。

## 图形界面用法

打开后会出两个窗口：

- **「网页素材抓取器」** 控制台：选类型、抓取、导出
- **「浏览器」**：正常浏览目标网站，可以登录、翻页、播放

操作流程：

1. 在浏览器窗口打开目标网页，视频可以先点播放让它加载
2. 回控制台选抓取类型
3. 点 **开始抓取**
4. 点 **导出结果**，文件落到 `下载\网页抓取结果\`

### 五种抓取类型

| 类型 | 内容 |
|---|---|
| 视频 | `<video>`/`<source>`、内联脚本里的 m3u8/mp4、网络层的真实媒体流 |
| 图片 | `img` 的 src/srcset/懒加载属性、CSS `background-image`、`og:image` |
| 文字 | `article`/`main` 里的标题、段落、列表、引用 |
| 选中文字 | 只抓你在页面上选中的部分 |
| 全部 | 三类一次抓完，导出 JSON + 分项清单 |

抓图片前先点 **滚到底**，让懒加载的图片全部加载，能多抓 3~5 倍。

### 清理功能

工具栏的 **🧹 清理** 按钮：

| 操作 | 清理什么 | 副作用 |
|---|---|---|
| 仅清缓存 | DiskCache、CacheStorage、浏览历史 | 无，登录状态保留 |
| 彻底清除 | 上述 + Cookie、localStorage、IndexedDB | 所有网站需重新登录 |
| 删除导出 | 输出目录下全部文件 | 不可恢复 |

## 命令行用法

不点界面，适合批量或自动化。

### 抓取

```bash
python grab_url.py "https://www.bilibili.com/video/BVxxxx/"
python grab_url.py "URL" --kind video
python grab_url.py "URL" --wait 8 --json result.json
```

| 参数 | 说明 |
|---|---|
| `target` / `--url` | 目标网址 |
| `--kind` / `-k` | `video` / `image` / `text` / `all`，默认 `all` |
| `--wait` / `-w` | 页面静默多少秒后开抓，默认 5 |
| `--timeout` / `-t` | 整体超时，默认 45 秒 |
| `--json` | 完整结果另存 JSON |
| `--no-export` | 只打印，不落盘 |

exe 也支持：

```bat
网页素材抓取器.exe --url "URL" --kind video
```

### 下载媒体

抓取后会产出 `*_视频流.txt`，里面是可直接下载的地址。**用专用下载器下载，不要手动粘贴**：

```bash
python fetch_media.py "xxx_视频流.txt"
python fetch_media.py "https://....m4s" -o D:\out
python fetch_media.py "文件" --dry-run     # 只检查可用性
python fetch_media.py "文件" --no-merge    # 只下载不拼接
```

它会自动：

- 按域名/URL 特征配对的 `Referer`（B站 / 抖音）
- 从导出文件头部的 `# 页面:` 记录读取真实页面地址
- 判别 DASH 双轨 vs 两个完整文件
- 自动拼接，AV1→H.264、HE-AAC→AAC-LC
- 输出 `merged.mp4`

需要 **ffmpeg** 在 PATH 里（拼接和转码用）。

## 那些坑的具体细节

### 403 不是链接坏了

B 站和抖音都要求 `User-Agent` 和 `Referer` **同时带**，缺一个就 403：

| 请求头 | 结果 |
|---|---|
| 都不带 | 403 |
| 只带 UA | 403 |
| 只带 Referer | 403 |
| 两个都带 | **200** |
| `Python-urllib` 默认 UA + Referer | 403（UA 必须是浏览器的） |

更关键的是：**B 站链接只有从 bilibili.com 页面点开才不 403**。

| 使用方式 | 状态 |
|---|---|
| 从 B 站页面里点开 | 200 |
| 地址栏直接粘贴 | 403 |
| 从搜索引擎点开 | 403 |
| IDM / 迅雷 / wget / aria2 / VLC | 403 |

因为只有第一种情况浏览器才会自动带 `Referer: https://www.bilibili.com/`。
所以 B 站链接**必须走脚本下载**。

### 链接有时效

抖音/B站的媒体链接带签名时间戳（`l=` / `deadline=`），有效期约几小时。
过期只能重新抓，没有补救办法。

### blob: 背后是什么

抖音、B站用 MSE 播放时，`<video src>` 是 `blob:https://...`，这只是浏览器内存对象的引用，
真实数据在 `MediaSource` 的分片请求里。`netcap.js` 通过包装 `fetch`/`XMLHttpRequest`
并用 `PerformanceObserver` 回溯已发生的请求，把真实地址捞出来。

而且**只有页面真的开始播放**才会触发分片加载 —— 所以 `crawl` 会先尝试 `video.play()`。

### codecid 不代表分辨率

B 站 DASH 轨道文件名形如 `42806806465-1-100023.m4s`，其中 `100023` 是 codecid。
网上常传"100023 = 1080P H.264"，**这是错的**。

实测 `BV1epJY6NEVw` 的 `100023` 是 **AV1 852×480**。codecid 与分辨率的对应关系随视频而变，
所以本项目只按 codecid 做轨道分类和相对排序，不宣称具体分辨率。
真实规格以下载后 `ffprobe` 为准。

## 项目结构

```
webcrawler/
├── app.py                 主程序：Api 类 + 窗口创建
├── console.html           控制台界面（内嵌 CSS/JS）
├── extract.js             页面提取器：视频/图片/文字
├── netcap.js              网络捕获：捞 blob 背后的真实媒体流
├── grab_url.py            命令行抓取模式
├── fetch_media.py         媒体下载器：处理 403、DASH 拼接、编码转换
├── build.spec             PyInstaller 打包配置
├── testpage.html          本地测试页（视频/图片/文字全覆盖）
└── verify_*.py, verify.js 回归测试
```

## 回归测试

改代码前后跑一遍，确保没破坏已有能力。

```bash
# 离线可跑
python verify_reflection.py     # pywebview 反射安全（防卡死）
python verify_export.py         # 导出链路、文件名清洗
python verify_bili_tracks.py    # B站 codecid 解析与择优

# 需要 WebView2 + 网络
python verify_gui.py            # 端到端抓取（用本地 testpage.html）
python verify_cache.py          # 缓存清理
python verify_clean_ui.py       # 清理面板交互

# 需要传一个公开视频页面 URL（脚本会在真实浏览器里打开它）
python verify_video_gui.py "https://www.bilibili.com/video/BVxxxxxxxx/"
python verify_video_timing.py "URL"   # 不同时刻抓取的表现
python verify_video_stress.py "URL"   # 多轮重载，统计直链/blob 覆盖率

# HE-AAC 无声修复验证（内置测试链接，链接过期会跳过）
python verify_heaudio.py

# JS 单元测试（需要 npm i jsdom）
node verify.js
```

## 开发时的两个致命坑

### 1. 不要把 Window 对象挂在 js_api 的公开属性上

pywebview 会把 `js_api` 对象的所有非下划线属性递归暴露给前端
（`webview/util.py` 的 `get_functions`）。如果挂 `self.browser = window`，递归路径会是：

```
Api.browser → Window.native → WinForms 窗体 → AccessibilityObject
→ Bounds.Empty.Empty.Empty... → 爆栈 → UI 线程死锁
```

表现：窗口白屏不响应，日志一行不出。

**解法**：窗口引用一律用下划线私有属性（`self._browser`），并加 `Api._serializable = False`。

### 2. 窗口建好后不能阻塞 UI 线程

**WebView2 的 COM 只能在 UI 线程访问**。窗口创建之后，任何 `while` + `sleep`
轮询都会堵死消息泵，页面永远加载不完。所有等待必须靠 `events.loaded` 事件推进。

`boot()`（`webview.start(func=...)` 的回调）里的 sleep 是安全的，因为那时窗口还没建。

## 已知限制

- **不能抓 DRM 保护的内容**（Netflix 等）
- **不能抓 canvas 渲染的文字**（部分漫画站）
- **VIP/付费内容需要你自己已登录的账号**
- 链接有效期约几小时，过期需重抓
- 仅支持 Windows（依赖 WebView2）

## 免责声明

本项目仅用于**个人学习和技术研究**。

请遵守目标网站的服务条款与 `robots.txt`，不要用于：
- 批量抓取受版权保护的内容并传播
- 对目标站点造成压力的高频请求
- 任何违反当地法律法规的用途

使用者自行承担全部责任。

## License

[MIT](LICENSE)
