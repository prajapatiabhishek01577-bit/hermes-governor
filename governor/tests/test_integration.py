import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

SOURCE = Path(__file__).resolve().parents[1]


@pytest.fixture
def native(isolated):
    shutil.copytree(SOURCE, isolated / "plugins" / "hermes-governor",
                    ignore=shutil.ignore_patterns("tests", "__pycache__"))
    (isolated / "config.yaml").write_text(yaml.safe_dump({
        "plugins":{"enabled":["hermes-governor"]},
        "terminal":{"backend":"local"}, "agent":{"max_verify_nudges":3},
        "memory":{"memory_enabled":False,"user_profile_enabled":False},
    }))
    from hermes_cli.plugins import discover_plugins, get_plugin_manager
    discover_plugins(force=True)
    manager=get_plugin_manager()
    assert "hermes-governor" in manager._plugins
    return manager


def test_real_discovery_registers_hooks_and_tool(native):
    from hermes_cli.lifecycle import has_hook
    from tools.registry import registry
    assert has_hook("pre_tool_call") and has_hook("completion_gate")
    assert "governor" in registry.get_all_tool_names()


def schema_subset():
    import model_tools
    from tools.registry import registry
    names={"read_file","write_file","governor","skill_view"}
    return [{"type":"function","function":e.schema} for e in registry.get_all_entries() if e.name in names]


def make_agent(native, max_iterations=12, deferred=False):
    from run_agent import AIAgent
    definitions=schema_subset()
    if deferred:
        from tools.tool_search import assemble_tool_defs, ToolSearchConfig
        definitions = assemble_tool_defs(definitions, context_length=64000, config=ToolSearchConfig.from_raw(True)).tool_defs
    with patch("run_agent.OpenAI"), patch("run_agent.get_tool_definitions",return_value=definitions), patch("run_agent.check_toolset_requirements",return_value={}):
        agent=AIAgent(session_id="safe-governor-scenario",api_key="test-only",base_url="https://example.invalid/v1",
                      provider="openai-compat",model="test/model",max_iterations=max_iterations,quiet_mode=True,
                      skip_context_files=True,skip_memory=True)
    agent._cached_system_prompt="Stable integration-test identity"
    if deferred:
        agent.enabled_toolsets = ["file", "skills", "governor"]
        assert "governor" not in [s["function"]["name"] for s in agent.tools]
    agent._session_db=None
    agent._session_json_enabled=False
    agent.save_trajectories=False
    agent.compression_enabled=False
    agent.skip_background_review=True
    agent._disable_streaming=True
    agent._cleanup_task_resources=lambda *_a,**_kw:None
    agent._save_trajectory=lambda *_a,**_kw:None
    return agent


def response(content=None, tool=None, args=None, number=1):
    calls=[SimpleNamespace(id=f"call-{number}",type="function",function=SimpleNamespace(name=tool,arguments=json.dumps(args or {})))] if tool else None
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content,tool_calls=calls),finish_reason="tool_calls" if tool else "stop")],model="test/model",usage=None)


def run_safe_scenario(native, directory, deferred=False, recovery=False):
    target=directory / "service-state.txt"
    target.write_text("broken\n")
    agent=make_agent(native, deferred=deferred)
    plan={"action":"plan","payload":{"objective":"Restore healthy state in service-state.txt",
        "intent":"Repair the controlled service-state fixture", "constraints":["Only change the temporary fixture"],
        "definition_of_done":[{"id":"healthy","description":"Fresh independent file read contains healthy",
                               "tool":"read_file","args":{"path":str(target)},"field":"content","contains":"healthy"}]}}
    write={"path":str(target),"content":"healthy\n"}
    selection={"action":"select","payload":{"candidates":[{"id":"repair","tool":"write_file","args":write,
               "reason":"Restore the inspected broken fixture","expected_observation":"A fresh read contains healthy"}]}}
    steps=[("governor",plan),("read_file",{"path":str(target)}),("governor",selection),
           ("write_file",write),("read_file",{"path":str(target)}),("governor",{"action":"verify"})]
    if recovery:
        wrong_write={"path":str(target),"content":"still broken\n"}
        wrong_selection={"action":"select","payload":{"candidates":[{"id":"first-approach","tool":"write_file","args":wrong_write,
            "reason":"Controlled first attempt that will not satisfy the objective","expected_observation":"Independent verification decides whether this worked"}]}}
        selection["payload"]["diagnosis"]="The first write succeeded but the independent read still reports broken; replace the incorrect content."
        steps = steps[:2] + [("governor",wrong_selection),("write_file",wrong_write),
            ("read_file",{"path":str(target)}),("governor",{"action":"verify"})] + steps[2:]
    calls=[]
    visible=[]
    agent.stream_delta_callback=visible.append
    agent.interim_assistant_callback=lambda text,**kw:visible.append(text)
    def model(_kwargs):
        index=len(calls)
        calls.append(index)
        if index<len(steps):
            tool,args=steps[index]
            if deferred and tool == "governor":
                tool,args="tool_call",{"name":"governor","arguments":args}
            return response(tool=tool,args=args,number=index)
        agent._fire_stream_delta("Premature done text must remain buffered")
        agent._emit_interim_assistant_message({"role":"assistant","content":"Premature done"})
        return response("Verified healthy: service-state.txt was independently read after the repair.")
    agent._interruptible_api_call=model
    result=agent.run_conversation("Fix service-state.txt so it contains healthy instead of broken.")
    assert target.read_text()=="healthy\n", [m for m in result["messages"] if m.get("role")=="tool"]
    assert result["objective_verified"] is True, result
    assert result["completed"] is True
    assert not any(visible)  # None is Hermes' display-reset marker, not text
    assert agent._cached_system_prompt=="Stable integration-test identity"
    records=list((Path(__import__('os').environ['HERMES_HOME']) / "plugin-data" / "hermes-governor").glob("*.json"))
    trace=json.loads(records[-1].read_text())
    assert trace["state"]=="VERIFIED_COMPLETE"
    assert trace["verification_result"]["healthy"]["passed"] is True
    assert any(e["source"]=="write_file" for e in trace["evidence"])
    if recovery:
        checks=[e for e in trace["trace"] if e["event"]=="governor_verify"]
        assert [e["state"] for e in checks]==["RECOVERING","VERIFIED_COMPLETE"]
        assert trace["recovery_attempts"]==1
    return result,trace


def test_full_agent_loop_repairs_real_file_and_verifies(native,tmp_path):
    run_safe_scenario(native,tmp_path)


def test_deferred_governor_full_agent_loop(native, tmp_path):
    run_safe_scenario(native, tmp_path, deferred=True)


def test_real_successful_write_with_false_state_recovers_and_verifies(native, tmp_path):
    run_safe_scenario(native, tmp_path, recovery=True)


def test_normal_diagnostic_reports_native_hooks_and_full_soul(native, isolated, capsys):
    from hermes_cli.lifecycle import invoke_hook
    (isolated / "SOUL.md").write_text("Objective ownership constitution.", encoding="utf-8")
    callback = next(cb for cb in native.iter_hook_callbacks("completion_gate") if "hermes_governor" in cb.__module__)
    callback.__self__.cli(SimpleNamespace(diagnose=True))
    report=json.loads(capsys.readouterr().out)
    assert report["soul_loaded_in_full"] and all(report["hooks"].values())
    assert report["tool_registered"] and report["settings"]["enabled"]


def test_false_done_recovery_is_bounded_and_not_delivered(native):
    agent=make_agent(native,6)
    attempts=[]
    agent._interruptible_api_call=lambda _kw:(attempts.append(1) or response("Deployment succeeded. Done!"))
    result=agent.run_conversation("Deploy the website and verify production")
    assert result["objective_verified"] is False
    assert result["completed"] is False
    assert result["final_response"].startswith("BLOCKED")
    assert "Deployment succeeded" not in result["final_response"]
    assert 1<len(attempts)<=5  # non-coding stop retries through the real loop
    assert result["messages"][-1]["content"].startswith("BLOCKED")


def test_real_approval_denial_prevents_executor(native,monkeypatch,tmp_path):
    from hermes_cli.lifecycle import invoke_hook
    from model_tools import handle_function_call
    from tools.registry import registry
    target=tmp_path / "important.txt"
    target.write_text("keep")
    definitions=schema_subset()+[{"function":{"name":"terminal","description":"shell"}}]
    invoke_hook("pre_llm_call",session_id="s",task_id="t",turn_id="1",user_message="Fix important.txt",available_tools=definitions)
    def execute(name,args):
        return json.loads(handle_function_call(name,args,task_id="t",session_id="s"))
    criterion={"id":"exists","description":"File preserved","tool":"read_file","args":{"path":str(target)},"field":"content","contains":"keep"}
    execute("governor",{"action":"plan","payload":{"definition_of_done":[criterion]}})
    execute("read_file",{"path":str(target)})
    args={"command":"Remove-Item important.txt"}
    execute("governor",{"action":"select","payload":{"candidates":[{"id":"delete","tool":"terminal","args":args,"reason":"Risk fixture","expected_observation":"Deletion requires approval"}]}})
    monkeypatch.setattr("tools.approval.request_tool_approval",lambda *a,**k:{"approved":False,"message":"Denied by test owner"})
    result=execute("terminal",args)
    assert result.get("error")
    assert target.read_text()=="keep"


def test_missing_required_gate_fails_closed():
    from agent.plugin_completion_policy import completion_decision,set_turn_policy
    agent=SimpleNamespace(session_id="missing",platform="cli")
    set_turn_policy(agent,[{"completion_policy":{"required":True}}])
    assert completion_decision(agent,"Done")["verified"] is False


def test_buffer_policy_resets_between_turns(native):
    from agent.plugin_completion_policy import set_turn_policy
    from run_agent import AIAgent
    agent=object.__new__(AIAgent)
    set_turn_policy(agent,[{"completion_policy":{"required":True,"buffer_output":True,"verify_on_stop":True}}])
    assert agent._plugin_buffer_output
    set_turn_policy(agent,[])
    assert not agent._plugin_buffer_output and not agent._plugin_completion_required
