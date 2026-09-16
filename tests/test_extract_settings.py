import os
import pymupdf

from pdfread.extract import extract_pages
from pdfread import settings


def test_appendix_after_references_is_preserved(tmp_path):
    path = tmp_path / "refs.pdf"
    with pymupdf.open() as doc:
        for i in range(4):
            page = doc.new_page()
            title = ["Introduction", "Results", "References", "Appendix A"][i]
            page.insert_text((60, 100), title, fontsize=18)
            page.insert_text((60, 160), "This is the ordinary body paragraph for this section.", fontsize=11)
        doc.save(path)
    pages, _ = extract_pages(str(path))
    assert not pages[2]
    assert any("Appendix" in p.text for p in pages[3])
    all_pages, _ = extract_pages(str(path), skip_references=False)
    assert all_pages[2]


def test_current_double_column_reading_order_is_unchanged(tmp_path):
    path = tmp_path / "columns.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page(width=600, height=800)
        for prefix, x in (("Left", 40), ("Right", 340)):
            for i in range(5):
                page.insert_text((x, 100 + i * 60), f"{prefix} paragraph number {i}.", fontsize=11)
        doc.save(path)
    pages, _ = extract_pages(str(path))
    assert [p.text.split()[0] for p in pages[0]] == ["Left", "Right"] * 5


def test_config_priority_and_permissions(monkeypatch):
    settings.update_translation("ollama", "saved-model", "http://localhost:9000/v1")
    assert settings.translation_config().model == "saved-model"
    monkeypatch.setenv("PDFREAD_MODEL", "env-model")
    assert settings.translation_config().model == "env-model"
    assert settings.translation_config(model="cli-model").model == "cli-model"
    assert settings.config_path().parent != settings.cache_dir()
    if os.name != "nt":
        assert settings.config_path().stat().st_mode & 0o777 == 0o600


def test_legacy_config_migrates_before_cache_cleanup(tmp_path, monkeypatch):
    import json
    monkeypatch.delenv("PDFREAD_CONFIG_DIR")
    monkeypatch.setattr(settings, "config_dir", lambda: tmp_path / "persistent")
    legacy = settings.cache_dir() / "config.json"
    legacy.write_text(json.dumps({"provider": "ollama", "keys": {}}))
    assert settings.get_provider() == "ollama"
    assert settings.config_path().exists()
    legacy.unlink()
    assert settings.get_provider() == "ollama"
