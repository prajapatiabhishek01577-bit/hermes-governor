# Hermes Governor v1

Native user plugin for objective ownership with technical execution and completion gates. Requires the accompanying additive `plugin_completion_policy` host extension. No external packages at runtime.

## How it works

1. Per-turn routing leaves general conversation alone. State-dependent requests and actionable objectives get a goal record.
2. Read tools remain available for inspection and research. Governor automatically shortlists current session tools and installed skills using metadata. `plan` binds observable success predicates to exact read-tool target arguments and generates inspection/skill/research candidates.
3. `select` ranks 1-6 meaningful model-proposed actions. Scores are bounded, inspectable heuristics, with strong uncertainty/risk/repeated-failure penalties. The selected tool and arguments must match execution.
4. Writes require a plan, matching inspection evidence, consideration of relevant skills, and action selection. Sensitive/destructive/unknown tool effects use existing Hermes approvals. Existing checkpoints protect file changes when enabled.
5. Host post-tool hooks record success/failure, source, call ID, time, digest, scan findings and transcript references. Raw results are evaluated transiently, not copied into a second output database.
6. DoD predicates are evaluated only against actual independent read results. Mutation results cannot satisfy them. Subsequent mutations or failed checks invalidate proof. The model cannot submit evidence or set a completion flag.
7. `verify` requires every criterion to pass against the current mutation revision. Failure requires diagnosis/replanning, penalizes repeats, and eventually blocks. `pre_verify` reuses Hermes' bounded continuation loop for code and non-code requests.
8. Required policy turns buffer display/TTS/interim answer text. The final gate rejects unsupported completion before final persistence and after cosmetic output transforms. `completed=false`, `objective_verified=false`, and a bounded blocker report are returned on failure.

State follows the existing session/task/turn IDs. Redacted decision snapshots live under the standard `plugin-data/hermes-governor` directory, bounded to 200 records, 100 trace events, and 100 evidence references by default. `hermes governor --latest` inspects the newest record. These are policy records, not another scheduler or task queue.

`hermes governor --diagnose` checks the active profile's settings, CLI toolsets, native hook registrations and complete SOUL loader result without printing credentials or prompt contents.

Hermes normally defers plugin tool schemas. Governor uses that existing architecture: `tool_describe(name="governor")` exposes its schema and `tool_call(name="governor", arguments={"action":..., "payload":...})` invokes it. The host unwraps the bridge before approvals and hooks, so Governor records and gates the actual underlying action. The completion-policy bridge inventories session-scoped deferred tools through Hermes' existing scope resolver; it neither exposes disabled tools nor changes the model's tool array. Discovery calls remain read-only and cannot satisfy completion predicates.

## Configuration

Use `plugins.entries.hermes-governor.settings` in existing `config.yaml`:

```yaml
enabled: true
require_verification: true
capability_discovery: true
research_when_uncertain: true
github_discovery: true
external_code_trust_gate: true
proactive_oversight: true
max_recovery_attempts: 3
max_evidence: 100
```

Enable native plugin `hermes-governor` and toolset `governor` on the relevant surfaces. The installation enables it for the existing configured surfaces and enables Hermes' existing checkpoints. New profiles need their own configuration; they do not inherit this owner's settings.

`enabled=false` disables the engine on the next turn; fresh sessions are recommended. `require_verification=false` explicitly opts out of the final gate/buffering/stop retries while retaining execution prerequisites; it must not be interpreted as verified completion. `github_discovery` controls GitHub inclusion in suggested research, not a ban on owner-requested browsing. Disable via owner configuration, not through an agent-selected action.

## Model protocol example

Use `governor` with `action=plan` and a payload like:

```json
{
  "objective": "Restore healthy state in /workspace/service-state.txt",
  "intent": "Repair the controlled service-state fixture",
  "constraints": ["Only edit this fixture"],
  "unknowns": [],
  "definition_of_done": [{
    "id": "healthy", "description": "Fresh read reports healthy",
    "tool": "read_file", "args": {"path": "/workspace/service-state.txt"},
    "field": "content", "contains": "healthy"
  }]
}
```

Inspect that exact file. Load matching skills with `skill_view`, or provide concrete `skip_skills` reasons in the plan. Select a candidate containing `id`, `tool`, exact `args`, `reason`, `expected_observation`, and optional normalized `scores`. Execute through the original Hermes tool. Independently read the target again, then call `governor(action=verify)`. Read the returned state, not just the tool transport status.

Supported criterion predicates are `equals` or nonempty `contains` on a dot-separated result field. Discovery-only calls and top-level status/exit-code checks are rejected. Use actual target observations (file, browser, extracted endpoint, existing task state). Source inspection checks target arguments, so reading an unrelated file cannot satisfy a prerequisite.

Build exact predicates from the actual tool response format before execution. For example, `read_file.content` includes line numbers and can include a trailing empty-line marker. Combine content, `total_lines`, `file_size`, and `truncated` checks when exact file shape matters. DoD remains immutable after a dispatched mutation; an incorrect predicate must be reported as blocked rather than silently weakened. Local file uncertainty calls for local inspection; unresolved external technical questions trigger documentation/source research when those capabilities are available.

For recovery, pass a concrete `diagnosis` with alternative candidates to `select`. DoD cannot be weakened after execution. For owner decisions use `escalate` with `discovered`, `attempted`, `options`, `recommendation`, and `decision`. Existing clarification tools also remain available.

External install/run candidates require a trust review bound to that exact action, source-specific inspected evidence passing Hermes' threat scanner, and written permissions/dependencies/compatibility/license/maintenance/sandbox/test-plan evaluation. Shell installs still go through the existing approval gate. Source discovery never itself installs anything.

After success, Governor recommends reuse of existing memory/skills and considers existing cron/watchers. It never creates background monitoring merely because a task ended.

## Boundaries

- This is a deterministic enforcement foundation, not a proof that an LLM understands arbitrary business intent. Objective extraction, predicate adequacy, skill relevance and action scores still depend partly on model judgment/heuristics. Poorly chosen success predicates can establish a narrower result than the owner intended.
- The read-tool allowlist and field predicates are deliberately conservative. Arbitrary terminal commands are sensitive, including test runners. External read-only MCP tools and semantic/business outcomes need additional reviewed verifier adapters; they are not assumed safe from their names.
- Shell programs are not statically understood. Unknown commands require approval. Threat scanning and documented review reduce risk but cannot prove arbitrary code safe. Approval inherits the owner's existing Hermes approval mode and grants; Governor does not grant approval itself.
- A plugin is not an OS security boundary against another trusted plugin, a compromised process, or owner filesystem access. Agent policy self-edits are blocked for recognized targets; general shell behavior still relies on approvals and the host sandbox.
- Goals are scoped to a turn and linked to existing tasks. Snapshots are audit records, not automatic resumption of long-running objectives. Continue long-running work through existing Kanban/session/event machinery.
- Buffering prevents unverified answer text from being streamed on guarded turns. Tool progress and reset signals remain visible. Chat turns remain lightweight.
- Existing running Hermes processes must restart to load code/plugin/toolset changes. A future Hermes update may need the small host bridge reapplied/revalidated. Run native-discovery and full-loop tests after upgrades.

## Verification

Use Hermes' canonical `scripts/run_tests.sh` with this plugin's test files, explicit `-c <plugin>/pytest.ini`, and a Python environment containing Hermes dependencies plus pytest. The package defers imports until registration so pytest can collect it without executing registration.

Tests cover the ten requested scenarios, stale/forged/mismatched evidence, immutable DoD, profile isolation, bounded traces, source-bound trust reviews, real plugin discovery, actual file tools through the full AIAgent loop (including the native deferred-tool bridge), denied approvals, buffered text, and rejected completion in the final transcript. The automated test transport is deterministic and offline. A separate normal-launcher acceptance run uses the owner's existing provider and actual temporary files; its captured evidence and limitations are in the installation report.
