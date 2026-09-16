# pdfread

轻量的 PDF 双栏对照翻译阅读器。左侧原版 PDF，右侧流式中文译文，滚动同步。

为"读论文/读报告"这一件事而做：**不保留排版，只要读得快、读得懂**。

## 特点

- **快**：98 页 PDF 解析 1.2 秒，单页渲染 40ms
- **流式**：翻译好一页显示一页，第一页秒出，不用等全文跑完
- **段落重建**：从行级别还原被 PDF 折断的自然段，避免半句送翻译
- **省钱**：SQLite 缓存按段落去重，重开文档零费用；自动跳过页眉页脚与参考文献
- **轻**：约 1000 行代码，4 个依赖，前端零构建

## 为什么不用现成工具

`pdf2zh` / `BabelDOC` 这类工具的目标是**产出一个排版一致的译文 PDF**，大部分耗时花在版面模型推理和原位回写上，且必须全文跑完才出文件。

本项目的目标是**一个阅读界面**。跳过版面还原后，解析从数分钟降到 1 秒级，并且可以边翻边读。

## 安装

需要 Python 3.10+。

```bash
pip install pymupdf fastapi uvicorn httpx
```

## 使用

```bash
export DEEPSEEK_API_KEY='sk-...'
python server.py --pdf /path/to/paper.pdf
```

浏览器打开 http://127.0.0.1:8011 ，填写页码范围（`0` 表示到最后一页），点「开始翻译」。

### 界面操作

| 操作 | 说明 |
|---|---|
| 点击段落 | 展开/收起对应英文原文 |
| 顶栏「原文」 | 全局切换原文对照 |
| 顶栏「同步」 | 开关左右滚动联动 |

### 翻译服务

| `--provider` | 环境变量 | 默认模型 |
|---|---|---|
| `deepseek` | `DEEPSEEK_API_KEY` | deepseek-chat |
| `silicon` | `SILICON_API_KEY` | Qwen/Qwen2.5-7B-Instruct |
| `qwen` | `DASHSCOPE_API_KEY` | qwen-plus |
| `openai` | `OPENAI_API_KEY` | gpt-4o-mini |
| `ollama` | 无需 | qwen2.5:7b |

任何 OpenAI 兼容接口都可用 `--base-url` + `--model` 接入。

### 常用参数

```
--pdf PATH           启动时加载的 PDF
--provider NAME      翻译服务
--model NAME         覆盖默认模型
--base-url URL       覆盖默认 API 地址
--concurrency N      并发请求数, 默认 8
--port N             监听端口, 默认 8011
--root DIR           追加允许访问的目录(可多次指定)
--cache PATH         缓存数据库路径, 默认 ./cache.db
```

## 成本参考

以 98 页 / 17 万字符的报告为例（约 4 万输入 + 5 万输出 tokens）：

| 服务 | 单次全文成本 |
|---|---|
| 豆包 / 硅基流动低价模型 | 约 0.1 元 |
| DeepSeek | 约 0.5–1 元 |
| DeepL / Google 传统翻译 API | 约 12–14 元 |

命中缓存的段落不重复计费。

## 结构

```
extract.py          PDF 文本提取与段落重建
translate.py        并发翻译 + SQLite 缓存 + 多服务适配
server.py           FastAPI: 页面位图渲染 + SSE 流式翻译
static/index.html   前端界面(单文件, 无构建)
run.sh              启动脚本
```

### 段落重建

很多 PDF 把每一行都作为独立文本块，直接按块翻译会让句子半途截断。`extract.py` 从行级别重新聚类：

- 按 x 坐标检测分栏，每列独立合并
- 主判据：前一行是否以句末标点收尾（实测覆盖约 77% 的无标记折行）
- 辅助判据：满行宽度、字号一致、行距、缩进、列表标记
- 行尾连字符断词自动拼合（`en-` + `gineering` → `engineering`）

## 安全

- API Key 仅从环境变量读取，不落盘、不返回前端、不写日志
- 服务默认绑定 `127.0.0.1`
- 文件访问限定在白名单目录内，防路径穿越
- 前端输出经 HTML 转义

## 已知限制

- 扫描版 PDF 需先 OCR（未内置）
- 公式、表格按纯文本处理，不做结构还原
- 复杂版面（跨栏图表、侧边栏）的段落顺序可能有偏差

## License

MIT
