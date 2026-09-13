import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("governor_test_module", SOURCE / "governor.py")
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def schemas(*names):
    return [{"function":{"name":n, "description":n.replace("_", " ")}} for n in names]


@pytest.fixture
def engine():
    return mod.Governor(SimpleNamespace(get_config=lambda k,d:d))


def start(g, objective="Fix target.txt", extra=()):
    return g.start(session_id="s", task_id="t", turn_id="turn", user_message=objective,
                   available_tools=schemas("read_file", "write_file", "terminal", "skill_view", "governor", *extra))


def call(g, action, **payload):
    return json.loads(g.tool({"action":action,"payload":payload}))


def plan(g, path="target.txt", expected="healthy"):
    return call(g, "plan", objective="Restore healthy state in " + path,
                definition_of_done=[dict(id="healthy",description="Target reports healthy state",tool="read_file",
                                         args={"path":path},field="content",contains=expected)])


def observe(g, tool="read_file", args=None, content="broken", success=True):
    g.after(tool, args or {"path":"target.txt"}, json.dumps({"content":content,"success":success}),
            session_id="s",tool_call_id="call",status="ok" if success else "error")


def select(g, tool="write_file", args=None, **kw):
    return call(g, "select", candidates=[dict(id="repair",tool=tool,args=args or {"path":"target.txt","content":"healthy"},
                                             reason="Restore intended healthy state",expected_observation="Healthy content appears")], **kw)


def test_a_installed_skill_discovered_and_enforced(engine, isolated):
    folder=isolated / "skills" / "target-repair"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text('---\nname: target-repair\ndescription: Fix target files and restore healthy content.\n---\nInspect, repair and independently read the target.\n')
    start(engine)
    assert any(c["name"]=="target-repair" for c in engine.get("s").available_capabilities)
    plan(engine); observe(engine); select(engine)
    assert "skills" in engine.before("write_file", {"path":"target.txt","content":"healthy"}, session_id="s")["message"]
    observe(engine,"skill_view",{"name":"target-repair"},"Instructions")
    assert engine.before("write_file", {"path":"target.txt","content":"healthy"}, session_id="s") is None


def test_b_inspection_bound_to_target(engine):
    start(engine); plan(engine); select(engine)
    observe(engine,args={"path":"unrelated.txt"})
    assert "required current state" in engine.before("write_file", {"path":"target.txt","content":"healthy"}, session_id="s")["message"]


def test_c_diagnostics_rank_above_blind_mutation():
    actions=[dict(id="edit",tool="write_file",args={}),dict(id="logs",tool="read_file",args={}),
             dict(id="delete",tool="terminal",args={"command":"rm -rf cache"})]
    assert mod.rank(actions, uncertain=True)[0]["id"]=="logs"


def test_d_execution_success_is_not_verification(engine):
    start(engine); plan(engine); observe(engine)
    observe(engine,"write_file",{"path":"target.txt"},"healthy")
    assert call(engine,"verify")["state"]=="RECOVERING"
    assert not engine.finish(session_id="s")["verified"]


def test_e_recovery_requires_diagnosis_and_penalizes_failed_action(engine):
    start(engine); plan(engine); observe(engine)
    observe(engine,"write_file",{"path":"target.txt"},success=False)
    assert engine.get("s").state=="RECOVERING"
    assert "error" in select(engine)
    result=select(engine, diagnosis="Failure indicates wrong content was used; correct it.")
    assert result["state"]=="READY"
    actions=[dict(id="repeat",tool="write_file",args={"path":"target.txt"}),dict(id="inspect",tool="read_file",args={})]
    assert mod.rank(actions,False,engine.get("s").failure_history)[0]["id"]=="inspect"


def test_f_false_deployment_done_blocked(engine):
    start(engine,"Deploy the website")
    verdict=engine.finish(session_id="s",response_text="Successfully deployed. Done!")
    assert verdict["verified"] is False
    assert "Successfully deployed" not in verdict["response_text"]


def test_g_external_install_needs_review(engine):
    start(engine); plan(engine); observe(engine)
    command={"command":"pip install example-package"}
    select(engine,"terminal",command)
    assert "Untrusted external code" in engine.before("terminal",command,session_id="s")["message"]
    assert "error" in call(engine,"trust_review",source="Popular repository")


def test_h_existing_scheduler_capability_considered(engine):
    start(engine,"Schedule a cron monitor",extra=("cron_list","cron_create"))
    assert any(c["name"].startswith("cron_") for c in engine.get("s").available_capabilities)


def test_i_destructive_execution_escalates(engine):
    start(engine); plan(engine); observe(engine)
    args={"command":"Remove-Item important.txt"}
    select(engine,"terminal",args)
    gate=engine.before("terminal",args,session_id="s")
    assert gate["action"]=="approve" and "DESTRUCTIVE" in gate["message"]


def test_j_trivial_chat_does_not_discover_or_persist(engine,isolated):
    assert start(engine,"Hello!")==None
    assert engine.get("s").category=="CHAT"
    assert not (isolated / "plugin-data").exists()


def test_chat_cannot_execute_tools_without_activation(engine):
    start(engine,"Hello!")
    assert engine.before("write_file",{},session_id="s")["action"]=="block"


def test_evidence_cannot_be_submitted_by_model(engine):
    start(engine); plan(engine)
    result=call(engine,"verify", evidence=[{"passed":True}],success=True)
    assert result["state"] != "VERIFIED_COMPLETE"


def test_latest_failure_invalidates_prior_pass(engine):
    start(engine); plan(engine); observe(engine,content="healthy")
    observe(engine,content="broken")
    assert call(engine,"verify")["state"]=="RECOVERING"


def test_mutation_invalidates_stale_evidence(engine):
    start(engine); plan(engine); observe(engine,content="healthy")
    assert call(engine,"verify")["state"]=="VERIFIED_COMPLETE"
    observe(engine,"write_file",{"path":"target.txt"},"broken")
    assert not engine.verified(engine.get("s"))


def test_pass_requires_separate_matching_observation(engine):
    start(engine); plan(engine); observe(engine)
    observe(engine,"write_file",{"path":"target.txt"},"healthy")
    observe(engine,content="healthy")
    assert call(engine,"verify")["state"]=="VERIFIED_COMPLETE"
    assert engine.finish(session_id="s")["verified"]


def test_verification_plan_cannot_be_weakened_after_execution(engine):
    start(engine); plan(engine); observe(engine,"write_file",{"path":"target.txt"})
    assert "error" in plan(engine,expected="broken")


def test_status_only_predicate_rejected(engine):
    start(engine)
    result=call(engine,"plan",definition_of_done=[dict(id="x",description="HTTP endpoint",tool="read_file",args={"path":"x"},field="success",equals=True)])
    assert "error" in result


def test_recovery_is_bounded(engine):
    start(engine)
    for _ in range(8):
        engine.before_finish(session_id="s")
    assert engine.get("s").state=="BLOCKED"
    assert engine.before_finish(session_id="s") is None


def test_profile_isolation(engine,monkeypatch,tmp_path):
    start(engine)
    monkeypatch.setenv("HERMES_HOME",str(tmp_path / "other"))
    assert engine.get("s") is None
    assert "error" in call(engine,"verify")


def test_decision_records_have_no_raw_outputs(engine,isolated):
    start(engine); plan(engine); observe(engine,content="private arbitrary raw tool body")
    record=json.loads(next((isolated / "plugin-data" / "hermes-governor").glob("*.json")).read_text())
    assert record["evidence"][0]["reference"]["tool_call_id"]=="call"
    assert "private arbitrary raw tool body" not in json.dumps(record)


def test_tool_arguments_must_match_selection(engine):
    start(engine); plan(engine); observe(engine); select(engine)
    assert engine.before("write_file",{"path":"other.txt","content":"healthy"},session_id="s")["action"]=="block"


def test_general_explanation_is_chat(engine):
    assert start(engine,"Explain photosynthesis.") is None


def test_file_contents_question_requires_observation(engine):
    assert start(engine,"What does target.txt contain?")["completion_policy"]["required"]


def test_plan_generates_inspection_candidates(engine):
    start(engine)
    result=plan(engine)
    assert result["candidates"][0]["tool"]=="read_file"
    assert result["candidates"][0]["args"]=={"path":"target.txt"}


def test_failed_read_invalidates_passing_observation(engine):
    start(engine); plan(engine); observe(engine,content="healthy")
    observe(engine,success=False)
    assert not engine.verified(engine.get("s"))


def test_unrelated_evidence_cannot_approve_external_code(engine):
    start(engine); plan(engine); observe(engine)
    select(engine,"terminal",{"command":"pip install example-package"})
    result=call(engine,"trust_review",source="https://github.com/example/package",
                permissions="local installation",dependencies="inspected dependency list",compatibility="Python compatible",
                license="Apache license",maintenance="recently maintained",sandbox="isolated temporary workspace",
                test_plan="test installed module",evidence_ids=["e1"])
    assert "error" in result


def test_shell_program_is_not_classified_read_only():
    assert mod.risk("terminal",{"command":"python -c 'import os; os.remove(\"data\")'"})!="SAFE"


def test_verification_toggle_is_honored(isolated):
    engine=mod.Governor(SimpleNamespace(get_config=lambda k,d:False if k=="require_verification" else d))
    assert not start(engine)["completion_policy"]["required"]
    assert engine.before_finish(session_id="s") is None


def test_local_unknown_does_not_force_external_research(engine):
    start(engine, extra=("web_search",)); plan(engine); observe(engine); select(engine)
    engine.get("s").unknowns=["Does the local file have the requested final newline?"]
    assert engine.before("write_file", {"path":"target.txt","content":"healthy"}, session_id="s") is None
    engine.get("s").unknowns=["Which library API version is compatible?"]
    assert "External technical unknowns" in engine.before("write_file", {"path":"target.txt","content":"healthy"}, session_id="s")["message"]


def test_diagnostic_read_does_not_erase_recovery_diagnosis_requirement(engine):
    start(engine); plan(engine); observe(engine, success=False)
    observe(engine, content="broken")
    assert engine.get("s").state == "RECOVERING"
    assert "error" in select(engine)


def test_impossible_numbered_file_predicate_rejected_before_execution(engine):
    start(engine)
    criterion=dict(id="exact",description="Exact file contents",tool="read_file",args={"path":"target.txt"},field="content",equals="1|healthy\n")
    assert "numbered display text" in call(engine,"plan",definition_of_done=[criterion])["error"]
    assert not engine.get("s").definition_of_done
    criterion["equals"]="1|healthy\n2|"
    assert call(engine,"plan",definition_of_done=[criterion])["state"]=="PLANNING"
