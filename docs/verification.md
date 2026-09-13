# Verification evidence

This package was validated against Hermes Agent 0.20.5, commit `f377140e3ddb4c98a9e0b42c4497b8bb46ca697c`.

The original implementation passed 298 automated tests and a normal Hermes-launcher acceptance workflow. The test breakdown was 31 Governor policy tests, 9 native integration/full-loop tests, 4 completion-policy host tests, 30 new streaming tests, and 224 targeted Hermes regression tests. Evidence was deliberately summarized here; original logs and session data contained local paths and were excluded.

The portable package was separately revalidated before publication. Its 77-test suite passed: 31 Governor policy tests, 9 native integration/full-loop tests, 30 streaming tests, 4 completion-policy host tests, and 3 installer configuration tests. The installer also passed disposable-checkout installation, diagnostic, automatic-failure rollback, and explicit rollback exercises.

The normal-launcher workflow used the existing configured provider and a temporary local fixture. Governor discovered a relevant skill, inspected the fixture, selected a targeted patch, observed the fresh read result, passed three explicit Definition-of-Done predicates, and reached `VERIFIED_COMPLETE` through two final gates. A separate trivial chat made zero tool calls. Additional full-loop tests cover a successful tool action that leaves the objective false, bounded recovery, unsupported completion claims, destructive-action approval, and deferred-tool bridge operation.

Before treating a new installation as active, run:

```powershell
& "$env:LOCALAPPDATA\hermes\bin\hermes.exe" governor --diagnose
```

The JSON must report `tool_registered: true`, all five hooks true, `settings.enabled: true`, `settings.require_verification: true`, and `soul_loaded_in_full: true`.
