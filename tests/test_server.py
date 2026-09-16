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
    monkeypatch.setattr(urlfetch, "fetch_document", lambda url, dest, kind: (src, "remote.pdf"))
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
