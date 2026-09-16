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

解压后双击 `pdfread` 即可，浏览器会自动打开。首次使用点右上角「设置」填入 API Key。

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

或者克隆后用 `run.sh`（Linux/macOS）、`run.bat`（Windows），脚本会自动准备环境。

## 使用

```bash
pdfread                      # 不指定文件，在界面中选择
pdfread paper.pdf            # 直接打开
pdfread paper.pdf --open     # 并自动打开浏览器
```

| 操作 | 说明 |
|---|---|
| 顶栏「打开」 | 浏览目录或上传 PDF |
| 拖放文件到窗口 | 直接打开 |
| 顶栏「设置」 | 选择翻译服务、填写 API Key |
| 点击段落 | 展开/收起对应英文原文 |
| 顶栏「原文」 | 全局切换原文对照 |
| 顶栏「同步」 | 开关左右滚动联动 |

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
- **流式**：翻译好一页显示一页，第一页秒出，不用等全文跑完
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
本仓库的 GitHub Actions 会在推送 `v*` 标签时自动构建三平台产物。

## 安全

- API Key 优先读环境变量，界面填写的保存在用户配置目录且权限为 `0600`
- 密钥不会通过接口返回前端，不写入日志
- 服务默认绑定 `127.0.0.1`
- 文件访问限定在白名单目录内，防路径穿越
- 上传限制扩展名与大小（200 MB）
- 前端输出经 HTML 转义

## 已知限制

- 扫描版 PDF 需先 OCR（未内置）
- 公式、表格按纯文本处理，不做结构还原
- 复杂版面（跨栏图表、侧边栏）的段落顺序可能有偏差

## License

MIT
