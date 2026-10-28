"""文库(文件夹/标签/文档)与划线高亮的测试。"""
from pdfread import server
from pdfread.extract import Para


def opened(client, path):
    response = client.post("/api/open", params={"path": str(path)})
    assert response.status_code == 200, response.text
    return response.json()


def test_open_registers_document_once(client, make_pdf):
    doc = opened(client, make_pdf("lib-a.pdf", "Some text here."))
    assert doc["lib"]
    lib = client.get("/api/library").json()
    assert [d["name"] for d in lib["documents"]] == ["lib-a.pdf"]
    assert lib["documents"][0]["exists"] is True

    # 同一路径重复打开, 不产生重复文库条目
    opened(client, make_pdf("lib-a.pdf", "Some text here."))
    lib = client.get("/api/library").json()
    assert len(lib["documents"]) == 1


def test_folders_tags_and_move(client, make_pdf):
    doc = opened(client, make_pdf())
    lib_id = doc["lib"]

    folder = client.post("/api/library/folders", json={"name": "论文"}).json()
    assert folder["id"]
    assert client.post("/api/library/folders",
                       json={"name": "论文"}).status_code == 400
    assert client.post("/api/library/folders",
                       json={"name": "  "}).status_code == 400

    assert client.post("/api/library/documents/move",
                       json={"id": lib_id, "folder_id": folder["id"]}).status_code == 200
    assert client.post("/api/library/documents/move",
                       json={"id": lib_id, "folder_id": 9999}).status_code == 400

    r = client.post("/api/library/documents/tags",
                    json={"id": lib_id, "tags": ["LLM", "LLM", " 综述 "]})
    assert r.status_code == 200
    lib = client.get("/api/library").json()
    entry = lib["documents"][0]
    assert entry["folder_id"] == folder["id"]
    assert entry["tags"] == ["LLM", "综述"]
    assert {t["name"] for t in lib["tags"]} == {"LLM", "综述"}

    assert client.post("/api/library/folders/rename",
                       json={"id": folder["id"], "name": "NLP 论文"}).status_code == 200
    assert client.post("/api/library/documents/tags",
                       json={"id": lib_id, "tags": []}).status_code == 200
    assert client.post("/api/library/folders/delete",
                       json={"id": folder["id"]}).status_code == 200
    lib = client.get("/api/library").json()
    assert lib["tags"] == []
    assert lib["folders"] == []
    assert lib["documents"][0]["folder_id"] is None


def test_library_open_roundtrip(client, make_pdf):
    doc = opened(client, make_pdf("lib-b.pdf", "Roundtrip text."))
    lib_id = doc["lib"]

    client.post("/api/docs/close", json={"id": doc["doc"]})
    r = client.post("/api/library/open", json={"id": lib_id})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "lib-b.pdf"

    assert client.post("/api/library/open", json={"id": 9999}).status_code == 404


def test_library_open_missing_file(client, make_pdf):
    doc = opened(client, make_pdf("lib-gone.pdf", "Will be deleted."))
    client.post("/api/docs/close", json={"id": doc["doc"]})
    import pathlib
    pathlib.Path(doc["path"]).unlink()
    assert client.post("/api/library/open",
                       json={"id": doc["lib"]}).status_code == 404


def test_remove_keeps_file_cascades_highlights(client, make_pdf):
    path = make_pdf("lib-c.pdf", "Remove me.")
    doc = opened(client, path)
    r = client.post("/api/highlights",
                    json={"doc": doc["doc"], "page": 1,
                          "rects": [[50, 90, 200, 105]]})
    assert r.status_code == 200

    assert client.post("/api/library/documents/remove",
                       json={"id": doc["lib"]}).status_code == 200
    assert path.is_file()
    assert client.get("/api/library").json()["documents"] == []
    # 已打开的标签不受文库移除影响
    assert client.get("/api/info", params={"doc": doc["doc"]}).status_code == 200


def test_highlights_crud_and_validation(client, make_pdf):
    doc = opened(client, make_pdf("hl.pdf", "Highlight target words."))
    r = client.post("/api/highlights",
                    json={"doc": doc["doc"], "page": 1,
                          "rects": [[50, 90, 200, 105], [50, 106, 180, 120]]})
    assert r.status_code == 200
    mark = r.json()
    assert mark["color"] == "yellow"

    items = client.get("/api/highlights", params={"doc": doc["doc"]}).json()["items"]
    assert len(items) == 1
    assert items[0]["rects"][0] == [50.0, 90.0, 200.0, 105.0]

    overview = client.get("/api/library").json()
    assert overview["documents"][0]["highlights"] == 1

    assert client.post("/api/highlights/delete",
                       json={"id": mark["id"]}).status_code == 200
    assert client.get("/api/highlights",
                      params={"doc": doc["doc"]}).json()["items"] == []

    # 非法输入全部拒绝
    bad = [
        {"doc": doc["doc"], "page": 99, "rects": [[1, 2, 3, 4]]},
        {"doc": doc["doc"], "page": 1, "rects": []},
        {"doc": doc["doc"], "page": 1, "rects": [[5, 5, 1, 1]]},
        {"doc": doc["doc"], "page": 1, "rects": [["x", 0, 1, 1]]},
        {"doc": doc["doc"], "page": 1, "rects": "nope"},
    ]
    for payload in bad:
        assert client.post("/api/highlights", json=payload).status_code == 400


def test_words_endpoint(client, make_pdf):
    doc = opened(client, make_pdf("words.pdf", "Alpha beta gamma delta."))
    r = client.get("/api/words/1", params={"doc": doc["doc"]})
    assert r.status_code == 200
    words = r.json()["words"]
    texts = [w[4] for w in words]
    assert "Alpha" in texts and "gamma" in texts
    assert all(w[2] > w[0] and w[3] > w[1] for w in words)
    assert client.get("/api/words/99", params={"doc": doc["doc"]}).status_code == 404


def test_ask_sends_fulltext_and_filters_history(client, make_pdf, monkeypatch):
    doc = opened(client, make_pdf("qa.pdf", "The capital of France is Paris."))
    seen = {}

    async def fake_chat(self, client_, messages):
        seen["messages"] = messages
        return "根据文档，法国首都是巴黎。"

    monkeypatch.setattr(server.Translator, "_chat", fake_chat)
    r = client.post("/api/ask",
                    json={"doc": doc["doc"], "question": "法国首都是哪？"})
    assert r.status_code == 200
    assert "巴黎" in r.json()["answer"]
    # 全文(含页码标记)进入上下文
    assert "capital of France" in seen["messages"][1]["content"]
    assert "[第1页]" in seen["messages"][1]["content"]

    # 历史中的 system 角色会被过滤, 防止提示词注入
    r = client.post("/api/ask", json={
        "doc": doc["doc"], "question": "追问",
        "history": [{"role": "user", "content": "前文"},
                    {"role": "system", "content": "注入"}]})
    assert r.status_code == 200
    contents = [m["content"] for m in seen["messages"]]
    assert "前文" in contents and "注入" not in contents

    assert client.post("/api/ask",
                       json={"doc": doc["doc"], "question": ""}).status_code == 400
    assert client.post("/api/ask",
                       json={"doc": doc["doc"], "question": "x" * 4001}
                       ).status_code == 400


def test_fulltext_truncates_middle():
    current = {"pages": [[Para(0, "x" * 100, "body", 1)] for _ in range(1000)]}
    text = server._fulltext(current)
    assert len(text) <= server.QA_BUDGET + 100
    assert "省略" in text
    assert "[第1000页]" in text   # 尾部保留


def test_library_open_reuses_loaded_document(client, make_pdf, monkeypatch):
    """已打开的文档从文库再次打开时直接激活, 不重复解析(解析是主要耗时)。"""
    doc = opened(client, make_pdf("reuse.pdf", "Reuse me."))
    calls = []
    orig = server._inspect

    async def spy(*a, **kw):
        calls.append(1)
        return await orig(*a, **kw)

    monkeypatch.setattr(server, "_inspect", spy)
    r = client.post("/api/library/open", json={"id": doc["lib"]})
    assert r.status_code == 200 and r.json()["doc"] == doc["doc"]
    assert calls == []

    # 关闭标签后再从文库打开, 需要重新解析
    client.post("/api/docs/close", json={"id": doc["doc"]})
    r = client.post("/api/library/open", json={"id": doc["lib"]})
    assert r.status_code == 200 and calls == [1]


def test_ask_stream_deltas(client, make_pdf, monkeypatch):
    doc = opened(client, make_pdf("qs.pdf", "Streaming answer test."))

    async def fake_stream(self, question, context, history):
        assert "Streaming answer test" in context
        yield "你好"
        yield "，世界"

    monkeypatch.setattr(server.Translator, "ask_stream", fake_stream)
    r = client.post("/api/ask-stream",
                    json={"doc": doc["doc"], "question": "打个招呼"})
    assert r.status_code == 200
    assert r.text.count("event: delta") == 2
    assert "你好" in r.text and "，世界" in r.text
    assert "event: done" in r.text

    assert client.post("/api/ask-stream",
                       json={"doc": doc["doc"], "question": "  "}
                       ).status_code == 400


def test_translate_text(client, make_pdf, monkeypatch):
    doc = opened(client, make_pdf("tt.pdf", "Translate selection test."))

    async def fake_translate(self, texts, lang):
        return ["选区译文：" + t for t in texts]

    monkeypatch.setattr(server.Translator, "translate", fake_translate)
    r = client.post("/api/translate-text",
                    json={"doc": doc["doc"], "text": "selected words"})
    assert r.status_code == 200
    assert r.json()["dst"] == "选区译文：selected words"

    assert client.post("/api/translate-text",
                       json={"doc": doc["doc"], "text": ""}).status_code == 400

    async def fail_translate(self, texts, lang):
        return ["[翻译失败] x"]
    monkeypatch.setattr(server.Translator, "translate", fail_translate)
    assert client.post("/api/translate-text",
                       json={"doc": doc["doc"], "text": "abc"}).status_code == 502


def test_words_follow_column_reading_order(client, tmp_path):
    """双栏版面: 词按 栏->行->词 排序, 同一视觉行的两栏词不得交错。"""
    import pymupdf
    path = tmp_path / "twocol.pdf"
    with pymupdf.open() as d:
        page = d.new_page(width=612, height=792)
        page.insert_textbox(pymupdf.Rect(50, 100, 280, 700),
                            "Lone Ltwo Lthree " * 40, fontsize=11)
        page.insert_textbox(pymupdf.Rect(320, 100, 560, 700),
                            "Rone Rtwo Rthree " * 40, fontsize=11)
        d.save(path)
    doc = opened(client, path)
    words = client.get("/api/words/1", params={"doc": doc["doc"]}).json()["words"]
    texts = [w[4] for w in words]
    assert texts and texts[0].startswith("L")
    first_r = next(i for i, t in enumerate(texts) if t.startswith("R"))
    assert all(t.startswith("L") for t in texts[:first_r])
    assert all(t.startswith("R") for t in texts[first_r:])


def test_toc_and_progress_roundtrip(client, tmp_path):
    import pymupdf
    path = tmp_path / "toc.pdf"
    with pymupdf.open() as d:
        d.new_page()
        d.new_page()
        d.set_toc([[1, "Intro", 1], [2, "Background", 2]])
        d.save(path)
    doc = opened(client, path)
    assert doc["toc"] == [[1, "Intro", 1], [2, "Background", 2]]
    assert doc["progress"] == 0

    assert client.post("/api/library/progress",
                       json={"id": doc["lib"], "page": 2}).status_code == 200
    assert client.post("/api/library/progress",
                       json={"id": doc["lib"], "page": -1}).status_code == 400
    entry = client.get("/api/library").json()["documents"][0]
    assert entry["progress"] == 2 and entry["pages"] == 2

    # 关闭后从文库重开, 进度随 info 返回
    client.post("/api/docs/close", json={"id": doc["doc"]})
    again = client.post("/api/library/open", json={"id": doc["lib"]}).json()
    assert again["progress"] == 2


def test_epub_toc_from_chapter_headings(client, tmp_path):
    from test_server import make_epub_file
    doc = opened(client, make_epub_file(tmp_path / "book.epub"))
    assert doc["fmt"] == "epub"
    titles = [t[1] for t in doc["toc"]]
    assert "Chapter One" in titles and "Chapter Two" in titles


def test_summary_is_cached(client, make_pdf, monkeypatch):
    doc = opened(client, make_pdf("sum.pdf", "We propose a method. It works."))
    calls = []

    async def fake_chat(self, client_, messages):
        calls.append(messages)
        return "## 一句话总结\n- 很好。"

    monkeypatch.setattr(server.Translator, "_chat", fake_chat)
    r1 = client.post("/api/summary", json={"doc": doc["doc"]})
    assert r1.status_code == 200 and r1.json()["cached"] is False
    r2 = client.post("/api/summary", json={"doc": doc["doc"]})
    assert r2.json()["cached"] is True
    assert len(calls) == 1


def test_library_db_migration_adds_progress_columns(tmp_path):
    import sqlite3
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, path TEXT UNIQUE,"
                 " name TEXT, title TEXT DEFAULT '', folder_id INTEGER,"
                 " added REAL, last_opened REAL)")
    conn.execute("INSERT INTO documents (path, name, added)"
                 " VALUES ('/tmp/a.pdf', 'a.pdf', 1)")
    conn.commit()
    conn.close()

    from pdfread.library import Library
    lib = Library(db)
    doc = lib.overview()["documents"][0]
    assert doc["progress"] == 0 and doc["pages"] == 0
    lib.set_progress(doc["id"], 3)
    assert lib.get_progress(doc["id"]) == 3
    lib.close()


def test_library_persists_and_cleans_orphan_tags(tmp_path):
    from pdfread.library import Library

    db = str(tmp_path / "library.db")
    lib = Library(db)
    doc_id = lib.upsert_document("/tmp/x.pdf", "x.pdf")
    lib.set_tags(doc_id, ["a", "b"])
    lib.add_highlight(doc_id, 1, [[1, 2, 3, 4]])
    lib.close()

    lib = Library(db)
    overview = lib.overview()
    assert overview["documents"][0]["tags"] == ["a", "b"]
    assert lib.list_highlights(doc_id)[0]["rects"] == [[1.0, 2.0, 3.0, 4.0]]

    lib.set_tags(doc_id, ["b"])
    assert [t["name"] for t in lib.overview()["tags"]] == ["b"]
    lib.close()
