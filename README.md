# pdfread

PDF / EPUB / TXT 双栏对照翻译阅读器：左边原文，右边中文译文，滚动同步。

不保留排版，只要读得快、读得懂。适合读论文、报告和原版书。

## 30 秒上手

1. 到 [Releases](https://github.com/fhan235/pdfread/releases) 下载对应系统的文件，解压后双击 `pdfread`

   | 系统 | 文件 |
   |---|---|
   | Windows | `pdfread-windows-x64.zip` |
   | macOS（含 Intel 芯片） | `pdfread-macos-arm64.dmg` |
   | Linux | `pdfread-linux-x64.tar.gz` |

2. 点右上角「设置」，选择翻译服务并填入 API Key
3. 点「打开」选择文件（也可以拖进窗口，或粘贴 arXiv / 网页链接），再点「开始翻译」

> **macOS 提示无法验证开发者**：右键 App →「打开」。若提示「已损坏」，执行 `xattr -cr /Applications/pdfread.app`
> **Windows SmartScreen 拦截**：点「更多信息」→「仍要运行」

## 用 Python 安装

需要 Python 3.10+：

```bash
pip install git+https://github.com/fhan235/pdfread.git

pdfread                  # 启动后在界面中选择文件
pdfread paper.pdf        # 直接打开
pdfread paper.pdf --open # 同时打开浏览器
```

## 翻译服务

| 服务 | `--provider` | 密钥环境变量 | 默认模型 |
|---|---|---|---|
| DeepSeek | `deepseek` | `DEEPSEEK_API_KEY` | deepseek-chat |
| 硅基流动 | `silicon` | `SILICON_API_KEY` | Qwen/Qwen2.5-7B-Instruct |
| 通义千问 | `qwen` | `DASHSCOPE_API_KEY` | qwen-plus |
| OpenAI | `openai` | `OPENAI_API_KEY` | gpt-4o-mini |
| Ollama（本机） | `ollama` | 不需要 | qwen2.5:7b |

其他 OpenAI 兼容接口可以用 `--base-url` 和 `--model` 接入。
密钥可以在界面里填写，也可以用环境变量提供，环境变量优先。

**参考成本**：98 页、17 万字符的报告翻译一遍，低价模型约 0.1 元，DeepSeek 约 0.5–1 元。
译文会缓存到本地，重新打开同一文档不会再次计费。

## 主要功能

- **对照阅读**：译文逐页推送，可以展开每段原文；支持滚动同步、页码跳转、目录和阅读进度记忆
- **论文模式**：识别图表和公式，自动跳过参考文献，并把被拆断的段落接回去
- **润色**：对已有译文再处理一遍，让上下文读起来更连贯
- **划线与划词翻译**：在 PDF 上选中文字后可以高亮，也可以单独翻译
- **AI 问答**：围绕当前文档提问，或一键生成速读摘要
- **文库**：打开过的文件会自动加入文库，可以用文件夹、标签和搜索管理
- **导出**：导出为 TXT 或 Markdown，可以选择只要译文或双语对照
- **桌面应用**：使用原生窗口，支持多标签、自定义主题和应用内更新

## 常用命令行参数

| 参数 | 说明 |
|---|---|
| `--provider` / `--model` / `--base-url` | 指定翻译服务、模型和接口地址 |
| `--concurrency N` | 并发请求数，范围 1–32，默认 8 |
| `--include-references` | 保留参考文献，不自动过滤 |
| `--root DIR` | 增加界面中允许访问的目录，可以多次指定 |
| `--port N` | 监听端口，默认 8011，端口被占用时自动往后找 |

相关环境变量：`PDFREAD_PROVIDER`、`PDFREAD_MODEL`、`PDFREAD_BASE_URL`、`PDFREAD_CONFIG_DIR`、`PDFREAD_CACHE_DIR`。

## 隐私

- 服务只监听本机 `127.0.0.1`
- API Key 只保存在本机，配置文件权限为 `0600`，不会返回给前端，也不会写入日志
- 翻译时，正文会发送给你选择的翻译服务；使用 Ollama 时，数据不会离开本机

## 已知限制

- 扫描版 PDF 需要先做 OCR
- 公式和表格按纯文本处理
- 版面复杂时，段落顺序可能不准确

## 开发

```bash
git clone https://github.com/fhan235/pdfread.git && cd pdfread
pip install -e '.[test]'
python -m pytest -q
node --test tests/test_frontend.cjs   # 前端测试，需要 Node 22+

# 打包（需要在目标系统上构建，不支持交叉编译）
pip install . pyinstaller && pyinstaller --noconfirm pdfread.spec
```

代码结构、API 和常见修改位置见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## License

MIT
