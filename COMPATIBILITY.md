# Compatibility

This release is pinned to Hermes Agent **0.20.5**, Git commit:

```text
f377140e3ddb4c98a9e0b42c4497b8bb46ca697c
```

Supported target: a Windows Hermes installation with a normal launcher at `<HermesHome>\bin\hermes.exe`, a Git checkout at `<HermesHome>\hermes-agent`, and its Python environment at `<RepoRoot>\venv\Scripts\python.exe`.

The installer requires the exact commit, version marker, clean affected host paths, a clean `git apply --check`, and `ruamel.yaml` in Hermes' virtual environment. It refuses to patch a different revision or a target with overlapping local changes. Do not bypass those checks. Newer Hermes versions may be compatible, but must receive a reviewed patch update and full regression/normal-launcher validation first.

The package does not provide or copy models, API keys, OAuth tokens, browser cookies, user profiles, cached state, skills, or the Hermes application itself.
