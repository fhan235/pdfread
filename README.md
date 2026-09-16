# pdfread

轻量的 PDF 双栏对照翻译阅读器。左侧原版 PDF，右侧流式中文译文，滚动同步。

为"读论文/读报告"这一件事而做：**不保留排版，只要读得快、读得懂**。

## 安装

### 方式一：下载现成应用（推荐，无需 Python）

到 [Releases](https://github.com/fhan235/pdfread/releases) 下载对应系统的文件：

| 系统 | 文件 |
|---|---|
| Windows | `pdfread-windows-x64.zip` |
| macOS | `pdfread-macos-arm64.dmg` |
| Linux | `pdfread-linux-x64.tar.gz` |

Intel 芯片的 Mac 也下载上面这个 dmg，系统会自动通过 Rosetta 2 运行。

解压后双击 `pdfread` 即可。启动后会打开**原生窗口**（Windows 用 WebView2，macOS 用 WKWebView），
环境不支持时自动回退为浏览器应用模式窗口。首次使用点右上角「设置」填入 API Key。

> **macOS 首次打开**：当前版本未使用 Apple Developer ID 签名和 Notarization（公证），
> 因此 Gatekeeper 可能提示「无法验证是否包含可能危害 Mac 安全或泄漏隐私的恶意软件」。
> 这是 macOS 对未签名第三方 App 的标准提示，不代表它检测到了恶意软件。
>
> 请右键点击 App →「打开」→ 再次确认；若提示「已损坏」，终端执行：
> `xattr -cr /Applications/pdfread.app`
>
> **Windows**：若被 SmartScreen 拦截，点「更多信息」→「仍要运行」。

### 方式二：从源码运行

```bash
pip install git+https://github.com/fhan235/pdfread.git
pdfread paper.pdf
```

### 从源码运行

```bash
git clone https://github.com/fhan235/pdfread.git
cd pdfread
pip install -e .
pdfread paper.pdf
```

也可以使用模块入口：

```bash
python -m pdfread paper.pdf
```

Windows、macOS 与 Linux 均可使用以上方式；请使用 Python 3.10 或更高版本。

## 使用

```bash
pdfread                      # 不指定文件，在界面中选择
pdfread paper.pdf            # 直接打开
pdfread paper.pdf --open     # 并自动打开浏览器
```

| 操作 | 说明 |
|---|---|
| 顶栏「打开」 | 浏览目录、上传 PDF，或粘贴链接 |
| 粘贴 URL | 直接打开网络 PDF（arXiv 的 abs/html 链接自动转 pdf）；网页链接经系统浏览器 headless 转成 PDF |
| 拖放文件到窗口 | 直接打开 |
| 顶栏「设置」 | 选择翻译服务、填写 API Key |
| 点击段落 | 展开/收起对应英文原文 |
| 顶栏「原文」 | 全局切换原文对照 |
| 顶栏「同步」 | 开关左右滚动联动 |
| 打开面板「保留参考文献」 | 关闭自动参考文献过滤，适合识别不准确的文档 |
| 顶栏「导出」 | 导出当前已完成内容为 TXT / Markdown，可选双语；跳过失败段落并提示未完成内容 |

参考文献过滤会在后续章节或附录标题处恢复正文，仍属于启发式识别。
命令行可用 `pdfread paper.pdf --include-references` 保留全部内容。
当前双栏段落的阅读顺序保持不变。

每个浏览器标签的文件请求使用独立文档 ID；进程内保留最近打开的 16 份文档，
服务重启或文档过期后需要重新打开。原文件发生修改时也需要重新打开。

## 翻译服务

在界面「设置」中配置，或使用环境变量（环境变量优先）：

| `--provider` | 环境变量 | 默认模型 |
|---|---|---|
| `deepseek` | `DEEPSEEK_API_KEY` | deepseek-chat |
| `silicon` | `SILICON_API_KEY` | Qwen/Qwen2.5-7B-Instruct |
| `qwen` | `DASHSCOPE_API_KEY` | qwen-plus |
| `openai` | `OPENAI_API_KEY` | gpt-4o-mini |
| `ollama` | 无需 | qwen2.5:7b |

任何 OpenAI 兼容接口都可用 `--base-url` + `--model` 接入。
界面「设置」也可填写模型和 API 地址，按服务分别保存；只更新密钥不会重置这些选项。
服务、模型和地址的启动优先级为：命令行参数 > 环境变量 > 本地设置 > 默认值。
对应环境变量为 `PDFREAD_PROVIDER`、`PDFREAD_MODEL`、`PDFREAD_BASE_URL`。
目前仅支持翻译为简体中文；并发数 `--concurrency` 必须在 1–32 之间。

设置与缓存分开保存，可分别用 `PDFREAD_CONFIG_DIR`、`PDFREAD_CACHE_DIR` 指定目录。
旧版缓存目录中的配置会在首次读取时复制到新设置目录，旧文件保留以便回退。
翻译缓存包含服务地址、模型和提示词版本；升级后旧缓存不会误用于新翻译逻辑，可能需要重新翻译。

## 成本参考

以 98 页 / 17 万字符的报告为例：

| 服务 | 单次全文成本 |
|---|---|
| 豆包 / 硅基流动低价模型 | 约 0.1 元 |
| DeepSeek | 约 0.5–1 元 |
| DeepL / Google 传统翻译 API | 约 12–14 元 |

命中缓存的段落不重复计费。

## 特点

- **快**：98 页 PDF 解析 1.2 秒，单页渲染 40ms
- **逐页推送**：哪页先完成就先显示，页面仍按原页码排列；不是逐字流式输出，耗时取决于翻译服务
- **段落重建**：从行级别还原被 PDF 折断的自然段，避免半句送翻译
- **省钱**：SQLite 缓存按段落去重，重开文档零费用；自动跳过页眉页脚与参考文献

### 段落重建

很多 PDF 把每一行都作为独立文本块，直接按块翻译会让句子半途截断。`extract.py` 从行级别重新聚类：

- 按 x 坐标检测分栏，每列独立合并
- 主判据：前一行是否以句末标点收尾（实测覆盖约 77% 的无标记折行）
- 辅助判据：满行宽度、字号一致、行距、缩进、列表标记
- 行尾连字符断词自动拼合（`en-` + `gineering` → `engineering`）

## 结构

```
launcher.py             PyInstaller 打包入口
pdfread.spec            打包配置
src/pdfread/
├── app.py              桌面应用入口（双击启动）
├── cli.py              命令行入口
├── server.py           FastAPI 服务
├── extract.py          PDF 文本提取与段落重建
├── pdfworker.py        专用进程中的 PDF 解析与页面渲染
├── translate.py        并发翻译 + SQLite 缓存
├── settings.py         本地配置（API Key）
├── paths.py            跨平台路径解析
└── static/index.html   前端界面（单文件，无构建）
```

## 自行打包

```bash
pip install . pyinstaller
pyinstaller --noconfirm pdfread.spec
```

产物在 `dist/`。注意 PyInstaller **不支持交叉编译**，需在目标系统上构建。
本仓库的 GitHub Actions 在主分支、PR 和 `v*` 标签触发测试与三平台构建；标签构建通过后发布 Release。
CI 使用 `uv.lock` 固定依赖，Intel macOS 使用 `macos-15-intel` runner。

## 开发验证

```bash
pip install -e '.[test]'
python -m pytest -q
# 如已安装 Node.js 22+，可运行前端交互逻辑测试：
node --test tests/test_frontend.cjs
```

测试使用临时配置、合成 PDF 和模拟翻译响应，不调用付费翻译服务。
如已安装 uv，可用 `uv sync --locked --extra test --extra build` 复现 CI 依赖。
`run.sh` 仅启动当前 Python 环境中的应用，不自动安装依赖；可用 `PDFREAD_PYTHON` 指定解释器。

## 安全

- API Key 优先读环境变量，界面填写的保存在独立用户设置目录；POSIX 系统中新建配置文件权限为 `0600`
- 密钥不会通过接口返回前端，不写入日志
- 启动入口仅允许本机监听，并检查 Host 与浏览器请求来源；不支持直接暴露为局域网或公网服务
- 文件访问限定在白名单目录内，防路径穿越
- 上传限制扩展名与大小（200 MB）
- 前端输出经 HTML 转义
- 翻译时，正文和认证密钥会发送给所选翻译服务；使用本机 Ollama 时由本机处理
- 上传文件使用独立存储路径，同名文件不会互相覆盖；解析失败不会替换已打开的文档

## 已知限制

- 扫描版 PDF 需先 OCR（未内置）
- 公式、表格按纯文本处理，不做结构还原
- 复杂版面（跨栏图表、侧边栏）的段落顺序可能有偏差

## License

MIT
