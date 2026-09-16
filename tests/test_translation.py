import asyncio
import re

import httpx
import pytest

from pdfread.translate import Cache, TransConfig, Translator


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
