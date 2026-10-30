# pdfread 架构索引

> 面向维护者的导览：改代码前先看这里，不必从头通读。
> 行号会随改动漂移，以"模块 + 区块名"定位为准。

## 一句话

FastAPI（127.0.0.1 本机）+ 无构建多文件前端的 PDF/EPUB/TXT 双栏对照翻译阅读器，
带文库（文件夹/标签）、划线高亮、AI 问答/速读摘要、版本自更新。
PyMuPDF 解析在独立进程池里跑（崩溃隔离）。

## 入口与运行链路

| 入口 | 文件 | 说明 |
|---|---|---|
| `pdfread` 命令 | `src/pdfread/cli.py` | 解析参数 → `server.configure()` → uvicorn |
| `python -m pdfread` | `src/pdfread/__main__.py` | 转调 `cli.main()` |
| 桌面双击 | `src/pdfread/app.py` | pywebview 原生窗口 → 浏览器应用模式回退；单实例（runtime.json）；关页看门狗；`run_updater()` 一键更新器 |
| PyInstaller | `launcher.py` + `pdfread.spec` | 打包入口/配置；`--apply-update PKG PID` 进入更新器模式 |

启动流程：`cli.py` → `server.configure(pdf, roots, cfg, cache_path)`（装配 STATE）
→ 浏览器/原生窗口加载 `/`（即 `static/index.html`）→ 前端 `init()` 恢复标签或进文库。

## 后端模块索引（src/pdfread/）

| 文件 | 职责 | 关键位置 |
|---|---|---|
| `server.py` | 全部 HTTP 路由 + 运行时 STATE | 路由见下表；`configure()`；`_register()` 打开文档登记（含文库 upsert、打开历史）；`_qa_cfg()` 问答模型覆盖；`_close_pool()` 退出时强制回收进程池；`enable_page_watchdog()` 关页退出看门狗 |
| `extract.py` | PDF 文本提取 + 段落重建（行级聚类、分栏） | `Para` 数据类；`extract_pages()` |
| `paper.py` | 论文模式：图表区域识别（figtext）、跨图表段落续接、公式占位（⟦F1⟧）、表格/参考文献过滤 | `looks_like_paper()` / `extract_paper()`；`mask_formulas()` / `unmask_formulas()` |
| `pdfworker.py` | 进程池内执行：解析（带 stamp 防文件中途变更）、渲染 PNG/SVG、词坐标 | `inspect_pdf` / `render_page_svg` / `page_words` |
| `translate.py` | LLM 调用：翻译/润色/问答/摘要 + SQLite 缓存 | `PROVIDERS`（含 `models` 候选列表）；`Translator`（`_chat` 是统一调用点，429/5xx 重试 3 次） |
| `library.py` | 文库 + 高亮的 SQLite 持久化 | `Library`；表结构在 `_SCHEMA`；`VALID_COLORS` |
| `settings.py` | 本地配置（API Key 0600、解析选项、打开历史、更新代理、按服务的 model/base_url/qa_model） | `profile()` / `update_translation()` / `get_parse()` / `add_history()` / `set_proxy()` |
| `paths.py` | 跨平台路径：缓存/配置/上传/白名单根目录、图标资源、安装位置 | `cache_dir()` `config_dir()` `uploads_dir()`；`asset_icon()`；`install_target()`（一键更新定位） |
| `epub.py` | EPUB 原生解析（章节即页） | `inspect_epub` / `read_resource`；解压大小上限防 zip bomb；`_sanitize` 去脚本/事件/内联样式/外链图 |
| `txt.py` | TXT 原生解析（编码检测、章节识别、按字符切块） | `inspect_txt` |
| `urlfetch.py` | URL 打开（SSRF 校验、headless 网页转 PDF） | `normalize_url` / `validate_url` / `resolve_checked`（返回已验证 IP 供连接钉扎）/ `fetch_document` |
| `browser.py` | 系统浏览器探测 + headless 网页转 PDF | `find_browser()` / `html_to_pdf()`（`--host-resolver-rules` 钉 IP 防 SSRF） |
| `_macos.py` | macOS Dock 图标处理 | `hide_dock_icon()` / `hide_dock_icon_if_multiprocessing_child()` |

## HTTP API 速查（server.py）

阅读：`GET /api/page/{n}.svg`（默认）L816 · `GET /api/page/{n}.png` L838 · `GET /api/text/{n}` L859 · `GET /api/translate`(SSE) L867 · `GET /api/polish`(SSE) L943 · `POST /api/ask` L1051 · `POST /api/ask-stream`(SSE 流式) L1067 · `POST /api/translate-text`(划词翻译, 走缓存) L1094 · `POST /api/summary`(缓存) L1113 · `GET /api/raw` L1135
EPUB/TXT：`GET /api/chapter/{n}`(净化章节 HTML) L783 · `GET /api/epub-res/{n}`(包内图片, SVG 加 CSP sandbox) L796
文档管理：`GET /api/docs` L273 · `POST /api/docs/close` L286 · `GET /api/info` L299 · `POST /api/open` L573 · `POST /api/upload` L645 · `POST /api/open-url` L598 · `GET /api/browse` L362 · `POST /api/roots`(加白名单) L348 · `GET /api/progress`(打开任务进度轮询) L593 · `GET /api/history` L401 · `POST /api/history/remove` L409
文库：`GET /api/library` L425 · folders L430-451 · documents move/tags/remove L454-481 · `POST /api/library/open` L495 · `POST /api/library/progress` L484
划线：`GET /api/words/{n}` L526 · `GET/POST /api/highlights` L544/L552 · `POST /api/highlights/delete` L567
设置：`GET/POST /api/settings`（model/qa_model/base_url/proxy 覆盖，models 候选列表）L684/L714 · `GET/POST /api/parse-settings`（论文模式选项）L761/L768
版本更新：`GET /api/update/check` L1377 · `POST /api/update/download` L1427 · `POST /api/update/apply`(派生自身 exe 做更新器) L1436
生命周期：`POST /api/heartbeat` L1488 · `POST /api/pagehide`(关页启动 8s 退出倒计时) L1495 · `POST /api/shutdown`（强制回收进程池后 `os._exit`，不让卡住的 worker 拖住退出）L1525

## 数据存储

| 数据 | 位置 | 结构 |
|---|---|---|
| 翻译/润色/摘要缓存 | `cache_dir()/translations.db` | 表 `t(k hash, v 文本)`，key 含 base_url+模型+提示词版本 |
| 文库/高亮 | `config_dir()/library.db` | 表 `folders` `documents`(含 progress/pages 列, 旧库自动 ALTER 迁移) `tags` `document_tags` `highlights`（外键级联；高亮 rects 为 JSON 的 PDF 点坐标） |
| 设置/密钥 | `config_dir()/config.json`（0600） | 环境变量优先于配置文件；另含 `profiles`（按服务 model/base_url/qa_model）、`parse`（论文模式选项）、`history`（最近打开, 上限 20）、`proxy`（更新代理） |
| 上传文件 | `cache_dir()/uploads/` | uuid 文件名，不覆盖 |
| 运行实例 | `cache_dir()/runtime.json` | `{pid, port}`，桌面版单实例探测 |
| 更新回执 | `cache_dir()/update/pending.json` + `update.log` | 一键更新标记与更新器日志；下载包在 `cache_dir()/downloads/` |
| 前端偏好 | localStorage `pdfread.prefs` | theme/themeCustom/font/layout/layoutPrev/zoom/lib/qa/toc/leftPct/libW/qaW/expFmt/expSrc/italicSrc/efont/eline/efontFam/efontCustom/skipVersion |
| 会话标签 | sessionStorage `pdfread.doc` | 当前标签 id |

## 前端地图（static/，无构建，多文件）

`index.html` 只有页面结构；`style.css` 是全部样式；JS 按功能拆成 8 个**经典脚本**，
加载顺序即依赖顺序（`index.html` 底部的 script 标签顺序）。
跨文件共享全局作用域：顶层 `function` 声明挂在 window 上，`const/let` 在全局词法环境，
后续文件可直接引用先加载文件的符号。改动时注意：

- **不要重复声明**同名顶层 `const/let`（会抛 SyntaxError）
- 引用后加载文件的函数必须包在箭头函数里延迟求值（如 `() => openPicker()`），
  直接赋值 `onclick = fn` 在加载时 fn 尚未定义会失效

| 文件 | 职责 | 关键状态/函数 |
|---|---|---|
| `js/state.js` | 全局状态与 `$` 简写；页面心跳与 pagehide 上报 | `INFO`、`left/right`、`es/esP`、`fileSerial` |
| `js/tabs.js` | 多文档标签 + 启动恢复 | `tabs[]`/`activeTab`；`snapshotTab`/`restoreTab`（含 qaHist、润色视图快照）；`addOrActivateTab()`；`init()` |
| `js/pages.js` | 左栏懒加载（SVG/章节）、翻译 SSE、润色、滚动同步、目录、阅读进度 | `buildLeft()`（imgwrap+hlayer / echapter）；`run()`/`renderPage()`；`togglePolish()`；`tidy`/`esc`；`centerLeft()`；`scrollToPage()`；`renderTOC()` |
| `js/library.js` | 文库侧栏（文件夹/标签/搜索/移动/移除） | `LIB`/`libScope`；`renderLibrary()`；`libPost()`；`openLibDoc()` |
| `js/highlight.js` | 划线高亮：拖选→预览→确认，双击选词 | `marksByDoc`/`wordsByDoc`；`hitWord`/`selectionRects`；`discardPending`；`recoverLib`（lib 缺失自动恢复） |
| `js/panels.js` | 分割条拖拽 + AI 问答（流式、独立模型选择）+ 速读摘要 | `applySplit()`/`dragResize()`；`qaHist`/`mdRender()`（先转义）/`qaSend()`/`qaSummary()`；`loadQaModel` |
| `js/ui.js` | 顶栏交互、文件选择/上传/URL/最近打开/白名单目录 | `openFile`/`uploadFile`/`openUrl`（XHR 进度 + `/api/progress` 轮询）；`loadHistory`/`addRoot`；`setBusy*` + `#busy` 右下角浮动进度卡片 |
| `js/prefs.js` | 阅读偏好、导出、设置面板、论文模式、自定义主题、PDF 缩放、版本更新、启动 | `prefs`/`applyPrefs()`；`doExport()`；`applyCustomTheme`/`buildThemeGrid`；`setZoom` + Ctrl/⌘+滚轮；`checkUpdate()` + 更新弹窗；尾部是启动序列 |

**关键全局**：`INFO`（当前文档元信息，含 `doc` 运行时 id、`lib` 文库 id、`sizes` 页面尺寸 pt）。
样式按注释区块组织（主题变量/顶栏/标题栏/标签栏/布局/译文/文库/问答/划线等），改样式先搜区块名。

## 核心机制备忘

- **PDF 渲染**：页面→SVG 矢量图（`text_as_path`，任意缩放不失真）；`/api/page/{n}.png` 保留作回退。左栏是图，不是文本。
- **EPUB/TXT 渲染**：左栏按章节请求 `/api/chapter/{n}` 的净化 HTML；EPUB 图片经 `/api/epub-res` 提供（前端把 `__EPUB_RES__` 占位前缀替换为真实端点）。
- **划线坐标**：以 PDF 点存入 SQLite，前端按 `INFO.sizes` 换算成 % 定位 → 缩放/换 DPI 不错位。EPUB/TXT 不支持划线。
- **划词吸附**：`page_words()` 给词坐标（按 块→行→词 排序，双栏不交错），前端拖选按词吸附，按 (块,行) 合并成每行矩形；双击单词直接选中；起点落在空白处拖到第一个词才确立，不作废手势。
- **模型配置**：预设 + 按服务 profile 覆盖（model/base_url/qa_model）；问答/速读走 `_qa_cfg()`，qa_model 为空时跟随翻译模型。
- **自定义主题**：`theme=custom` 时 8 个 CSS 变量（bg/panel/line/fg/dim/accent/ok/warn）以 inline style 覆盖，按背景亮度自适应深浅组件；首次进入以当前主题为底版快照。
- **标签页**：切换=快照 innerHTML+滚动位置+qaHist+润色视图；翻译流切换即停（缓存在服务端，重翻秒出）。
- **AI 问答**：方案 A 全文直塞，超 60000 字符（`QA_BUDGET`）掐中段留首尾（首 70% + 尾 30%）；不走缓存；历史只允许 user/assistant 角色，单条 ≤8000 字符、≤20 条。速读摘要结果按 (全文, 模型) 缓存。
- **文库白名单**：`library.db` 里的路径视为用户已授权；普通路径仍需在 roots 白名单内（`_safe_resolve`）。
- **单实例**：桌面版启动时读 `runtime.json` 探测已有实例，有则激活其窗口直接退出。
- **关页看门狗**：浏览器回退模式下页面每 5s 心跳；pagehide 启动 8s 退出倒计时，心跳消失超 180s 也兜底退出。
- **版本更新**：网页端 `releases/latest` 302 优先（不占 API 配额）+ API 富化（两通道版本一致才合并）；结果缓存 6 小时；下载地址仅允许 `github.com/{_UPDATE_REPO}/releases/download/` 的 https 链接；`apply` 派生自身 exe 第二个进程（`--apply-update`）做替换与重启，下次启动按 `pending.json` 回执成败。

## 常见改动去哪改

| 需求 | 位置 |
|---|---|
| 加翻译服务 | `translate.py` `PROVIDERS` 加一行（`models` 是界面下拉的候选列表） |
| 改翻译/润色/问答/摘要行为 | 对应 PROMPT（translate.py L61/72/84/93）——改后建议 bump `cache_model`/`polish_model` 里的版本串 |
| 加主题 | `style.css` 加变量块（仿 `body.theme-paper`）+ `prefs.js` 的 `THEME_ORDER/NAMES` + index.html 的 segTheme 按钮 |
| 加划线颜色 | `library.py` `VALID_COLORS` + `style.css` `.hl` 样式 + index.html `#hlConfirm`/`#hlPop` 加按钮 |
| 加 API | `server.py` 路由区 + `library.py` 方法 + 前端对应 js 文件用 `libPost` 调用 |
| 改文库 UI | `style.css`「文库侧栏」区块 + `js/library.js` |
| 段落识别优化 | `extract.py`（通用）/ `paper.py`（论文模式） |
| 改更新通道 | `server.py` `_UPDATE_REPO` + 前端 `prefs.js` 的 `checkUpdate()` |

## 测试

```bash
pip install -e '.[test]' && python -m pytest -q     # 后端（80+ 测试函数，参数化展开约百项）
node --test tests/test_frontend.cjs                 # 前端（需 Node 22+）
```

- `tests/conftest.py`：`client` fixture（TestClient + 临时目录）、`make_pdf` 合成 PDF
- 测试文件按域划分：`test_server.py`(服务端) · `test_library.py`(文库/划线/问答) · `test_translation.py` · `test_update.py`(更新/看门狗/代理) · `test_epub.py` · `test_txt.py` · `test_urlfetch.py` · `test_extract_settings.py`
- 测试不调付费 API：monkeypatch `Translator._chat`（问答/润色）或 `Translator.translate`（翻译）
- 前端 cjs 测试按 `JS_FILES` 顺序拼接 `static/js/*.js` 后用 vm 跑（截到 `/* 启动:` 之前），新增 js 文件要同步加入该列表，改 DOM 结构或启动逻辑时注意兼容

## 安全约定（改动时保持）

仅绑 127.0.0.1 + TrustedHost + 同源中间件；路径白名单防穿越；上传限扩展名/200MB；
密钥环境变量优先、不返回前端、不入日志；前端一律 `esc()`/`textContent` 输出；
urlfetch 拦内网/凭证 URL 并返回已验证 IP；网页转 PDF 时 Chrome 用
`--host-resolver-rules` 钉住该 IP 且其余域名解析失败(页面级跳转/子资源拦截,
残余风险: IP 字面量直链的内网资源不经 DNS 无法拦截)；
EPUB 解压设单文件/总量上限防 zip bomb、内嵌 SVG 加 CSP sandbox；
更新包下载地址白名单（仅本仓库 Release 的 https 链接，上限 500MB）；
解析在独立进程，失败重建进程池释放句柄。
