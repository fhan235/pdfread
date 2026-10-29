"""TXT 格式: 解析单元测试 + 端到端打开。"""

import pytest

from pdfread.txt import inspect_txt

from test_library import opened


def _write(tmp_path, name, text, encoding="utf-8"):
    p = tmp_path / name
    p.write_bytes(text.encode(encoding))
    return p


def test_blank_line_paragraphs_and_chapters(tmp_path):
    text = ("第一章 开始\n\n第一段内容。\n折行继续。\n\n第二段。\n\n"
            "第二章 转折\n\n另一章的内容。\n")
    d = inspect_txt(str(_write(tmp_path, "novel.txt", text)))
    assert d["fmt"] == "txt"
    assert [t[1] for t in d["toc"]] == ["第一章 开始", "第二章 转折"]
    # 第一章: heading + 合并段(折行拼接) + 第二段
    first = d["pages"][0]
    assert first[0].kind == "heading"
    assert first[1].text == "第一段内容。折行继续。"
    assert first[2].text == "第二段。"
    assert "<h2>第一章 开始</h2>" in d["html"][0]


def test_line_per_paragraph_mode(tmp_path):
    # 中文网文常见: 每行一段, 几乎无空行
    text = "\n".join(["第一章 风起"] + [f"第{i}句正文。" for i in range(30)])
    d = inspect_txt(str(_write(tmp_path, "web.txt", text)))
    assert d["toc"][0][1] == "第一章 风起"
    body = [p.text for p in d["pages"][0] if p.kind == "body"]
    assert body[0] == "第0句正文。"
    assert len(body) == 30


def test_gbk_encoding(tmp_path):
    d = inspect_txt(str(_write(tmp_path, "gbk.txt", "第一章 编码\n\n中文内容。\n",
                               encoding="gb18030")))
    assert d["pages"][0][0].text == "第一章 编码"


def test_no_chapters_chunked_by_chars(tmp_path):
    d = inspect_txt(str(_write(tmp_path, "plain.txt",
                               "无章节文本。" * 4000)))  # ~2.4万字, 空行分段一整段
    assert d["toc"] == []
    assert len(d["pages"]) >= 2          # 按字符预算切成多页


def test_empty_txt_rejected(tmp_path):
    with pytest.raises(ValueError, match="可读内容"):
        inspect_txt(str(_write(tmp_path, "empty.txt", "   \n\n  ")))


def test_open_txt_end_to_end(client, tmp_path):
    doc = opened(client, _write(tmp_path, "book.txt",
                                "第一章 测试\n\n你好世界。\n\n再见。\n"))
    assert doc["fmt"] == "txt"
    assert doc["pages"] == 1
    assert doc["lib"]
    r = client.get(f"/api/chapter/1?doc={doc['doc']}")
    assert r.status_code == 200 and "你好世界。" in r.text
    # TXT 不支持划线词坐标
    r = client.get(f"/api/words/1?doc={doc['doc']}")
    assert r.status_code == 400
