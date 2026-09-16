import pytest
from pdfread import server
from pdfread.translate import TransConfig


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("PDFREAD_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("PDFREAD_CACHE_DIR", str(tmp_path / "cache"))
    for key in ("PDFREAD_PROVIDER", "PDFREAD_MODEL", "PDFREAD_BASE_URL", "DEEPSEEK_API_KEY", "SILICON_API_KEY", "DASHSCOPE_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def client(tmp_path):
    from fastapi.testclient import TestClient
    server.configure(None, [tmp_path], TransConfig(provider="ollama"), tmp_path / "translations.db")
    with TestClient(server.app, base_url="http://127.0.0.1") as value:
        yield value


@pytest.fixture
def make_pdf(tmp_path):
    import pymupdf
    def make(name="paper.pdf", text="This is an example paragraph for translation."):
        path = tmp_path / name
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((60, 100), text)
            doc.set_metadata({"title": name})
            doc.save(path)
        return path
    return make
