# Hermes Governor

Hermes Governor adds objective ownership to an existing Hermes Agent installation. It turns a state-dependent request into a bounded operating loop: inspect the actual state, discover relevant capabilities and skills, compare candidate actions, respect risk and approval gates, execute with Hermes' existing tools, collect independent evidence, and only then permit a completion claim.

It keeps ordinary chat lightweight. For guarded work, it blocks unsupported “done”, “fixed”, “deployed”, “sent”, “working”, and “successful” claims from streaming or becoming the final result. Failed verification triggers bounded recovery and replanning; exhausted recovery becomes an evidence-bearing BLOCKED result.

> **HERMES DOES NOT EXECUTE THE INSTRUCTION. HERMES OWNS THE OBJECTIVE.**
>
> **BEST ACTION → BEST TOOL/SKILL → VERIFIED RESULT.**
>
> **NO EVIDENCE, NO DONE.**

## What is included

```text
governor/     Native plugin: policy engine, manifest, isolated tests
patches/      Minimal Hermes 0.20.5 host patch, including host regression tests
soul/         Generic, idempotent objective-ownership protocol append
installer/    Safe Windows installer, config updater, rollback support
tests/        Extra host completion-policy and streaming tests
docs/         Architecture and recorded verification summary
```

Nothing in this repository is an entire Hermes installation. It excludes credentials, `.env` files, OAuth data, model configuration, runtime databases, logs, personal SOUL context, temporary acceptance files, caches, and generated artifacts.

## Compatibility

See [COMPATIBILITY.md](COMPATIBILITY.md). The installer intentionally supports only Hermes Agent 0.20.5 at commit `f377140e3ddb4c98a9e0b42c4497b8bb46ca697c`.

## Install on another Windows PC

1. Install Hermes normally and confirm it is the exact compatible checkout.
2. Clone this repository somewhere outside Hermes' installation directory.
3. Close Hermes sessions/processes so a new launch loads the files.
4. Open PowerShell in the cloned repository and run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\installer\Install-HermesGovernor.ps1
```

For a non-default installation:

```powershell
.\installer\Install-HermesGovernor.ps1 `
  -HermesHome 'D:\Apps\hermes' `
  -RepoRoot 'D:\Source\hermes-agent'
```

Use `-RunTests -TestPython 'C:\path\to\pytest-environment\Scripts\python.exe'` to request the copied host regression files after activation. The test Python must contain Hermes dependencies and pytest. The package never asks for, reads, writes, or copies `auth.json`, `.env`, API keys, OAuth credentials, model credentials, cookies, or other secrets.

The installer checks the exact Hermes commit, version, clean affected files, host-patch applicability, launcher, Python environment, and package files. It then backs up the affected host files, existing `config.yaml`, `SOUL.md`, and any existing Governor plugin under `<HermesHome>\governor-backups\<timestamp>`. It applies only the patch in `patches/`, copies only `governor/`, appends the generic marked SOUL protocol without replacing the existing SOUL, atomically updates `config.yaml`, enables existing checkpoints, and runs `hermes governor --diagnose`. A failed installation automatically attempts restoration and retains displaced files in the backup directory.

## Configuration

The installer writes this namespaced plugin configuration while preserving unrelated YAML settings:

```yaml
plugins:
  enabled: [hermes-governor]
  entries:
    hermes-governor:
      settings:
        enabled: true
        require_verification: true
        capability_discovery: true
        research_when_uncertain: true
        github_discovery: true
        external_code_trust_gate: true
        proactive_oversight: true
        max_recovery_attempts: 3
        max_evidence: 100
toolsets: [governor]
checkpoints:
  enabled: true
```

When the target already explicitly configures `platform_toolsets.cli`, Governor is added there too. Add `governor` to other explicitly restricted platform lists only when that platform should use it.

The model may access Governor directly or through Hermes' existing deferred-tool bridge. The safe bridge path is `tool_describe(name="governor")`, followed by `tool_call(name="governor", arguments={...})`; policy and approval hooks still see the underlying action.

## Verify

After installation, start a fresh normal Hermes session and run:

```powershell
& "$env:LOCALAPPDATA\hermes\bin\hermes.exe" governor --diagnose
```

The JSON must show the target Hermes home, the `governor` tool, all five hooks, enabled verification settings, and `soul_loaded_in_full: true`. The installer rejects a launcher that diagnoses a different home. See [docs/verification.md](docs/verification.md) for the original 298-test and normal-launcher evidence.

## Tests

Run the complete portable package suite through Hermes' required test runner:

```powershell
.\tests\Test-PortablePackage.ps1 `
  -HermesRepo 'C:\path\to\hermes-agent' `
  -TestPython 'C:\path\to\pytest-environment\Scripts\python.exe'
```

The test Python must contain Hermes dependencies plus pytest. The suite includes plugin policy/integration tests, host completion/streaming tests, and installer configuration tests. During packaging, all 77 portable-package tests passed. The runner isolates `HERMES_HOME` and strips credential-like environment variables before plugin test collection. Use installer `-RunTests -TestPython ...` to rerun the two copied host regression files after activation.

## Roll back

The installer prints the precise backup directory. From this repository run:

```powershell
.\installer\Install-HermesGovernor.ps1 -RollbackBackup '<HermesHome>\governor-backups\<timestamp>'
```

Rollback refuses if patched host files changed after installation, then reverses the exact patch and restores the backed-up SOUL/config/plugin state. It never touches credentials. Restart Hermes afterward.

## Limits

Governor verifies explicit observable predicates, not arbitrary business intent. Model-authored criteria, capability relevance, and candidate scores remain judgment calls. Shell and unknown MCP actions stay behind Hermes' existing approvals. The plugin is not an OS security boundary. State is per turn with bounded redacted audit snapshots, not a new durable scheduler. See [docs/architecture.md](docs/architecture.md) and `governor/README.md` for detailed behavior and boundaries.
