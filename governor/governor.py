"""A bounded per-turn policy engine over Hermes' existing execution system.

The model proposes objective-specific predicates and actions. Python owns
ranking, prerequisites, observed evidence, freshness, and completion status.
No model-reported success flag can satisfy a verification predicate.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from threading import RLock
import json
import re

from hermes_constants import get_hermes_home
from agent.redact import redact_sensitive_text
from tools.threat_patterns import scan_for_threats
from plugins.plugin_storage import plugin_data_dir
from utils import atomic_json_write

DEFAULTS = dict(enabled=True, require_verification=True, capability_discovery=True,
                research_when_uncertain=True, github_discovery=True,
                external_code_trust_gate=True, proactive_oversight=True,
                max_recovery_attempts=3, max_evidence=100)
READ_TOOLS = {"read_file", "search_files", "web_search", "web_extract", "skills_list",
              "skill_view", "browser_snapshot", "browser_get_content", "browser_screenshot",
              "browser_navigate", "session_search", "kanban_show", "kanban_list", "cron_list",
              "tool_search", "tool_describe"}
CONTROL_TOOLS = {"clarify", "ask_user"}
WRITE_TOOLS = {"write_file", "patch", "skill_manage"}
RESEARCH = {"web_search", "web_extract"}
SENSITIVE = re.compile(r"delete|remove|reset|drop|destroy|publish|deploy|send|purchase|payment|billing|secret|credential", re.I)
EXTERNAL = re.compile(r"\b(pip\s+install|uv\s+(?:pip\s+)?(?:install|add)|npm\s+(?:i|install|exec)|npx|yarn\s+add|git\s+clone|curl|wget|Invoke-WebRequest|Invoke-RestMethod)\b", re.I)
ACTION = re.compile(r"\b(fix|build|create|edit|update|upgrade|install|deploy|send|delete|remove|change|write|implement|run|test|check|inspect|verify|research|find|monitor|schedule|automate|restore|debug|save|configure|make|set|open|analy[sz]e)\b", re.I)
CHAT = re.compile(r"^(hi|hello|hey|thanks|thank you|good morning|what is \d.*|what(?:'s| is) (?:the capital|your name).*)[.!?\s]*$", re.I)


def now():
    return datetime.now(timezone.utc).isoformat()


def clean(value, limit=1200):
    return redact_sensitive_text(str(value))[:limit]


def fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def tokens(text):
    ignored = {"the", "and", "for", "with", "this", "that", "from", "please", "using", "into", "have"}
    return {t.rstrip("s") for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2 and t not in ignored}


def relevance(objective, name, description):
    query = tokens(objective)
    terms = tokens(name + " " + description)
    shared = query & terms
    if not shared:
        return 0.0
    return round(min(1.0, len(shared) / max(1, min(len(query), 6)) +
                     (0.25 if tokens(name) & query else 0)), 3)


def risk(tool, args):
    if tool in READ_TOOLS:
        return "SAFE"
    if tool == "terminal":
        command = str(args.get("command", ""))
        if re.search(r"\b(rm|del|rmdir|Remove-Item|drop\s+table|git\s+reset)\b", command, re.I):
            return "DESTRUCTIVE"
        # A shell is a programming language. Unknown commands require approval;
        # never mistake 'python -c ...' or a pipe for a read-only diagnostic.
        if re.fullmatch(r"\s*(?:pwd|whoami|git\s+status(?:\s+--short)?|git\s+diff(?:\s+--stat)?)\s*", command):
            return "SAFE"
        return "SENSITIVE"
    if SENSITIVE.search(tool + " " + str(args.get("action", ""))):
        return "SENSITIVE"
    if tool in WRITE_TOOLS:
        return "REVERSIBLE"
    return "SENSITIVE"  # unknown MCP/plugin effects must not be guessed safe


def rank(actions, uncertain=True, failures=()):
    ranked = []
    for original in actions:
        action = dict(original)
        dims = action.get("scores") or {}
        def number(key, default):
            value = dims.get(key, default)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
                raise ValueError(f"scores.{key} must be a number from 0 to 1")
            return value
        tool, args = action["tool"], action.get("args", {})
        level = risk(tool, args)
        score = (2 * number("expected_success", .5) + number("evidence_strength", .3) +
                 number("reversibility", .5) + number("confidence", .5) +
                 2 * number("objective_alignment", .7) +
                 (2 if uncertain else .5) * number("information_gain", .3) -
                 number("cost", .2) - number("time", .2) - number("dependency_count", .2))
        score -= {"SAFE": 0, "REVERSIBLE": .7, "SENSITIVE": 2, "DESTRUCTIVE": 4}[level]
        if uncertain and level != "SAFE":
            score -= 3
        if tool == "skill_view":
            score += 1
        repeated = sum(f.get("fingerprint") == fingerprint([tool, args]) for f in failures)
        score -= 5 * repeated
        action.update(risk=level, score=round(score, 3), repeated_failures=repeated,
                      rank_reason="Evidence, alignment, information gain, reversibility and cost; uncertainty/risk/retry penalties applied.")
        ranked.append(action)
    return sorted(ranked, key=lambda a: (-a["score"], a["id"]))


def value_at(payload, path):
    value = payload
    for key in path.split("."):
        if isinstance(value, dict):
            value = value[key]
        elif isinstance(value, list) and key.isdigit():
            value = value[int(key)]
        else:
            raise KeyError(path)
    return value


@dataclass
class Goal:
    session_id: str
    task_id: str
    turn_id: str
    objective: str
    intent: str = ""
    category: str = "SUBSTANTIAL_ACTION"
    state: str = "UNDERSTANDING"
    constraints: list = field(default_factory=list)
    definition_of_done: list = field(default_factory=list)
    current_state_summary: str = "Unknown until observed."
    known_facts: list = field(default_factory=list)
    unknowns: list = field(default_factory=lambda: ["Current state and objective-specific success conditions"])
    available_capabilities: list = field(default_factory=list)
    candidate_actions: list = field(default_factory=list)
    selected_action: dict | None = None
    selected_tool_or_skill: str = ""
    risk_level: str = "SAFE"
    required_permission: str = ""
    execution_result: str = ""
    evidence: list = field(default_factory=list)
    verification_result: dict = field(default_factory=dict)
    confidence: str = "unknown"
    failure_history: list = field(default_factory=list)
    next_action: str = "Inspect relevant state and define observable success."
    oversight_needed: str = "undecided"
    loaded_skills: list = field(default_factory=list)
    skipped_skills: dict = field(default_factory=dict)
    inspections: list = field(default_factory=list)
    inspection_requirements: list = field(default_factory=list)
    tools: list = field(default_factory=list)
    revision: int = 0
    recovery_attempts: int = 0
    sequence: int = 0
    last_mutation: int = 0
    executed: bool = False
    research_done: bool = False
    trust_reviews: dict = field(default_factory=dict)
    trace: list = field(default_factory=list)
    errors: list = field(default_factory=list)


class Governor:
    def __init__(self, ctx):
        self.ctx = ctx
        self.goals = {}
        self.lock = RLock()
        self.active = ContextVar("governor_active", default=None)

    def config(self):
        config = {k: self.ctx.get_config(k, v) for k, v in DEFAULTS.items()}
        for name in ("max_recovery_attempts", "max_evidence"):
            value = config[name]
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"Governor {name} must be an integer")
        config["max_recovery_attempts"] = max(0, min(5, config["max_recovery_attempts"]))
        config["max_evidence"] = max(10, min(200, config["max_evidence"]))
        return config

    def key(self, session_id):
        if not session_id:
            raise ValueError("Governor requires the host session ID")
        return (str(get_hermes_home().resolve()), session_id)

    def get(self, session_id):
        return self.goals.get(self.key(session_id))

    def record(self, goal, event, **fields):
        goal.trace.append(dict(timestamp=now(), event=event, state=goal.state, **fields))
        goal.trace = goal.trace[-100:]
        snapshot = json.loads(redact_sensitive_text(json.dumps(asdict(goal), ensure_ascii=False)))
        path = plugin_data_dir("hermes-governor") / (fingerprint([goal.session_id, goal.turn_id])[:24] + ".json")
        atomic_json_write(path, snapshot)
        # Bounded per-plugin summaries; raw tool data remains in Hermes transcripts.
        records = sorted(path.parent.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in records[200:]:
            old.unlink(missing_ok=True)

    def discover(self, text, available_tools):
        from tools.skills_tool import _find_all_skills
        items = []
        for schema in available_tools:
            function = schema.get("function", schema)
            name = function.get("name", "")
            if name == "governor":
                continue
            items.append(dict(name=name, source="session_tool", relevance=relevance(text, name, function.get("description", ""))))
        for skill in _find_all_skills():
            items.append(dict(name=skill["name"], source="installed_skill",
                              relevance=relevance(text, skill["name"], skill.get("description", ""))))
        return sorted((i for i in items if i["relevance"] > 0), key=lambda i: -i["relevance"])[:15]

    def start(self, session_id="", task_id="", turn_id="", user_message="", available_tools=(), **kwargs):
        with self.lock:
            if not self.config()["enabled"]:
                self.goals.pop(self.key(session_id), None)
                return None
            text = user_message if isinstance(user_message, str) else json.dumps(user_message)
            # State-dependent and action requests enter the pipeline. Chat remains
            # lightweight; unexpected tools on a chat turn are blocked below.
            state_dependent = bool(re.search(r"\b(file|repository|installed|deployment|website|logs?|configuration|processes)\b|[\w-]+\.(?:txt|py|js|json|yaml|md)\b", text, re.I))
            conversational = bool(CHAT.fullmatch(text.strip())) or (not ACTION.search(text) and not state_dependent)
            goal = Goal(session_id, task_id, turn_id, clean(text, 2500), category="CHAT" if conversational else "SUBSTANTIAL_ACTION")
            goal.tools = [s.get("function", s).get("name", "") for s in available_tools]
            self.goals[self.key(session_id)] = goal
            self.active.set(self.key(session_id))
            if conversational:
                return None
            return self.activate(goal, available_tools)

    def activate(self, goal, available_tools):
        goal.state = "DISCOVERING"
        try:
            goal.available_capabilities = self.discover(goal.objective, available_tools) if self.config()["capability_discovery"] else []
        except Exception:
            goal.errors.append("Capability inventory failed; retry discovery before execution.")
        goal.state = "INSPECTING"
        if "governor" not in goal.tools:
            goal.errors.append("Governor toolset is not enabled for this surface; enable it and start a new session.")
        self.record(goal, "objective_received", capabilities=goal.available_capabilities)
        required = self.config()["require_verification"]
        return {"completion_policy": {"required": required, "buffer_output": required, "verify_on_stop": required},
                "context": "[Governor v1] " + json.dumps({
                    "objective": goal.objective, "capabilities": goal.available_capabilities,
                    "instructions": "If governor is deferred, use tool_describe(name=governor), then tool_call(name=governor,arguments={action,payload}); Hermes applies the same hooks to the underlying call. Inspect state with existing read tools. Use governor(action=plan,payload=...) to specify intent, constraints, inspection_requirements, unknowns, definition_of_done. Derive criteria from the actual read-tool result shape before mutation. Preserve formatting including line numbers and trailing empty lines in exact comparisons; use file_size, total_lines and truncated fields when exact file shape matters. Criteria bind read-only tool, exact target args, field and value; exit code alone is insufficient. Load matching skills or record skip reasons. Submit candidates with governor(action=select); execute ONLY the selected tool/args using Hermes tools. After mutations obtain fresh separate read/test evidence, then governor(action=verify). Failures require diagnose/replan. governor(action=status) shows state. External installs require source review and existing human approval. No evidence, no DONE. For example, read_file displays the file text a\\nb\\n as 1|a\\n2|b\\n3| (a numbered empty final line). Compare the actual display, not unnumbered bytes. See governor schema for payload fields."
                }, ensure_ascii=False)}

    def block(self, goal, reason):
        goal.next_action = reason
        self.record(goal, "execution_blocked", reason=clean(reason))
        return {"action": "block", "message": "Governor: " + reason}

    def suggest(self, goal):
        """Generate useful metadata-based candidates; no auxiliary LLM call."""
        candidates = []
        for index, req in enumerate(goal.inspection_requirements[:3]):
            candidates.append(dict(id=f"inspect-{index}", tool=req["tool"], args=req["args"],
                                   reason="Inspect the state bound to a success criterion",
                                   expected_observation="Current target state or a concrete error",
                                   scores={"information_gain":.9,"evidence_strength":.8}))
        if "skill_view" in goal.tools:
            for skill in (c for c in goal.available_capabilities if c["source"]=="installed_skill" and c["relevance"] >= .5):
                candidates.append(dict(id="load-skill",tool="skill_view",args={"name":skill["name"]},
                                       reason="Use the installed relevant workflow before improvisation",
                                       expected_observation="Full applicable skill instructions"))
                break
        if goal.unknowns and self.config()["research_when_uncertain"] and "web_search" in goal.tools:
            suffix = " official documentation GitHub issues" if self.config()["github_discovery"] else " official documentation"
            candidates.append(dict(id="research",tool="web_search",args={"query":goal.objective + suffix},
                                   reason="Resolve material uncertainty with current sources",
                                   expected_observation="Relevant current documentation/source evidence"))
        return rank(candidates, bool(goal.unknowns), goal.failure_history)

    def before(self, tool_name, args, session_id="", task_id="", **kwargs):
        with self.lock:
            if not self.config()["enabled"]:
                return None
            goal = self.get(session_id)
            self.active.set(self.key(session_id))
            if goal is None:
                return {"action": "block", "message": "Governor context missing; start a fresh session."}
            if tool_name == "governor":
                return None
            if tool_name in CONTROL_TOOLS:
                return None
            if goal.category == "CHAT":
                # No tool execution may exploit a conversational routing decision.
                return self.block(goal, "This was routed as chat. Start an actionable request to use tools.")
            if goal.state == "BLOCKED":
                return self.block(goal, "Recovery limit reached. Owner input is required before more execution.")
            if tool_name in READ_TOOLS:
                return None
            if not goal.definition_of_done:
                return self.block(goal, "Define observable success with governor plan before execution.")
            if goal.errors:
                return self.block(goal, goal.errors[0])
            missing = [req for req in goal.inspection_requirements if not any(
                item["tool"] == req["tool"] and self.matches(req["args"], item["args"]) for item in goal.inspections)]
            if not goal.inspections or missing:
                return self.block(goal, "Inspect required current state before modifying it: " + clean(missing))
            high = [c["name"] for c in goal.available_capabilities if c["source"] == "installed_skill" and c["relevance"] >= .5]
            skipped = [s for s in high if s not in goal.loaded_skills and s not in goal.skipped_skills]
            if skipped:
                return self.block(goal, "Load relevant skills or record concrete skip reasons: " + ", ".join(skipped))
            selected = goal.selected_action
            if not selected or selected["tool"] != tool_name or fingerprint(selected.get("args", {})) != fingerprint(args):
                return self.block(goal, "Select and rank this exact action/arguments through governor select first.")
            if selected.get("repeated_failures", 0) >= 2:
                return self.block(goal, "This exact approach already failed repeatedly. Select a different approach.")
            external_unknowns = re.search(r"\b(api|library|package|dependency|dependencies|version|documentation|compatibility|external|online|github)\b", str(goal.unknowns), re.I)
            if external_unknowns and self.config()["research_when_uncertain"] and not goal.research_done:
                if any(t in goal.tools for t in RESEARCH):
                    return self.block(goal, "External technical unknowns remain. Research current documentation or source before executing.")
            encoded = json.dumps(args, sort_keys=True).lower()
            if any(p in encoded for p in ("hermes-governor", "plugin_completion_policy", "hermes\\\\config.yaml", "hermes/config.yaml")):
                return self.block(goal, "Runtime policy cannot modify its own implementation or authorization settings.")
            if self.config()["external_code_trust_gate"] and (EXTERNAL.search(encoded) or selected.get("external_code")):
                key = fingerprint([tool_name, args])
                if key not in goal.trust_reviews:
                    return self.block(goal, "Untrusted external code: inspect source, permissions, dependencies, license, maintenance and compatibility; record a trust review with scan evidence before install/run.")
            level = risk(tool_name, args)
            goal.risk_level = level
            goal.state = "READY"
            if level in {"SENSITIVE", "DESTRUCTIVE"}:
                goal.required_permission = "Existing Hermes approval gate"
                self.record(goal, "approval_required", tool=tool_name, risk=level)
                return {"action": "approve", "message": f"Governor: {level} action requires owner authorization.",
                        "rule_key": "governor:" + fingerprint([tool_name, args])}
            self.record(goal, "execution_handoff", tool=tool_name, risk=level)
            return None

    @staticmethod
    def matches(expected, actual):
        return all(key in actual and actual[key] == value for key, value in expected.items())

    def after(self, tool_name, args, result, session_id="", tool_call_id="", status="", **kwargs):
        with self.lock:
            if not self.config()["enabled"]:
                return
            goal = self.get(session_id)
            if goal is None or goal.category == "CHAT" or tool_name == "governor" or tool_name in CONTROL_TOOLS:
                return
            was_recovering = goal.state == "RECOVERING"
            try:
                data = json.loads(result) if isinstance(result, str) else result
            except (TypeError, ValueError):
                data = {"output": str(result)}
            if not isinstance(data, dict):
                data = {"output": data}
            ok = status not in {"blocked", "error", "failed"} and not data.get("error") and data.get("success") is not False
            if "exit_code" in data and data["exit_code"] != 0:
                ok = False
            goal.sequence += 1
            item = dict(id=f"e{goal.sequence}", type="tool_response", source=tool_name, timestamp=now(),
                        success=ok, reference={"session_id":session_id, "tool_call_id":tool_call_id},
                        digest=fingerprint(result), sequence=goal.sequence,
                        summary=f"{tool_name}: {'observed result' if ok else 'failed or blocked'}",
                        scan_findings=scan_for_threats(str(result)[:200000], scope="context") if tool_name in READ_TOOLS else [])
            goal.evidence.append(item)
            goal.evidence = goal.evidence[-self.config()["max_evidence"]:]
            readonly = tool_name in READ_TOOLS
            if ok and readonly:
                goal.inspections.append({"tool":tool_name, "args":args, "evidence":item["id"]})
                goal.inspections = goal.inspections[-100:]
                goal.known_facts.append(item["id"] + ": " + item["summary"])
                goal.known_facts = goal.known_facts[-30:]
                goal.current_state_summary = "Observed state references: " + ", ".join(goal.known_facts[-6:])
                if tool_name == "skill_view" and not args.get("file_path"):
                    goal.loaded_skills.append(args.get("name", ""))
                if tool_name in RESEARCH:
                    goal.research_done = True
            if not readonly and status != "blocked":
                goal.state = "EXECUTING"
                goal.revision += 1
                goal.last_mutation = goal.sequence
                goal.verification_result.clear()
                goal.executed = True
            goal.execution_result = item["summary"]
            goal.state = "RECOVERING" if was_recovering else "OBSERVING"
            if not ok:
                self.recover(goal, "Tool failed or was blocked; inspect evidence " + item["id"], tool_name, args)
            else:
                # Predicates are evaluated against REAL host results, never model
                # submissions. Executing a mutation can never satisfy a criterion.
                for criterion in goal.definition_of_done:
                    if (readonly and criterion["tool"] == tool_name and
                            self.matches(criterion["args"], args) and goal.sequence > goal.last_mutation):
                        try:
                            observed = value_at(data, criterion["field"])
                            passed = (observed == criterion["equals"]) if "equals" in criterion else (
                                isinstance(observed, str) and criterion["contains"] in observed)
                        except (KeyError, IndexError, TypeError):
                            passed = False
                        goal.verification_result[criterion["id"]] = dict(passed=passed, evidence=item["id"], revision=goal.revision)
            # A failed re-check invalidates an earlier passing observation too.
            if not ok and readonly:
                for criterion in goal.definition_of_done:
                    if criterion["tool"] == tool_name and self.matches(criterion["args"], args):
                        goal.verification_result.pop(criterion["id"], None)
            self.record(goal, "observation", evidence=item)

    def recover(self, goal, reason, tool="", args=None):
        goal.recovery_attempts += 1
        goal.failure_history.append(dict(reason=clean(reason), fingerprint=fingerprint([tool, args or {}])))
        goal.failure_history = goal.failure_history[-30:]
        goal.selected_action = None
        goal.state = "RECOVERING" if goal.recovery_attempts <= self.config()["max_recovery_attempts"] else "BLOCKED"
        goal.next_action = "Diagnose the failure, inspect evidence, update assumptions, and rank an alternative." if goal.state == "RECOVERING" else "Owner input required: recovery budget exhausted."

    def validated_criteria(self, goal, criteria):
        if not isinstance(criteria, list) or not 1 <= len(criteria) <= 12:
            raise ValueError("Provide 1-12 observable Definition of Done criteria")
        seen = set()
        for c in criteria:
            if not isinstance(c, dict) or not all(c.get(k) for k in ("id", "description", "tool", "args", "field")):
                raise ValueError("Each criterion needs id, description, read-only tool, target args, field, and equals/contains")
            if c["id"] in seen:
                raise ValueError("Criterion IDs must be unique")
            seen.add(c["id"])
            if c["tool"] not in READ_TOOLS or c["tool"] not in goal.tools:
                raise ValueError("Verification requires an available independent read-only tool")
            if c["tool"] in {"skills_list", "skill_view", "web_search", "session_search", "tool_search", "tool_describe"}:
                raise ValueError("Discovery/search alone cannot verify an external objective")
            if c["field"].lower() in {"exit_code", "success", "status", "ok"}:
                raise ValueError("Status/exit code alone does not prove the objective")
            if ("equals" in c) == ("contains" in c):
                raise ValueError("Use exactly one predicate: equals or contains")
            if (c["tool"] == "read_file" and c["field"] == "content" and
                    isinstance(c.get("equals"), str) and c["equals"].endswith("\n")):
                raise ValueError("read_file.content is numbered display text, not raw file bytes. Its final line never ends in a bare newline: a file containing a\\nb\\n is displayed as 1|a\\n2|b\\n3|. Derive the expected display from an actual read before execution, retaining the final empty-line marker.")
            if "contains" in c and (not isinstance(c["contains"], str) or not c["contains"].strip()):
                raise ValueError("contains must be nonempty")
        return criteria

    def verified(self, goal):
        if not goal.definition_of_done or goal.state == "BLOCKED":
            return False
        return all(goal.verification_result.get(c["id"], {}).get("passed") is True and
                   goal.verification_result[c["id"]].get("revision") == goal.revision
                   for c in goal.definition_of_done)

    def tool(self, args, **kwargs):
        with self.lock:
            try:
                key = self.active.get()
                if key is None or key[0] != str(get_hermes_home().resolve()):
                    raise ValueError("Missing scoped Governor session")
                goal = self.goals[key]
                action, payload = args.get("action"), args.get("payload") or {}
                if action == "status":
                    return json.dumps(asdict(goal), ensure_ascii=False)
                if action == "escalate":
                    required = ("discovered", "attempted", "options", "recommendation", "decision")
                    if not all(payload.get(k) for k in required):
                        raise ValueError("Escalation needs discovered, attempted, options, recommendation and exact decision")
                    goal.state = "BLOCKED"
                    goal.next_action = clean(json.dumps({k:payload[k] for k in required}), 2000)
                    self.record(goal,"owner_escalation",summary=goal.next_action)
                    return json.dumps({"state":goal.state,"next_action":goal.next_action})
                if goal.state == "BLOCKED":
                    raise ValueError("Recovery limit exhausted; a new owner turn is required")
                if action == "plan":
                    if goal.executed:
                        raise ValueError("Cannot weaken Definition of Done after execution; use select to replan actions")
                    goal.definition_of_done = self.validated_criteria(goal, payload.get("definition_of_done"))
                    goal.objective = clean(payload.get("objective") or goal.objective, 2500)
                    goal.intent = clean(payload.get("intent") or goal.objective)
                    goal.constraints = payload.get("constraints", [])
                    reqs = payload.get("inspection_requirements") or [dict(tool=c["tool"], args=c["args"]) for c in goal.definition_of_done]
                    if not all(isinstance(r, dict) and r.get("tool") in READ_TOOLS and isinstance(r.get("args"), dict) and r["args"] for r in reqs):
                        raise ValueError("Inspection requirements must bind read tools and target args")
                    goal.inspection_requirements = reqs
                    goal.unknowns = payload.get("unknowns", [])
                    goal.skipped_skills.update({str(k):clean(v) for k,v in payload.get("skip_skills", {}).items() if len(str(v).strip()) >= 15})
                    goal.verification_result.clear()
                    goal.state = "PLANNING"
                    goal.candidate_actions = self.suggest(goal)
                elif action == "select":
                    actions = payload.get("candidates")
                    if not isinstance(actions, list) or not 1 <= len(actions) <= 6:
                        raise ValueError("Provide 1-6 meaningful candidate actions")
                    for a in actions:
                        if not all(a.get(k) for k in ("id", "tool", "reason", "expected_observation")) or not isinstance(a.get("args"), dict):
                            raise ValueError("Candidates require id, tool, args, reason, expected_observation")
                        if a["tool"] not in goal.tools or a["tool"] == "governor":
                            raise ValueError("Candidate tool must exist in this session")
                    if goal.state == "RECOVERING" and len(str(payload.get("diagnosis", "")).strip()) < 10:
                        raise ValueError("Recovery needs a concrete diagnosis before another action")
                    goal.candidate_actions = rank(actions, bool(goal.unknowns), goal.failure_history)
                    goal.selected_action = goal.candidate_actions[0]
                    goal.selected_tool_or_skill = goal.selected_action["tool"]
                    goal.risk_level = goal.selected_action["risk"]
                    goal.next_action = goal.selected_action["reason"]
                    goal.state = "READY"
                elif action == "trust_review":
                    selected = goal.selected_action
                    if not selected:
                        raise ValueError("Select the external action before reviewing its trust")
                    evidence_ids = payload.get("evidence_ids", [])
                    source = payload.get("source", "")
                    source_inspections = {i["evidence"] for i in goal.inspections if i["tool"] in {"read_file", "web_extract"} and
                                          source and source in i["args"].values()}
                    refs = [e for e in goal.evidence if e["id"] in evidence_ids and e["id"] in source_inspections and e["success"]]
                    fields = ("source", "permissions", "dependencies", "compatibility", "license", "maintenance", "sandbox", "test_plan")
                    if not all(isinstance(payload.get(f), str) and len(payload[f].strip()) >= 8 for f in fields):
                        raise ValueError("Trust review needs source, permissions, dependencies, compatibility, license, maintenance, sandbox and test_plan")
                    if not refs or any(e["scan_findings"] for e in refs):
                        raise ValueError("Source inspection evidence must exist and pass the available threat scanner")
                    goal.trust_reviews[fingerprint([selected["tool"], selected["args"]])] = dict(summary={f:clean(payload[f]) for f in fields}, evidence_ids=evidence_ids)
                elif action == "verify":
                    goal.state = "VERIFYING"
                    if not self.verified(goal):
                        self.recover(goal, "Definition of Done missing, stale or failed independent evidence")
                    else:
                        goal.state = "VERIFIED_COMPLETE"
                        goal.confidence = "verified against explicit predicates; semantic adequacy requires review"
                        goal.oversight_needed = clean(payload.get("oversight") or "Consider regression risk; use existing cron/watchers only with authorization.") if self.config()["proactive_oversight"] else "disabled"
                        goal.next_action = "Report evidence; hand reusable learning to existing memory/skills if appropriate."
                else:
                    raise ValueError("Unknown Governor action")
                self.record(goal, "governor_" + action,
                            selected=goal.selected_action, verification=goal.verification_result,
                            diagnosis=clean(payload.get("diagnosis", "")))
                return json.dumps(dict(state=goal.state, selected_action=goal.selected_action,
                                       capabilities=goal.available_capabilities, candidates=goal.candidate_actions, evidence=goal.evidence[-8:],
                                       verification=goal.verification_result, next_action=goal.next_action), ensure_ascii=False)
            except (ValueError, KeyError, TypeError) as exc:
                return json.dumps({"error": clean(exc)})

    def before_finish(self, session_id="", **kwargs):
        with self.lock:
            if not self.config()["require_verification"]:
                return None
            goal = self.get(session_id)
            if goal is None or goal.category == "CHAT" or goal.state == "VERIFIED_COMPLETE" and self.verified(goal):
                return None
            if goal.state != "BLOCKED":
                self.recover(goal, "Attempted to finish without verified objective")
                self.record(goal, "completion_retry")
            if goal.state == "BLOCKED":
                return None
            return {"action":"continue", "message":"Governor: objective unverified. " + goal.next_action +
                    " Use governor status to inspect missing criteria. Obtain fresh independent observations, then governor verify. Do not claim success."}

    def finish(self, session_id="", **kwargs):
        with self.lock:
            goal = self.get(session_id)
            if goal is None:
                return {"verified":False, "response_text":"BLOCKED: Governor session state is unavailable."}
            passed = goal.state == "VERIFIED_COMPLETE" and self.verified(goal)
            if not passed:
                goal.state = "BLOCKED"
            self.record(goal, "final_gate", verified=passed)
            missing = [c["id"] for c in goal.definition_of_done if not goal.verification_result.get(c["id"], {}).get("passed")]
            return {"verified":passed, "response_text":
                    "BLOCKED — objective not verified.\n" + clean(goal.objective) +
                    "\nMissing evidence: " + (", ".join(missing) if missing else "an accepted Definition of Done and fresh verification") +
                    "\nObserved tool results: " + str(len(goal.evidence)) +
                    "\nNext: " + goal.next_action}

    @staticmethod
    def cli_setup(parser):
        parser.add_argument("--latest", action="store_true", help="Print latest redacted decision record")
        parser.add_argument("--diagnose", action="store_true", help="Check active policy, hooks, CLI tools and SOUL loader")

    def cli(self, args):
        if getattr(args, "diagnose", False):
            from agent.prompt_builder import load_soul_md
            from hermes_cli.config import load_config
            from hermes_cli.plugins import iter_hook_callbacks
            from hermes_cli.tools_config import _get_platform_tools
            from tools.registry import registry
            config = load_config()
            path = get_hermes_home() / "SOUL.md"
            model_config = config.get("model")
            loaded = load_soul_md(context_length=model_config.get("context_length") if isinstance(model_config, dict) else None)
            raw = path.read_text(encoding="utf-8").strip() if path.exists() else ""
            hooks = ("pre_llm_call", "pre_tool_call", "post_tool_call", "pre_verify", "completion_gate")
            report = {"plugin": "hermes-governor", "home": str(get_hermes_home()),
                      "settings": self.config(), "cli_toolsets": sorted(_get_platform_tools(config, "cli")),
                      "tool_registered": "governor" in registry.get_all_tool_names(),
                      "hooks": {h: any("hermes_governor" in getattr(cb, "__module__", "") for cb in iter_hook_callbacks(h)) for h in hooks},
                      "soul_path": str(path), "soul_loaded_in_full": bool(raw) and loaded == raw,
                      "soul_sha256": sha256(path.read_bytes()).hexdigest() if path.exists() else None,
                      "soul_characters_loaded": len(loaded or "")}
            print(json.dumps(report, indent=2))
            return
        records = sorted(plugin_data_dir("hermes-governor").glob("*.json"), key=lambda p:p.stat().st_mtime, reverse=True)
        if not records:
            print("No Governor decisions recorded yet.")
        elif args.latest:
            print(records[0].read_text(encoding="utf-8"))
        else:
            print("\n".join(str(p) for p in records[:20]))


SCHEMA = {"name":"governor", "description":
    "Objective policy: status; plan(objective,intent,constraints,unknowns,inspection_requirements:[{tool,args}],skip_skills:{name:reason},definition_of_done:[{id,description,tool,args,field,equals OR contains}]); select(candidates:[{id,tool,args,reason,expected_observation,scores:{expected_success,evidence_strength,reversibility,cost,time,dependency_count,confidence,information_gain,objective_alignment},external_code?}],diagnosis?); trust_review(source,permissions,dependencies,compatibility,license,maintenance,sandbox,test_plan,evidence_ids); verify(oversight?). Predicates bind actual separate read-only tool results and exact target arguments. No claimed success or exit code alone can verify. Execute selected actions using existing tools, then obtain fresh read evidence and verify.",
    "parameters":{"type":"object", "properties":{"action":{"type":"string","enum":["status","plan","select","trust_review","verify","escalate"]},"payload":{"type":"object"}},"required":["action"]}}
