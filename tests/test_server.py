import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

from pdfread import server
from pdfread.extract import Para


def opened(client, path):
    response = client.post("/api/open", params={"path": str(path)})
    assert response.status_code == 200, response.text
    return response.json()


def test_bad_pdf_preserves_current_document(client, make_pdf, tmp_path):
    good = opened(client, make_pdf())
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    assert client.post("/api/open", params={"path": str(bad)}).status_code == 400
    assert client.get("/api/info").json()["doc"] == good["doc"]
    assert client.get("/api/page/1.png", params={"doc": good["doc"]}).content.startswith(b"\x89PNG")


def test_document_isolation_and_concurrent_render(client, make_pdf):
    first = opened(client, make_pdf("one.pdf", "This is the first document."))
    second = opened(client, make_pdf("two.pdf", "This is the second document."))
    assert first["doc"] != second["doc"]
    for doc, word in ((first, "first"), (second, "second")):
        response = client.get("/api/text/1", params={"doc": doc["doc"]})
        assert word in response.json()["paras"][0]["text"]
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: client.get("/api/page/1.png", params={"doc": first["doc"]}), range(4)))
    assert all(r.status_code == 200 and r.content.startswith(b"\x89PNG") for r in results)
    assert client.get("/api/info", params={"doc": "expired"}).status_code == 410


def test_same_name_uploads_and_failed_upload_do_not_overwrite(client, make_pdf, monkeypatch):
    a = make_pdf("a.pdf").read_bytes()
    b = make_pdf("b.pdf").read_bytes()
    first = client.post("/api/upload", files={"file": ("same.pdf", a, "application/pdf")}).json()
    second = client.post("/api/upload", files={"file": ("same.pdf", b, "application/pdf")}).json()
    assert first["path"] != second["path"]
    assert first["name"] == second["name"] == "same.pdf"
    monkeypatch.setattr(server, "MAX_UPLOAD", 4)
    assert client.post("/api/upload", files={"file": ("same.pdf", b)}).status_code == 413
    assert client.get("/api/raw", params={"doc": first["doc"]}).content == a
    assert client.get("/api/raw", params={"doc": second["doc"]}).content == b
    assert not list(server.uploads_dir().glob("*.part"))


def test_invalid_upload_is_cleaned(client):
    assert client.post("/api/upload", files={"file": ("bad.pdf", b"garbage")}).status_code == 400
    assert not list(server.uploads_dir().iterdir())


def test_settings_preserve_overrides_and_reject_atomically(client):
    from pdfread.settings import translation_config
    good = {"provider": "deepseek", "model": "custom", "base_url": "https://example.com/v1", "key": "test-key"}
    assert client.post("/api/settings", json=good).status_code == 200
    assert client.post("/api/settings", json={"provider": "deepseek", "key": "updated"}).status_code == 200
    cfg = translation_config()
    assert (cfg.provider, cfg.model, cfg.base_url) == ("deepseek", "custom", "https://example.com/v1")
    assert client.post("/api/settings", json={"provider": "ollama", "key": "wrong"}).status_code == 400
    assert server.STATE["cfg"].provider == "deepseek"
    assert client.post("/api/settings", json={"base_url": "https://example.com/v1?key=secret"}).status_code == 400
    assert "updated" not in client.get("/api/settings").text


def test_host_origin_and_path_boundaries(client, tmp_path):
    assert client.get("/api/info", headers={"Host": "evil.example"}).status_code == 400
    assert client.post("/api/roots", headers={"Origin": "https://evil.example"}, json={"path": str(tmp_path)}).status_code == 403
    assert client.get("/api/translate", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post("/api/open", params={"path": str(tmp_path.parent / "elsewhere.pdf")}).status_code == 403


def test_page_range_validation(client, make_pdf):
    doc = opened(client, make_pdf())
    for start, end in ((0, 1), (2, 1), (1, 2), (1, -1)):
        assert client.get("/api/translate", params={"doc": doc["doc"], "start": start, "end": end}).status_code == 400


def test_fast_page_streams_before_slow_page(client, monkeypatch, tmp_path):
    doc = server._register(tmp_path / "synthetic.pdf", {
        "pages": [[Para(0, "slow", "body", 1)], [Para(0, "fast", "body", 1)]],
        "sizes": [(600, 800)] * 2, "title": "", "stamp": (0, 0)})
    async def translate(self, texts, lang):
        await asyncio.sleep(0.03 if texts == ["slow"] else 0)
        return ["译文"]
    monkeypatch.setattr(server.Translator, "translate", translate)
    response = client.get("/api/translate", params={"doc": doc["id"]})
    events = [json.loads(block.split("data: ")[1]) for block in response.text.split("\n\n") if block.startswith("event: page")]
    assert [e["page"] for e in events] == [2, 1]
    assert all(e["doc"] == doc["id"] for e in events)


def test_stream_reports_partial_failure(client, monkeypatch, make_pdf):
    doc = opened(client, make_pdf())
    async def fail(self, texts, lang):
        return ["[翻译失败] test"] * len(texts)
    monkeypatch.setattr(server.Translator, "translate", fail)
    response = client.get("/api/translate", params={"doc": doc["doc"]})
    assert '"status": "failed"' in response.text
    assert '"ok": false' in response.text


def test_disconnect_cancels_unfinished_pages(client, monkeypatch, tmp_path):
    doc = server._register(tmp_path / "synthetic.pdf", {
        "pages": [[Para(0, "fast", "body", 1)], [Para(0, "slow", "body", 1)]],
        "sizes": [(600, 800)] * 2, "title": "", "stamp": (0, 0)})
    cancelled = []
    async def translate(self, texts, lang):
        if texts == ["slow"]:
            try:
                await asyncio.sleep(100)
            finally:
                cancelled.append(True)
        return ["译文"]
    monkeypatch.setattr(server.Translator, "translate", translate)
    async def run():
        response = await server.translate_stream(doc=doc["id"])
        stream = response.body_iterator
        assert (await anext(stream)).startswith("event: meta")
        assert (await anext(stream)).startswith("event: page")
        await stream.aclose()
    asyncio.run(run())
    assert cancelled == [True]


def test_malformed_result_reports_stream_failure(client, monkeypatch, make_pdf):
    doc = opened(client, make_pdf())
    async def translate(self, texts, lang):
        return []
    monkeypatch.setattr(server.Translator, "translate", translate)
    response = client.get("/api/translate", params={"doc": doc["doc"]})
    assert "event: failure" in response.text
    assert "event: done" not in response.text


def test_open_url_rejects_internal_target(client):
    response = client.post("/api/open-url", json={"url": "http://127.0.0.1/x.pdf"})
    assert response.status_code == 400
    assert "拦截" in response.text or "仅支持" in response.text


def test_open_url_registers_document(client, make_pdf, monkeypatch):
    import pdfread.urlfetch as urlfetch
    src = make_pdf("remote.pdf")
    monkeypatch.setattr(urlfetch, "fetch_document", lambda *a, **kw: (src, "remote.pdf"))
    response = client.post("/api/open-url", json={"url": "https://arxiv.org/abs/2501.01423"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["loaded"] and data["name"] == "remote.pdf"
    assert client.get("/api/page/1.png", params={"doc": data["doc"]}).status_code == 200


def test_failed_open_restarts_worker(client, tmp_path):
    # 解析失败必须重建进程池: PyMuPDF 打开失败会在 C 层残留句柄,
    # 常驻 worker 不重启的话, Windows 上文件会被锁住无法清理
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"garbage")
    assert client.post("/api/open", params={"path": str(bad)}).status_code == 400
    assert server._pool is None


def test_docs_list_close_and_dedupe(client, make_pdf):
    first = opened(client, make_pdf("tab-a.pdf", "Document A."))
    second = opened(client, make_pdf("tab-b.pdf", "Document B."))

    listed = client.get("/api/docs").json()
    assert listed["active"] == second["doc"]
    assert [d["id"] for d in listed["docs"]] == [first["doc"], second["doc"]]

    # 同一路径重复打开: 复用原 id, 不产生新文档
    again = opened(client, make_pdf("tab-a.pdf", "Document A."))
    assert again["doc"] == first["doc"]
    assert len(client.get("/api/docs").json()["docs"]) == 2

    # 关闭非活动文档: 活动文档不变
    closed = client.post("/api/docs/close", json={"id": first["doc"]}).json()
    assert closed["existed"] is True
    assert client.get("/api/info", params={"doc": first["doc"]}).status_code == 410
    assert client.get("/api/info").json()["doc"] == second["doc"]

    # 关闭活动文档: 切到剩余文档; 全部关闭后回到未加载
    client.post("/api/docs/close", json={"id": again["doc"]})
    client.post("/api/docs/close", json={"id": second["doc"]})
    assert client.get("/api/info").json()["loaded"] is False
    assert client.post("/api/docs/close", json={"id": "ghost"}).json()["existed"] is False


def make_epub_file(path):
    import zipfile
    container = ('<?xml version="1.0"?><container version="1.0" '
                 'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                 '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                 'media-type="application/oebps-package+xml"/></rootfiles></container>')
    opf = ('<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
           'version="3.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
           '<dc:title>Test Book</dc:title></metadata><manifest>'
           '<item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>'
           '<item id="c2" href="ch2.xhtml" media-type="application/xhtml+xml"/>'
           '</manifest><spine><itemref idref="c1"/><itemref idref="c2"/></spine></package>')
    ch1 = ('<html><body><h1>Chapter One</h1><p>First paragraph of the book.</p>'
           '<p>Second paragraph with <b>bold text</b>.</p><script>alert(1)</script>'
           '<p><img src="pic.png"/></p></body></html>')
    ch2 = ('<html><body><h2>Chapter Two</h2><ul><li>Item one here</li>'
           '<li>Item two here</li></ul></body></html>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/container.xml", container)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/ch1.xhtml", ch1)
        z.writestr("OEBPS/ch2.xhtml", ch2)
        z.writestr("OEBPS/pic.png", b"\x89PNG\r\n\x1a\nfake")
    return path


def test_epub_open_translate_and_chapter(client, tmp_path, make_pdf):
    doc = opened(client, make_epub_file(tmp_path / "book.epub"))
    assert doc["fmt"] == "epub"
    assert doc["title"] == "Test Book"
    assert doc["pages"] == 2

    text = client.get("/api/text/1", params={"doc": doc["doc"]}).json()
    kinds = [p["kind"] for p in text["paras"]]
    assert kinds[0] == "heading" and "Chapter One" in text["paras"][0]["text"]
    assert "bold text" in text["paras"][2]["text"]

    html = client.get("/api/chapter/1", params={"doc": doc["doc"]}).text
    assert "Chapter One" in html and "First paragraph" in html
    assert "<script>" not in html and "alert" not in html
    assert "__EPUB_RES__OEBPS/pic.png" in html

    res = client.get("/api/epub-res/1", params={"doc": doc["doc"], "path": "OEBPS/pic.png"})
    assert res.status_code == 200 and res.headers["content-type"] == "image/png"
    assert client.get("/api/epub-res/1", params={"doc": doc["doc"], "path": "../content.opf"}).status_code == 404
    assert client.get("/api/page/1.png", params={"doc": doc["doc"]}).status_code == 400

    # EPUB 与 PDF 标签共存互不影响
    pdf = opened(client, make_pdf())
    assert client.get("/api/chapter/1", params={"doc": pdf["doc"]}).status_code == 400
    assert client.get("/api/page/1.png", params={"doc": pdf["doc"]}).status_code == 200
