"""Required completion policy must cover real streaming dispatch paths."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def chunk(content=None, tools=None, finish=None):
    delta = SimpleNamespace(content=content, tool_calls=tools, reasoning_content=None, reasoning=None)
    return SimpleNamespace(choices=[SimpleNamespace(index=0, delta=delta, finish_reason=finish)], model="test/model", usage=None)


@pytest.mark.parametrize("buffered", [True, False])
@pytest.mark.parametrize("claim", ["done", "fixed", "deployed", "sent", "working", "successful"])
@pytest.mark.parametrize("with_tool", [True, False])
def test_provider_stream_claims_are_buffered(buffered, claim, with_tool):
    from run_agent import AIAgent

    tool = SimpleNamespace(index=0, id="call1", function=SimpleNamespace(name="read_file", arguments='{"path":"fixture"}'))
    chunks = ([chunk(tools=[tool])] if with_tool else []) + [chunk(content=claim), chunk(finish="tool_calls" if with_tool else "stop")]
    client = MagicMock()
    client.chat.completions.create.return_value = iter(chunks)
    with patch.object(AIAgent, "_create_request_openai_client", return_value=client), patch.object(AIAgent, "_close_request_openai_client"):
        agent = AIAgent(api_key="test-key", provider="openrouter", base_url="https://openrouter.ai/api/v1", model="test/model", quiet_mode=True, skip_context_files=True, skip_memory=True)
        agent.api_mode = "chat_completions"
        agent._plugin_buffer_output = buffered
        visible = []
        agent.stream_delta_callback = visible.append
        response = agent._interruptible_streaming_api_call({})
        assert response.choices[0].message.content == claim
        assert (claim not in visible) if buffered else (claim in visible)


@pytest.mark.parametrize("claim", ["done", "fixed", "deployed", "sent", "working", "successful"])
def test_codex_and_interim_claims_are_buffered(claim):
    from run_agent import AIAgent

    agent = AIAgent(api_key="test-key", provider="openrouter", base_url="https://openrouter.ai/api/v1", model="test/model", quiet_mode=True, skip_context_files=True, skip_memory=True)
    agent._plugin_buffer_output = True
    visible = []
    agent.stream_delta_callback = visible.append
    agent.interim_assistant_callback = lambda *a, **kw: visible.append(a)
    agent._fire_streamed_codex_commentary(claim)
    agent._emit_interim_assistant_message({"content": claim})
    agent._fire_stream_delta(claim)
    assert not visible
