import asyncio
import re

import httpx
import pytest

from pdfread.translate import (QA_PROMPT, SUMMARY_PROMPT, Cache, TransConfig,
                               Translator)


@pytest.fixture
def translator():
    cache = Cache(":memory:")
    yield Translator(TransConfig(provider="ollama", max_chars=100), cache)
    cache.close()


@pytest.mark.parametrize("value", [0, -1, 33])
def test_invalid_concurrency(value):
    with pytest.raises(ValueError):
        TransConfig(concurrency=value)


def test_format_failure_is_not_original_or_cached(translator):
    calls = []
    async def broken(client, payload):
        calls.append(payload)
        return "<<<999>>> invalid"
    translator._call = broken
    result = asyncio.run(translator.translate(["First source", "Second source"]))
    assert all(s.startswith("[翻译失败]") for s in result)
    assert len(calls) == 3  # one batch plus retries for missing paragraphs
    asyncio.run(translator.translate(["First source", "Second source"]))
    assert len(calls) == 6


def test_only_missing_paragraph_retried(translator):
    calls = []
    async def partial(client, payload):
        calls.append(payload)
        return "<<<0>>> 第一段" if len(calls) == 1 else "<<<0>>> 第二段"
    translator._call = partial
    assert asyncio.run(translator.translate(["First", "Second"])) == ["第一段", "第二段"]
    assert calls[-1] == "<<<0>>>\nSecond"
    assert asyncio.run(translator.translate(["First", "Second"])) == ["第一段", "第二段"]
    assert len(calls) == 2


def test_long_paragraph_split_and_reassembled(translator):
    payloads = []
    async def echo(client, payload):
        payloads.append(payload)
        return payload
    translator._call = echo
    text = "word " * 81
    result = asyncio.run(translator.translate([text, "another"]))
    assert result[0].split() == text.split()
    assert result[1] == "another"
    for payload in payloads:
        parts = re.split(r"<<<\d+>>>\n", payload)[1:]
        assert sum(len(p.strip()) for p in parts) <= 100


def test_endpoint_separates_cache(translator):
    other = Translator(TransConfig(provider="ollama", base_url="http://localhost:9999/v1"), translator.cache)
    assert translator.cache_model != other.cache_model


@pytest.mark.parametrize("prompt", [QA_PROMPT, SUMMARY_PROMPT])
def test_math_must_be_latex_wrapped(prompt):
    """前端用 KaTeX 渲染, 提示词必须要求数学一律写成 LaTeX。

    少了这条, 模型只给独立公式加 $$, 行内符号写成裸文本(d²_hat{q}、λ_bound),
    既进不了渲染器, 还会把 hat{} 这种命令名直接显示给用户。
    """
    assert "$...$" in prompt and "$$...$$" in prompt
    assert "LaTeX" in prompt
    assert "\\hat{q}" in prompt      # 明确给出正例, 否则模型爱写 hat{q}
    assert "Unicode" in prompt       # 禁止 d² 这类上下标字符


def test_summary_cache_tracks_its_own_prompt(translator):
    """摘要缓存键必须绑定 SUMMARY_PROMPT。

    否则改了摘要提示词后旧缓存仍命中, 用户永远看不到新格式。
    """
    assert translator.summary_model != translator.cache_model
    assert translator.summary_model != translator.polish_model
    import hashlib
    assert hashlib.sha256(SUMMARY_PROMPT.encode()).hexdigest() in translator.summary_model


def test_reject_unsupported_language(translator):
    with pytest.raises(ValueError):
        asyncio.run(translator.translate(["hello"], "fr"))


def test_unauthorized_not_retried(translator):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(401, json={"error": "sensitive upstream details"})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(RuntimeError, match="HTTP 401") as exc:
                await translator._call(client, "hello")
            assert "sensitive" not in str(exc.value)
    asyncio.run(run())
    assert len(calls) == 1


def test_cancellation_propagates(translator):
    async def slow(client, payload):
        await asyncio.sleep(100)
    translator._call = slow
    async def run():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(translator.translate(["hello"]), 0.03)
    asyncio.run(run())
