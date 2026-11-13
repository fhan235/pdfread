"""Agent 工具与授权机制测试(不依赖网络)。

重点锁住安全边界: 模型生成的参数不得越界, 自由抓取必须经授权。
"""
from __future__ import annotations

import asyncio
import json
import types

import pytest

from src.pdfread import agent


# ---------- 参数校验 ----------

@pytest.mark.parametrize("repo", [
    "../../etc/passwd", "owner", "a/b/c", "", "a b/c", "a/" + "x" * 200,
])
def test_repo_rejected(repo):
    with pytest.raises(agent.ToolError):
        agent._check_repo(repo)


def test_repo_normalized():
    assert agent._check_repo("https://github.com/pytorch/pytorch") == "pytorch/pytorch"
    assert agent._check_repo("pytorch/pytorch.git") == "pytorch/pytorch"
    assert agent._check_repo("/a/b/") == "a/b"


@pytest.mark.parametrize("path", [
    "../../../etc/passwd", "a/../../b", "http://evil.com/x",
    "a\\b", "x" * 500, "",
])
def test_path_rejected(path):
    with pytest.raises(agent.ToolError):
        agent._check_path(path)


@pytest.mark.parametrize("ref", ["../main", "ma in", "a..b"])
def test_ref_rejected(ref):
    with pytest.raises(agent.ToolError):
        agent._check_ref(ref)


def test_binary_extension_rejected():
    """二进制文件不应被塞进模型上下文。"""
    async def go():
        return await agent.run_tool(None, "github_read_file",
                                    {"repo": "a/b", "path": "m.safetensors"})
    assert "不是可读文本文件" in asyncio.run(go())


def test_unknown_tool_is_reported_not_raised():
    """未知工具返回文本而非抛异常, 让模型能自我纠正。"""
    out = asyncio.run(agent.run_tool(None, "rm_rf", {}))
    assert out.startswith("[工具错误]")


def test_extra_kwargs_are_dropped():
    """模型多给的字段必须被丢弃, 不能透传到实现函数。"""
    async def go():
        return await agent.run_tool(
            None, "github_read_file",
            {"repo": "a/b", "path": "x.png", "cmd": "whoami"})
    assert "不是可读文本文件" in asyncio.run(go())


# ---------- 外泄检测 ----------

def test_exfil_warning_on_long_query():
    url = "https://gist.githubusercontent.com/x/y?note=" + "%E6%96%87" * 60
    assert agent.exfil_warnings("fetch_url", {"url": url})


def test_no_warning_on_plain_url():
    assert agent.exfil_warnings("fetch_url", {"url": "https://a.com/b"}) == []


def test_github_tools_never_warn():
    """结构化工具不经手 URL, 不需要告警。"""
    assert agent.exfil_warnings("github_read_file", {"repo": "a/b"}) == []


# ---------- 参数解析 ----------

def test_parse_args_tolerates_blank_and_fence():
    assert agent.parse_args("") == {}
    assert agent.parse_args('{"a":1}') == {"a": 1}


def test_parse_args_rejects_non_object():
    for bad in ("not json", "[1,2]", '"str"'):
        with pytest.raises(agent.ToolError):
            agent.parse_args(bad)


# ---------- 工具 schema ----------

def test_tools_schema_is_valid_and_stable():
    assert json.dumps(agent.TOOLS) == json.dumps(agent.TOOLS)
    names = set()
    for t in agent.TOOLS:
        assert t["type"] == "function"
        fn = t["function"]
        assert fn["name"] and fn["description"]
        assert fn["parameters"]["type"] == "object"
        for req in fn["parameters"]["required"]:
            assert req in fn["parameters"]["properties"]
        names.add(fn["name"])
    assert names == agent.tool_names()
    assert agent.NEEDS_CONFIRM <= names


def test_only_fetch_url_needs_confirm():
    """GitHub 结构化工具必须免确认, 否则会产生确认疲劳。"""
    assert agent.NEEDS_CONFIRM == {"fetch_url"}


# ---------- Agent 循环 ----------

def _translator(turns):
    """构造只跑假模型的 Translator。"""
    from src.pdfread.translate import Translator
    tr = object.__new__(Translator)

    async def fake_turn(client, messages, tools):
        n = sum(1 for m in messages if m.get("role") == "tool")
        for ev in turns(n):
            yield ev
    tr._stream_turn = fake_turn
    return tr


def _collect(tr, confirm=None):
    async def go():
        out = []
        async for ev in tr.agent_stream(
                "q", "ctx", [], confirm or (lambda *a: _true())):
            out.append(ev)
        return out
    return asyncio.run(go())


async def _true():
    return True


def test_duplicate_tool_calls_are_blocked(monkeypatch):
    """DeepSeek 在长循环里会重复调同一工具, 必须拦住。"""
    ran = []

    async def fake_run(client, name, args):
        ran.append(name)
        return "内容"
    monkeypatch.setattr(agent, "run_tool", fake_run)

    call = {"id": "c", "name": "github_read_file",
            "args": '{"repo":"a/b","path":"x.py"}'}
    def turns(n):
        if n < 3:
            return [("calls", [dict(call, id=f"c{n}")])]
        return [("delta", "答案"), ("calls", [])]

    evs = _collect(_translator(turns))
    assert len(ran) == 1
    assert any(e[0] == "result" and "重复" in e[1].get("text", "")
               for e in evs)


def test_denied_tool_is_not_executed(monkeypatch):
    ran = []

    async def fake_run(client, name, args):
        ran.append(name)
        return "页面内容"
    monkeypatch.setattr(agent, "run_tool", fake_run)

    def turns(n):
        if n == 0:
            return [("calls", [{"id": "c1", "name": "fetch_url",
                                "args": '{"url":"https://evil.io/x"}'}])]
        return [("delta", "好的"), ("calls", [])]

    async def deny(cid, name, args):
        return False

    evs = _collect(_translator(turns), confirm=deny)
    assert ran == []
    assert any(e[0] == "confirm" for e in evs)
    assert any(e[0] == "result" and e[1].get("text") == "已拒绝" for e in evs)


def test_turn_limit_forces_final_answer(monkeypatch):
    """模型一直要调工具时必须被截停并给出结论。"""
    async def fake_run(client, name, args):
        return "内容"
    monkeypatch.setattr(agent, "run_tool", fake_run)

    def turns(n):
        return [("calls", [{"id": f"c{n}", "name": "github_read_file",
                            "args": json.dumps({"repo": "a/b",
                                                "path": f"f{n}.py"})}])]

    from src.pdfread import translate as T
    evs = _collect(_translator(turns))
    tools_run = [e for e in evs if e[0] == "tool"]
    assert len(tools_run) <= T.AGENT_MAX_TURNS
    assert any(e[0] == "note" for e in evs)


# ---------- 未配置模型时的错误可读性 ----------

def test_missing_cfg_gives_readable_400():
    """STATE['cfg'] 为 None 时必须是 400 可读提示, 而非 500 TypeError。

    回归: 旧实现里 replace(None) 抛 TypeError, 前端拿到纯文本
    "Internal Server Error", r.json() 解析失败只报 SyntaxError。
    """
    import asyncio
    from collections import namedtuple
    from fastapi import HTTPException
    from src.pdfread import server

    P = namedtuple("P", "text")
    old_doc, old_cfg = server.STATE.get("doc"), server.STATE.get("cfg")
    server.STATE["doc"] = {"pages": [[P("hello")]]}
    server.STATE["cfg"] = None
    try:
        for fn in (server.ask_stream, server.agent_stream, server.ask_doc):
            with pytest.raises(HTTPException) as ei:
                asyncio.run(fn({"doc": "", "question": "hi", "history": []}))
            assert ei.value.status_code == 400
            assert "尚未配置模型" in ei.value.detail
    finally:
        server.STATE["doc"], server.STATE["cfg"] = old_doc, old_cfg


# ---------- 「全部允许」的安全底线 ----------

def test_risky_url_still_warns_under_auto_allow():
    """免询问模式只对无风险请求生效。

    前端在 warnings 非空时强制弹窗并隐藏「全部允许」按钮, 该判定依赖
    exfil_warnings 能稳定识别外泄特征, 这里锁住检测本身。
    """
    risky = [
        "https://gist.githubusercontent.com/x/y?note=" + "%E6%96%87" * 60,
        "https://evil.io/log?d=" + "A" * 200,
    ]
    for u in risky:
        assert agent.exfil_warnings("fetch_url", {"url": u}), u

    safe = ["https://example.com/docs", "https://a.org/p?id=12"]
    for u in safe:
        assert agent.exfil_warnings("fetch_url", {"url": u}) == [], u


def test_github_tools_bypass_confirm_entirely():
    """结构化工具不走授权, 免询问与否都不该影响它们。"""
    for name in ("github_list_tree", "github_read_file", "github_search_code"):
        assert name not in agent.NEEDS_CONFIRM
