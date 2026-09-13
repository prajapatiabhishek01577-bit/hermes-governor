from types import SimpleNamespace

from agent.plugin_completion_policy import completion_decision, set_turn_policy


def test_default_is_no_policy():
    agent=SimpleNamespace()
    set_turn_policy(agent, [])
    assert completion_decision(agent, "Hello") is None


def test_any_veto_wins(monkeypatch):
    agent=SimpleNamespace(session_id="test")
    set_turn_policy(agent,[{"completion_policy":{"required":True}}])
    monkeypatch.setattr("hermes_cli.lifecycle.invoke_hook", lambda *a,**kw:[{"verified":True},{"verified":False,"response_text":"Needs evidence"}])
    assert completion_decision(agent,"Done")=={"verified":False,"response_text":"Needs evidence"}


def test_missing_required_gate_blocks(monkeypatch):
    agent=SimpleNamespace(session_id="test")
    set_turn_policy(agent,[{"completion_policy":{"required":True}}])
    monkeypatch.setattr("hermes_cli.lifecycle.invoke_hook",lambda *a,**kw:[])
    assert completion_decision(agent,"Done")["verified"] is False


def test_flags_reset_and_require_literal_booleans():
    agent=SimpleNamespace()
    set_turn_policy(agent,[{"completion_policy":{"required":True,"buffer_output":True}}])
    assert agent._plugin_buffer_output
    set_turn_policy(agent,[{"completion_policy":{"required":"true"}}])
    assert not agent._plugin_buffer_output and not agent._plugin_completion_required
