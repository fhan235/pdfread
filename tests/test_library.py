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
