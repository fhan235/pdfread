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
| 桌面双击 | `src/pdfread/app.py` | pywebview 原生窗口 → 浏览器应用模式回退；单实例（runtime.json）；关页看门狗 |
| PyInstaller | `launcher.py` + `pdfread.spec` | 打包入口/配置；`--install-update PLAN` 进入安装器模式（见 `updater.py`） |

启动流程：`cli.py` → `server.configure(pdf, roots, cfg, cache_path)`（装配 STATE）
→ 浏览器/原生窗口加载 `/`（即 `static/index.html`）→ 前端 `init()` 恢复标签或进文库。

## 后端模块索引（src/pdfread/）

| 文件 | 职责 | 关键位置 |
|---|---|---|
| `server.py` | 全部 HTTP 路由 + 运行时 STATE | 路由见下表；`configure()`；`_register()` 打开文档登记（含文库 upsert、打开历史）；`_qa_cfg()` 问答模型覆盖；`_close_pool()` 退出时强制回收进程池；`enable_page_watchdog()` 关页退出看门狗 |
| `extract.py` | PDF 文本提取 + 段落重建（行级聚类、分栏） | `Para` 数据类；`extract_pages()` |
| `paper.py` | 论文模式：图表区域识别（figtext）、跨图表段落续接、公式占位（⟦F1⟧）、表格/参考文献过滤 | `looks_like_paper()` / `extract_paper()`；`mask_formulas()` / `unmask_formulas()` |
| `pdfworker.py` | 进程池内执行：解析（带 stamp 防文件中途变更）、渲染 PNG/SVG、词坐标、文内链接 | `inspect_pdf` / `render_page_svg` / `page_words` / `page_links` |
| `translate.py` | LLM 调用：翻译/润色/问答/摘要 + SQLite 缓存 | `PROVIDERS`（含 `models` 候选列表）；`Translator`（`_chat` 是统一调用点，429/5xx 重试 3 次） |
| `library.py` | 文库 + 高亮的 SQLite 持久化 | `Library`；表结构在 `_SCHEMA`；`VALID_COLORS` |
| `settings.py` | 本地配置（API Key 0600、解析选项、打开历史、更新代理、按服务的 model/base_url/qa_model） | `profile()` / `update_translation()` / `get_parse()` / `add_history()` / `set_proxy()` |
| `paths.py` | 跨平台路径：缓存/配置/上传/白名单根目录、图标资源、安装位置 | `cache_dir()` `config_dir()` `uploads_dir()`；`asset_icon()`；`install_target()`（一键更新定位） |
| `epub.py` | EPUB 原生解析（章节即页） | `inspect_epub` / `read_resource`；解压大小上限防 zip bomb；`_sanitize` 去脚本/事件/内联样式/外链图 |
| `txt.py` | TXT 原生解析（编码检测、章节识别、按字符切块） | `inspect_txt` |
| `urlfetch.py` | URL 打开（SSRF 校验、headless 网页转 PDF） | `normalize_url` / `validate_url` / `resolve_checked`（返回已验证 IP 供连接钉扎）/ `fetch_document` |
| `browser.py` | 系统浏览器探测 + headless 网页转 PDF | `find_browser()` / `html_to_pdf()`（`--host-resolver-rules` 钉 IP 防 SSRF） |
| `updater.py` | 一键更新的暂存与安装（主进程侧 + 安装器侧） | `prepare()`/`spawn()`（主进程）；`install()`（安装器入口）；`replace()` 只换包内条目可回滚；`pid_alive()`（跨平台，Windows 用 OpenProcess） |
| `_macos.py` | macOS Dock 图标处理 | `hide_dock_icon()` / `hide_dock_icon_if_multiprocessing_child()` |

## HTTP API 速查（server.py）

阅读：`GET /api/page/{n}.svg`（默认）· `GET /api/page/{n}.png` · `GET /api/thumb/{lib_id}.png`（文库封面，落盘缓存）· `GET /api/text/{n}` · `GET /api/translate`(SSE) · `GET /api/polish`(SSE) · `POST /api/ask` · `POST /api/ask-stream`(SSE 流式) · `POST /api/translate-text`(划词翻译, 走缓存) · `POST /api/summary`(缓存) · `GET /api/raw`
EPUB/TXT：`GET /api/chapter/{n}`(净化章节 HTML) L783 · `GET /api/epub-res/{n}`(包内图片, SVG 加 CSP sandbox) L796
文档管理：`GET /api/docs` · `POST /api/docs/close` · `GET /api/info` · `POST /api/open`(支持 `force=1` 跳过查重) · `POST /api/upload`(同) · `POST /api/open-url`(body 里 `force`) · `GET /api/browse` · `POST /api/roots`(加白名单) · `GET /api/progress`(打开任务进度轮询) · `GET /api/history` · `POST /api/history/remove`
> 三个打开入口命中内容查重时返回 **409** `{duplicate, sha256, existing}`，不是错误而是等用户决策。
文库：`GET /api/library` L425 · folders L430-451 · documents move/tags/remove L454-481 · `POST /api/library/open` L495 · `POST /api/library/progress` L484
划线：`GET /api/words/{n}` L527 · `GET/POST /api/highlights` · `POST /api/highlights/delete`
文内链接：`GET /api/links/{n}`(本页指向文档内其他位置的链接, 仅 PDF) L545
设置：`GET/POST /api/settings`（model/qa_model/base_url/proxy 覆盖，models 候选列表）L684/L714 · `GET/POST /api/parse-settings`（论文模式选项）L761/L768
版本更新：`GET /api/update/check`(含 `apply_block` 不可一键安装的原因) L1381 · `POST /api/update/download`(下载 + SHA-256 校验) L1482 · `POST /api/update/apply`(用户确认后: 暂存并启动安装器, 随后本进程退出) L1491
生命周期：`POST /api/heartbeat` L1539 · `POST /api/pagehide`(关页启动 8s 退出倒计时) L1546 · `POST /api/shutdown`（`_schedule_exit()`：强制回收进程池后 `os._exit`，不让卡住的 worker 拖住退出）L1596

## 数据存储

| 数据 | 位置 | 结构 |
|---|---|---|
| 翻译/润色/摘要缓存 | `cache_dir()/translations.db` | 表 `t(k hash, v 文本)`，key 含 base_url+模型+提示词版本 |
| 文库/高亮 | `config_dir()/library.db` | 表 `folders` `documents`(含 progress/pages/sha256 列, 旧库自动 ALTER 迁移; sha256 建索引供查重) `tags` `document_tags` `highlights`（外键级联；高亮 rects 为 JSON 的 PDF 点坐标） |
| 设置/密钥 | `config_dir()/config.json`（0600） | 环境变量优先于配置文件；另含 `profiles`（按服务 model/base_url/qa_model）、`parse`（论文模式选项）、`history`（最近打开, 上限 20）、`proxy`（更新代理） |
| 上传文件 | `cache_dir()/uploads/` | uuid 文件名，不覆盖 |
| 运行实例 | `cache_dir()/runtime.json` | `{pid, port}`，桌面版单实例探测 |
| 更新工作区 | `cache_dir()/update/` | `pending.json` 更新回执、`update.log` 安装器日志、`staging-*` 解压出的新版本、`backup-*` 替换时的旧文件（下次检查时清理）；下载包在 `cache_dir()/downloads/` |
| 前端偏好 | localStorage `pdfread.prefs` | theme/themeCustom/font/layout/layoutPrev/zoom/lib/qa/toc/leftPct/libW/qaW/qaInH/expFmt/expSrc/italicSrc/efont/eline/efontFam/efontCustom/skipVersion/**libView**/libSort/**libShelf**（旧键 libDense/libFull 由 `migratePrefs()` 迁移）|
| 会话标签 | sessionStorage `pdfread.doc` | 当前标签 id |

## 前端地图（static/，无构建，多文件）

`index.html` 只有页面结构；`style.css` 是全部样式；JS 按功能拆成 10 个**经典脚本**，
加载顺序即依赖顺序（`index.html` 底部的 script 标签顺序）。
唯一的第三方前端依赖是 KaTeX，自托管在 `static/vendor/katex/`（在 `mathtex.js` 之前加载）。
跨文件共享全局作用域：顶层 `function` 声明挂在 window 上，`const/let` 在全局词法环境，
后续文件可直接引用先加载文件的符号。改动时注意：

- **不要重复声明**同名顶层 `const/let`（会抛 SyntaxError）
- 引用后加载文件的函数必须包在箭头函数里延迟求值（如 `() => openPicker()`），
  直接赋值 `onclick = fn` 在加载时 fn 尚未定义会失效

| 文件 | 职责 | 关键状态/函数 |
|---|---|---|
| `js/state.js` | 全局状态与 `$` 简写；页面心跳与 pagehide 上报 | `INFO`、`left/right`、`es/esP`、`fileSerial` |
| `js/tabs.js` | 多文档标签 + 启动恢复 | `tabs[]`/`activeTab`；`snapshotTab`/`restoreTab`（含问答会话组 `qaSessions`、润色视图快照）；`addOrActivateTab()`；`init()` |
| `js/pages.js` | 左栏懒加载（SVG/章节）、翻译 SSE、润色、滚动同步、目录、阅读进度 | `buildLeft()`（imgwrap+hlayer / echapter）；`run()`/`renderPage()`；`togglePolish()`；`tidy`/`esc`；`centerLeft()`；`scrollToPage()`；`renderTOC()` |
| `js/library.js` | 文库（文件夹/标签/搜索/排序/列表·详情/书架/批量/移动/移除） | `LIB`/`libScope`/`libSel`（批量选中）；`renderLibrary()`；`docRow()`（三种呈现分支）；`LIB_SORTS`/`libSorted()`；`renderLibBulk()`；`isShelf()`/`setShelf()`；`libPost()`；`openLibDoc()` |
| `js/highlight.js` | 划线高亮：拖选→预览→确认，双击选词 | `marksByDoc`/`wordsByDoc`；`hitWord`/`selectionRects`；`discardPending`；`recoverLib`（lib 缺失自动恢复） |
| `js/links.js` | 文内链接：引用/交叉引用点击跳转 + 返回 | `linkHTML()`（热区，纯函数）；`loadLinks()`；`jumpToLink()`/`navBackGo()`；`navBack`（按文档的返回栈）；`syncLinkBack()`（浮动返回按钮） |
| `js/mathtex.js` | 数学公式渲染：薄封装 KaTeX（本地自托管，离线可用） | `texRender(src, display)` 是唯一出口；`TEX_MACROS`（补 LLM 常用而 KaTeX 没有的命令）；`texNormalize()` 剥掉 `\begin{equation}`/`\label{}` 等 KaTeX 不认的文档级语法；KaTeX 本体在 `static/vendor/katex/` |
| `js/panels.js` | 分割条拖拽（含问答输入框高度）+ AI 问答（多对话会话、流式、独立模型选择）+ 速读摘要 | `applySplit()`/`dragResize()`；`qaSessions`/`qaHist`（指向当前会话）/`qaNewSession`/`qaSwitchSession`/`qaDelSession`/`qaLoadTab`/`qaSaveTab`；`renderQaSessions()`（横向标签栏，事件委托在 `#qaTabs`，支持中键关闭）/`qaStatus()`（临时提示）/`qaEmptyHint()`（空状态）；`mdRender()`（**先把公式/行内代码换占位符再做粗斜体**，所以 `**$x$：说明**` 这种跨公式的加粗能配对；表格/代码块/引用/分割线，容忍行间空行与未闭合块）/`qaSend()`/`qaSummary()`；`loadQaModel` |
| `js/ui.js` | 顶栏交互、文件选择/上传/URL/最近打开/白名单目录 | `openFile`/`uploadFile`/`openUrl`（XHR 进度 + `/api/progress` 轮询）；`loadHistory`/`addRoot`；`setBusy*` + `#busy` 右下角浮动进度卡片 |
| `js/prefs.js` | 阅读偏好、导出、设置面板、论文模式、自定义主题、PDF 缩放、版本更新、启动 | `prefs`/`applyPrefs()`；`doExport()`；`applyCustomTheme`/`buildThemeGrid`；`setZoom` + Ctrl/⌘+滚轮；`checkUpdate()` + 更新弹窗；尾部是启动序列 |

**关键全局**：`INFO`（当前文档元信息，含 `doc` 运行时 id、`lib` 文库 id、`sizes` 页面尺寸 pt）。
样式按注释区块组织（主题变量/顶栏/标题栏/标签栏/布局/译文/文库/问答/划线等），改样式先搜区块名。

## 核心机制备忘

- **PDF 渲染**：页面→SVG 矢量图（`text_as_path`，任意缩放不失真）；`/api/page/{n}.png` 保留作回退。左栏是图，不是文本。
- **EPUB/TXT 渲染**：左栏按章节请求 `/api/chapter/{n}` 的净化 HTML；EPUB 图片经 `/api/epub-res` 提供（前端把 `__EPUB_RES__` 占位前缀替换为真实端点）。
- **划线坐标**：以 PDF 点存入 SQLite，前端按 `INFO.sizes` 换算成 % 定位 → 缩放/换 DPI 不错位。EPUB/TXT 不支持划线。
- **划词吸附**：`page_words()` 给词坐标（按 块→行→词 排序，双栏不交错），前端拖选按词吸附，按 (块,行) 合并成每行矩形；双击单词直接选中；起点落在空白处拖到第一个词才确立，不作废手势。
- **文内链接**：`page_links()` 只取 `LINK_GOTO` / `LINK_NAMED`（外链、跨文件链接忽略），统一换算成**显示坐标**。两类目标点坐标系不同（已实测）：显式目标 MuPDF 已换算好；命名目标（LaTeX hyperref 的引用都是这种）来自 `resolve_names()`，仍是 PDF 原始坐标，需 `× transformation_matrix × rotation_matrix`。每条链接返回 `[x0,y0,x1,y1, 目标页, 锚点y, 滚动y]`：锚点用于高亮，滚动 y 通常等于锚点。**图片例外**（`_figure_view`）：hyperref / Word 交叉引用把图片锚点放在图注上，而图注多在图的下方，直接滚过去图会在视野外；因此当锚点左侧 90pt 内有「Figure/Fig./图 + 编号」标签（hyperref 锚点在标签之后，Word 在标签起点；按词匹配，因为粗体标签常被 PyMuPDF 切成单独的行），且图注正上方 40pt 内有图形（`get_image_info()` 位图 + `get_cdrawings()` 矢量，宽或高 <8pt 的细线不算，避免吞进表格横线）时，滚到图形顶部；向上吸附子图时，中间夹着别的图/表注就停。图注宽度从标签沿行向右延伸，遇到另一个图注标签（双栏并排的图）或 >40pt 间隔就停。表格不调整（表注在表上方）；旋转页不调整；参考文献链接（`cite.*`）跳过分析。前端在 `.imgwrap` 里叠 `.llayer`（图层不接收事件，只有 `.plink` 热区可点，其余位置照常拖选划线）；按下后移动超过 4px 视为拖选，不触发跳转。返回位置记为（页码, 页内比例），中途缩放也能准确回位；`buildLeft()` 时清空该文档的栈。
- **模型配置**：预设 + 按服务 profile 覆盖（model/base_url/qa_model）；问答/速读走 `_qa_cfg()`，qa_model 为空时跟随翻译模型。
- **自定义主题**：`theme=custom` 时 8 个 CSS 变量（bg/panel/line/fg/dim/accent/ok/warn）以 inline style 覆盖，按背景亮度自适应深浅组件；首次进入以当前主题为底版快照。默认主题是浅色（`prefs.theme="light"`，`index.html` 的 `<body class="light">` 避免首屏闪深色）。
- **标签页**：切换=快照 innerHTML+滚动位置+qaHist+润色视图；翻译流切换即停（缓存在服务端，重翻秒出）。
- **AI 问答**：方案 A 全文直塞，超 60000 字符（`QA_BUDGET`）掐中段留首尾（首 70% + 尾 30%）；不走缓存；历史只允许 user/assistant 角色，单条 ≤8000 字符、≤20 条。速读摘要结果按 (全文, `summary_model`) 缓存。
- **数学输出格式**：`QA_PROMPT` / `SUMMARY_PROMPT` 共用 `MATH_RULE`，**强制模型把数学一律写成 LaTeX**（行内 `$…$`、独立 `$$…$$`，变量与下标也要包）。没有这条时模型只给独立公式加 `$$`，行内符号写成裸文本（`d²_hat{q}`、`λ_bound`），既进不了 KaTeX，还会把 `hat{}` 这种命令名直接显示出来 —— 渲染器再好也救不了没被标记成公式的文本。规则里同时禁用 Unicode 上下标与 `\begin{equation}`/`\label{}`。
- **问答多会话**：同一文档可开多个对话（`qaSessions`，面板顶部**横向标签栏**切换，样式与顶部文档标签栏 `#tabbar` 同一套语言）。面板默认仅 340px 宽，所以标签 `flex: 1 1 auto` 可收缩，收到 `min-width` 后整条横向滚动、`＋` 固定在右侧不滚走（当前标签 `flex-grow:1.6` 优先拿空间并保底 60px；完整标题放 `title` 兜底）。`qaHist` 恒指向当前会话的历史数组（不可重新赋值成副本）；会话组随标签快照存取，刷新页面不保留。同一时刻只允许一个请求在跑，回答只写进发起时捕获的标签+会话，等待中的会话在标签上显示 `●`。
- **问答面板的垂直空间**：面板窄且高度有限，**常驻横幅会显著挤占对话区**。原先那条"基于当前文档全文…"的说明固定占 52px（三行），现改为：长说明走 `.qaempty` 空状态（放在 `#qaList` 内部，有消息后 `addQaBubble` 自动移除），临时提示走 `qaStatus()`（`#qaHint` 平时 `display:none`，有消息才出现、3 秒后自动消失）。对话区占比由 75% 提到 81%。
- **公式渲染**：`mathtex.js` 封装 **KaTeX 0.16**（与 ChatGPT 同方案，排版基于 TeX 字体度量）。JS/CSS/woff2 字体全部自托管在 `static/vendor/katex/`，**不引任何 CDN**（本机应用，必须离线可用）；只有 woff2 会被实际请求（`@font-face` 里排第一）。`$…$`/`\(…\)` 行内，`$$…$$`/`\[…\]` 独立成块（`$$` 单独一行也认，未闭合时按流式渲染到末尾）。行内公式要求 `$` 紧邻非空白，避免把金额当公式；表格按行切单元格前先把公式/行内代码换成占位符，防止公式里的 `|` 切坏表格。`throwOnError:false` 让流式中途的半截公式以警告色显示原文而不是整块消失；`strict:false` 允许公式里出现中文；`trust:false` 关掉 `\href`/`\html*`/`\includegraphics`；`maxExpand`/`maxSize` 限制宏展开与尺寸；`macros` 每次传副本，否则一条公式的 `\gdef` 会泄漏到后面的公式。MathML 里的 `<annotation>` 保留原始 LaTeX（可朗读、可复制回 TeX），所以断言「无裸 TeX」时要先剥掉它。
- **Markdown 行内变换**：`mdRender` 的 `inline()` **先把公式/行内代码整段换成占位符，再做 `**粗体**`/`*斜体*`，最后还原**。不能反过来（先按公式切片再逐片套正则）—— 模型很爱写 `**$d^2_{\Pi,i}$：点到切平面的距离**`，切片后 `**` 的开闭落在公式两侧的不同片段里，永远配不上对，`**` 就被原样显示出来。强调标记要求紧邻非空白，避免把 `a * b` 的乘号当强调。
- **滚动与画布**：**外层文档永不滚动**（`html, body { overflow: hidden }`），所有滚动都发生在内部面板里。配合主题变量只挂在 body（见下条），彻底消除"下拉露出画布"的黑屏/白屏。**flex 列里的滚动容器，其所有祖先都必须显式写 `min-height: 0`** —— flex 子项默认 `min-height:auto` 不肯收缩，漏一层就会把内容撑出面板、顶高整个页面（`#libMain` 漏写导致文库列表没有滚动条、页面被撑高到 3553px，下拉即白屏）。
- **主题与画布底色**：主题变量的覆盖（`body.light` / `body.theme-paper` / `body.theme-green` / 自定义）全挂在 **body** 上，而 CSS 变量只向下继承 —— `html`（即 `:root`）永远只能取到深色默认值。因此**背景只能画在 body 上，绝不能给 `html` 加 `background: var(--bg)`**：那样过滚动（macOS 橡皮筋）露出的画布会是近黑色，浅色主题下表现为「滚轮下拉整窗黑屏」。html 不画背景时，body 的背景按规范传播到画布，始终跟随主题。另配 `overscroll-behavior`（html/body 用 `none`、`.pane` 用 `contain`）从源头掐掉滚动链外溢。
- **导入查重**：三个入口（`/api/open`、`/api/upload`、`/api/open-url`）在解析前先算文件 **SHA-256**（`file_sha256()`，分块读，跑在 `to_thread` 里不阻塞事件循环），命中 `documents.sha256` 则返回 **409 + 已有记录**，前端弹窗给「打开已有的 / 仍然导入一份 / 取消」。`force=1` 跳过检查（用户已确认）。**同路径不算重复**（那是再次打开同一文件，不该打扰）；上传命中时会先删掉刚落盘的副本再返回，不留垃圾。只按内容哈希判定，字节不同就绝不报重复（零误报）；哈希为空（旧记录、读文件失败）一律不参与判重，`/api/library/open` 会顺带回填哈希让老记录逐步获得判重能力。
- **顶栏布局**：控件按用途包在 `header .grp` 里，**组内 `inline-flex` 不换行、换行以整组为单位**，组间用 `.grp + .grp::before` 画细竖线。窄窗口靠 `style.css` 末尾的媒体查询逐级让位（1520 缩字号滑块 → 1400 隐字号 → 1320 隐缩放 → 1100 隐同步与 label），把顶栏锁在单行 47px；让位的都是设置面板或快捷键另有入口的低频控件。**媒体查询必须放文件末尾**：同优先级下后出现的规则胜出，放前面会被后面的 `.fontctl`/`.zoomctl` 基础规则覆盖掉（踩过）。
- **文库白名单**：`library.db` 里的路径视为用户已授权；普通路径仍需在 roots 白名单内（`_safe_resolve`）。
- **文库三种呈现**（术语以界面为准）：侧栏**列表**（`libView:"list"`，33px/行，默认，用于快速定位）/ 侧栏**详情**（`libView:"detail"`，154px 卡片，信息摊开）/ **书架**（`body.shelf`，封面网格铺满整窗，用于浏览和整理）。书架由 `setShelf()` 切换，只加一个 body class：`main`/`#qaPanel`/分割条让位，`#libView` 改为横向（文件夹标签在左列），`#libList` 变 grid（`auto-fill minmax(190px,1fr)`，实测 1024–1920 自适应 3–8 列，卡片宽稳定在 194–249px 不被压扁）；返回阅读即还原，侧栏那套规则一行没动。书架下点卡片会自动返回阅读区。注意 `syncLibControls()` 必须在 `loadLibrary()` **之前**调用 —— 后者是异步的，放其后会被旧状态覆盖。
  > 命名约定：界面上只出现「列表 / 详情 / 书架 / 返回阅读」。不要再用"紧凑/舒适/大屏"——前两者描述的是感受而非内容，"大屏"描述的是设备而非功能。列表与详情做成分段控件而非切换按钮，避免"按钮上写着紧凑，到底是当前状态还是点击后的结果"这种歧义。
- **封面缩略图**：`GET /api/thumb/{lib_id}.png` → `render_thumb()` 渲染首页。与 `render_page` 不同**不校验 stamp**（文库文档通常没打开，没有 stamp 可比），改为按 `(lib_id, mtime, size)` 落盘缓存到 `cache_dir()/thumbs/`，文件变了自然换 key；写入先写 `.part` 再 `replace`，并发请求不会读到半截图片；`_trim_thumbs()` 保留最近 400 张。只接受**文库内的 PDF**（那些路径是用户主动导入的，视为已授权），EPUB/TXT 与丢失的文件返回 404，前端 `onerror` 移除 `<img>` 后露出垫在下层的扩展名占位。`<img loading="lazy">` 避免一次性拉完整库。
- **文库列表密度**：省出高度的关键是：标题单行省略 + 操作按钮悬停才浮现（`.rowops` 平时 `display:none`）+ 文件夹下拉从每行移到批量条。侧栏窄时用**容器查询**（`#libMain` 声明 `container-type: inline-size`）按侧栏实际宽度隐藏日期/划线数，把空间让给标题 —— 注意是容器宽而非窗口宽，媒体查询在这里不适用。排序在前端做（`LIB_SORTS`，服务端固定按 `last_opened` 返回）。批量选中 `libSel` 在每次 `renderLibrary()` 时会剔除当前筛选范围外的 id，避免误操作到看不见的文档。
- **单实例**：桌面版启动时读 `runtime.json` 探测已有实例，有则激活其窗口直接退出。
- **关页看门狗**：浏览器回退模式下页面每 5s 心跳；pagehide 启动 8s 退出倒计时，心跳消失超 180s 也兜底退出。
- **版本更新**：网页端 `releases/latest` 302 优先（不占 API 配额）+ API 富化（两通道版本一致才合并）；结果缓存 6 小时；下载地址必须等于本仓库该版本 Release 的固定地址，并用同一 Release 的 `SHA256SUMS.txt`（CI 生成）校验，缺校验文件则拒绝。
- **一键安装**（`updater.py`）：①下载校验 → ②用户确认 → ③主进程把包解压到 `staging-*`，以**暂存区里的新版本 exe** 启动安装器（`--install-update PLAN`，设 `PYINSTALLER_RESET_ENVIRONMENT=1` 作为独立实例），然后自行退出 → ④安装器等主进程真正退出（120s 超时则放弃，不改任何文件）→ 只替换安装包里存在的条目（旧条目移入 `backup-*`，失败整体回滚并重启旧版）→ 重启。安装器不在安装目录里运行，因此没有文件锁/ETXTBSY/覆写运行中 .app 的问题；且执行替换的总是新版本自己的逻辑。
- **跨平台进程检测**：Windows 上 `os.kill(pid, 0)` 会被当成 Ctrl+C 广播（bpo-14484），不能用来判断存活，统一走 `updater.pid_alive()`。

## 常见改动去哪改

| 需求 | 位置 |
|---|---|
| 加翻译服务 | `translate.py` `PROVIDERS` 加一行（`models` 是界面下拉的候选列表） |
| 改翻译/润色/问答/摘要行为 | 对应 PROMPT（`translate.py` 顶部的 `SYSTEM_PROMPT`/`POLISH_PROMPT`/`QA_PROMPT`/`SUMMARY_PROMPT`，数学格式要求在共用的 `MATH_RULE`）——翻译/润色改后要 bump `cache_model`/`polish_model` 的版本串；摘要的 `summary_model` 已绑定 `SUMMARY_PROMPT` 哈希，自动失效 |
| 加主题 | `style.css` 加变量块（仿 `body.theme-paper`）+ `prefs.js` 的 `THEME_ORDER/NAMES` + index.html 的 segTheme 按钮 |
| 补公式语法 | 一般不用动（KaTeX 覆盖面足够）；LLM 常用而 KaTeX 没有的命令加到 `js/mathtex.js` 的 `TEX_MACROS`，KaTeX 不认的文档级环境在 `texNormalize()` 里剥掉；样式见 `style.css`「数学公式」区块。升级 KaTeX：替换 `static/vendor/katex/` 下的 `katex.min.{js,css}` 与 `fonts/*.woff2` |
| 加划线颜色 | `library.py` `VALID_COLORS` + `style.css` `.hl` 样式 + index.html `#hlConfirm`/`#hlPop` 加按钮 |
| 加 API | `server.py` 路由区 + `library.py` 方法 + 前端对应 js 文件用 `libPost` 调用 |
| 改文库 UI | `style.css`「文库侧栏」/「列表行」/「文库书架」区块 + `js/library.js`；界面用词见「文库三种呈现」的命名约定 |
| 段落识别优化 | `extract.py`（通用）/ `paper.py`（论文模式） |
| 改更新通道 | `server.py` `_UPDATE_REPO` + 前端 `prefs.js` 的 `checkUpdate()` |
| 改安装流程 | `updater.py`；**`--install-update PLAN` 的参数与 plan.json 字段是跨版本协议**（旧版本会用它启动新版本），只能新增字段，不能改名或删除 |

## 测试

```bash
pip install -e '.[test]' && python -m pytest -q     # 后端 145 项
node --test tests/test_frontend.cjs                 # 前端 20 项（需 Node 22+）
```

- `tests/conftest.py`：`client` fixture（TestClient + 临时目录）、`make_pdf` 合成 PDF
- 测试文件按域划分：`test_server.py`(服务端) · `test_library.py`(文库/划线/问答) · `test_translation.py` · `test_update.py`(更新/看门狗/代理) · `test_links.py`(文内链接与坐标换算) · `test_epub.py` · `test_txt.py` · `test_urlfetch.py` · `test_extract_settings.py`
- 测试不调付费 API：monkeypatch `Translator._chat`（问答/润色）或 `Translator.translate`（翻译）
- 前端 cjs 测试按 `JS_FILES` 顺序拼接 `static/js/*.js` 后用 vm 跑（截到 `/* 启动:` 之前），新增 js 文件要同步加入该列表，改 DOM 结构或启动逻辑时注意兼容；KaTeX 由测试 `require` 本地那份 `vendor/katex/katex.min.js` 后注入 vm 上下文，与浏览器用的是同一个文件

## 安全约定（改动时保持）

仅绑 127.0.0.1 + TrustedHost + 同源中间件；路径白名单防穿越；上传限扩展名/200MB；
密钥环境变量优先、不返回前端、不入日志；前端一律 `esc()`/`textContent` 输出；
公式渲染走 KaTeX 且 `trust:false`（不产出链接/图片/内联样式注入，文本由 KaTeX 自行转义），
`maxExpand`/`maxSize` 防构造输入耗死渲染；KaTeX 本地自托管，不引外部 CDN；
urlfetch 拦内网/凭证 URL 并返回已验证 IP；网页转 PDF 时 Chrome 用
`--host-resolver-rules` 钉住该 IP 且其余域名解析失败(页面级跳转/子资源拦截,
残余风险: IP 字面量直链的内网资源不经 DNS 无法拦截)；
EPUB 解压设单文件/总量上限防 zip bomb、内嵌 SVG 加 CSP sandbox；
更新包仅接受本仓库 Release 固定地址并校验 SHA-256（上限 500MB），tar 解压用 `filter="data"` 防越界，
安装器只替换包内条目、绝不触碰安装目录里的其他文件；
解析在独立进程，失败重建进程池释放句柄。
