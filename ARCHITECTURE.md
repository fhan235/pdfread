# pdfread 架构索引

> 面向维护者的导览：改代码前先看这里，不必从头通读。
> 行号会随改动漂移，以"模块 + 区块名"定位为准。

## 一句话

FastAPI（127.0.0.1 本机）+ 无构建多文件前端的 PDF/EPUB 双栏对照翻译阅读器，
带文库（文件夹/标签）、划线高亮、AI 问答。PyMuPDF 解析在独立进程池里跑（崩溃隔离）。

## 入口与运行链路

| 入口 | 文件 | 说明 |
|---|---|---|
| `pdfread` 命令 | `src/pdfread/cli.py` | 解析参数 → `server.configure()` → uvicorn |
| 桌面双击 | `src/pdfread/app.py` | pywebview 原生窗口，失败回退浏览器 |
| PyInstaller | `launcher.py` + `pdfread.spec` | 打包入口/配置 |

启动流程：`cli.py` → `server.configure(pdf, roots, cfg, cache_path)`（装配 STATE）
→ 浏览器/原生窗口加载 `/`（即 `static/index.html`）→ 前端 `init()` 恢复标签或进文库。

## 后端模块索引（src/pdfread/）

| 文件 | 职责 | 关键位置 |
|---|---|---|
| `server.py` | 全部 HTTP 路由 + 运行时 STATE | 路由见下表；`configure()`；`_register()` 打开文档登记（含文库 upsert）；`_qa_cfg()` 问答模型覆盖；`_close_pool()` 退出时强制回收进程池 |
| `extract.py` | PDF 文本提取 + 段落重建（行级聚类、分栏） | `Para` 数据类；`extract_pages()` |
| `paper.py` | 论文模式：图表区域识别、公式占位、参考文献过滤 | `looks_like_paper()` / `extract_paper()` |
| `pdfworker.py` | 进程池内执行：解析、渲染 PNG/SVG、词坐标 | `inspect_pdf` / `render_page_svg` / `page_words` |
| `translate.py` | LLM 调用：翻译/润色/问答 + SQLite 缓存 | `PROVIDERS`（含 `models` 候选列表）；`Translator`（`_chat` 是统一调用点） |
| `library.py` | 文库 + 高亮的 SQLite 持久化 | `Library`；表结构在 `_SCHEMA` |
| `settings.py` | 本地配置（API Key 0600、解析选项、历史、按服务的模型/base_url/qa_model） | `profile()` / `update_translation()` / `get_parse()` |
| `paths.py` | 跨平台路径：缓存/配置/上传/白名单根目录 | `cache_dir()` `config_dir()` `uploads_dir()` |
| `epub.py` | EPUB 原生解析（章节即页） | `inspect_epub` / `read_resource` |
| `urlfetch.py` | URL 打开（SSRF 校验、headless 网页转 PDF） | `normalize_url` / `fetch_document` |
| `_macos.py` | macOS Dock 图标处理 | — |

## HTTP API 速查（server.py）

阅读：`GET /api/page/{n}.svg`(默认) · `GET /api/page/{n}.png` L717 · `GET /api/text/{n}` L738 · `GET /api/translate`(SSE) L746 · `GET /api/polish`(SSE) L822 · `POST /api/ask` · `POST /api/ask-stream`(SSE 流式) · `POST /api/translate-text`(划词翻译, 走缓存) · `POST /api/summary`(缓存) · `GET /api/raw` L931
文档管理：`GET /api/docs` L225 · `POST /api/docs/close` L238 · `POST /api/open` L501 · `POST /api/upload` L573 · `POST /api/open-url` L526 · `GET /api/browse` L312
文库：`GET /api/library` L375 · folders L380-398 · documents move/tags/remove L404-427 · `POST /api/library/open` L434 · `POST /api/library/progress`
划线：`GET /api/words/{n}` L454 · `GET/POST /api/highlights` L472/L480 · `POST /api/highlights/delete` L495
其他：`GET/POST /api/settings`（含 model/qa_model/base_url 覆盖，models 候选列表） · parse-settings · `POST /api/shutdown`（强制回收进程池后 `os._exit`，不让卡住的 worker 拖住退出）

## 数据存储

| 数据 | 位置 | 结构 |
|---|---|---|
| 翻译/润色缓存 | `cache_dir()/translations.db` | 表 `t(k hash, v 文本)`，key 含模型+提示词版本 |
| 文库/高亮 | `config_dir()/library.db` | 表 `folders` `documents`(含 progress/pages 列, 旧库自动 ALTER 迁移) `tags` `document_tags` `highlights`（外键级联；高亮 rects 为 JSON 的 PDF 点坐标） |
| 设置/密钥 | `config_dir()`（0600） | 环境变量优先于配置文件 |
| 上传文件 | `cache_dir()/uploads/` | uuid 文件名，不覆盖 |
| 前端偏好 | localStorage `pdfread.prefs` | theme(含 custom)/themeCustom(自定义配色)/font/layout/layoutPrev/zoom/lib/qa/toc/leftPct/libW/qaW/expFmt/expSrc/italicSrc |
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
| `js/state.js` | 全局状态与 `$` 简写 | `INFO`、`left/right`、`es/esP`、`fileSerial` |
| `js/tabs.js` | 多文档标签 + 启动恢复 | `tabs[]`/`activeTab`；`snapshotTab`/`restoreTab`；`init()` |
| `js/pages.js` | 左栏懒加载、翻译 SSE、润色、滚动同步、目录、阅读进度 | `buildLeft()`（imgwrap+hlayer）；`run()`/`renderPage()`；`tidy`/`esc`；`centerLeft()`；`scrollToPage()`；`renderTOC()` |
| `js/library.js` | 文库侧栏（文件夹/标签/搜索/移动/移除） | `LIB`/`libScope`；`renderLibrary()`；`libPost()`；`openLibDoc()` |
| `js/highlight.js` | 划线高亮：拖选→预览→确认，双击选词 | `marksByDoc`/`wordsByDoc`；`hitWord`/`selectionRects`；`discardPending`；`recoverLib`（lib 缺失自动恢复） |
| `js/panels.js` | 分割条拖拽 + AI 问答（含独立模型选择） | `applySplit()`/`dragResize()`；`qaHist`/`mdRender()`（先转义）/`qaSend()`；`loadQaModel` |
| `js/ui.js` | 顶栏交互、文件选择/上传/URL | `openFile`/`uploadFile`/`openUrl`（XHR 进度）；`setBusy*` + `#busy` 右下角浮动进度卡片 |
| `js/prefs.js` | 阅读偏好、导出、设置面板、论文模式、自定义主题、PDF 缩放、启动 | `prefs`/`applyPrefs()`；`applyCustomTheme`/`buildThemeGrid`；`setZoom` + Ctrl/⌘+滚轮；尾部是启动序列 |

**关键全局**：`INFO`（当前文档元信息，含 `doc` 运行时 id、`lib` 文库 id、`sizes` 页面尺寸 pt）。
样式按注释区块组织（主题变量/顶栏/标题栏/标签栏/布局/译文/文库/问答/划线等），改样式先搜区块名。

## 核心机制备忘

- **PDF 渲染**：页面→SVG 矢量图（`text_as_path`，任意缩放不失真）；`/api/page/{n}.png` 保留作回退。左栏是图，不是文本。
- **划线坐标**：以 PDF 点存入 SQLite，前端按 `INFO.sizes` 换算成 % 定位 → 缩放/换 DPI 不错位。EPUB 不支持划线。
- **划词吸附**：`page_words()` 给词坐标，前端拖选按词吸附，按 (块,行) 合并成每行矩形；双击单词直接选中；起点落在空白处拖到第一个词才确立，不作废手势。
- **模型配置**：预设 + 按服务 profile 覆盖（model/base_url/qa_model）；问答/速读走 `_qa_cfg()`，qa_model 为空时跟随翻译模型。
- **自定义主题**：`theme=custom` 时 8 个 CSS 变量（bg/panel/line/fg/dim/accent/ok/warn）以 inline style 覆盖，按背景亮度自适应深浅组件；首次进入以当前主题为底版快照。
- **标签页**：切换=快照 innerHTML+滚动位置；翻译流切换即停（缓存在服务端，重翻秒出）。
- **AI 问答**：方案 A 全文直塞，超 60000 字符掐中段留首尾；不走缓存；历史只允许 user/assistant 角色。
- **文库白名单**：`library.db` 里的路径视为用户已授权；普通路径仍需在 roots 白名单内（`_safe_resolve`）。

## 常见改动去哪改

| 需求 | 位置 |
|---|---|
| 加翻译服务 | `translate.py` `PROVIDERS` 加一行（`models` 是界面下拉的候选列表） |
| 改翻译/润色/问答行为 | 对应 PROMPT（translate.py L53/64/76）——改后建议 bump `cache_model` 里的版本串 |
| 加主题 | `style.css` 加变量块（仿 `body.theme-paper`）+ `prefs.js` 的 `THEME_ORDER/NAMES` + index.html 的 segTheme 按钮 |
| 加划线颜色 | `library.py` `VALID_COLORS` + `style.css` `.hl` 样式 + index.html `#hlConfirm`/`#hlPop` 加按钮 |
| 加 API | `server.py` 路由区 + `library.py` 方法 + 前端对应 js 文件用 `libPost` 调用 |
| 改文库 UI | `style.css`「文库侧栏」区块 + `js/library.js` |
| 段落识别优化 | `extract.py`（通用）/ `paper.py`（论文模式） |

## 测试

```bash
pip install -e '.[test]' && python -m pytest -q     # 后端 84 个
node --test tests/test_frontend.cjs                 # 前端（需 Node 22+）
```

- `tests/conftest.py`：`client` fixture（TestClient + 临时目录）、`make_pdf` 合成 PDF
- 文库/划线/问答测试在 `tests/test_library.py`；服务端在 `test_server.py`
- 测试不调付费 API：monkeypatch `Translator._chat`（问答/润色）或 `Translator.translate`（翻译）
- 前端 cjs 测试按 `JS_FILES` 顺序拼接 `static/js/*.js` 后用 vm 跑（截到 `/* 启动:` 之前），新增 js 文件要同步加入该列表，改 DOM 结构或启动逻辑时注意兼容

## 安全约定（改动时保持）

仅绑 127.0.0.1 + TrustedHost + 同源中间件；路径白名单防穿越；上传限扩展名/200MB；
密钥环境变量优先、不返回前端、不入日志；前端一律 `esc()`/`textContent` 输出；
urlfetch 拦内网/凭证 URL；解析在独立进程，失败重建进程池释放句柄。
