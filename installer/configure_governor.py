"""Preserve existing config.yaml while enabling the local Governor plugin."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

from ruamel.yaml import YAML


SETTINGS = {
    "enabled": True,
    "require_verification": True,
    "capability_discovery": True,
    "research_when_uncertain": True,
    "github_discovery": True,
    "external_code_trust_gate": True,
    "proactive_oversight": True,
    "max_recovery_attempts": 3,
    "max_evidence": 100,
}


def list_setting(container: dict, key: str) -> list:
    value = container.get(key)
    if value is None:
        value = []
        container[key] = value
    if not isinstance(value, list):
        raise ValueError(f"config.{key} must be a YAML list")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    path = Path(args.config)
    yaml = YAML()
    yaml.preserve_quotes = True
    config = yaml.load(path.read_text(encoding="utf-8")) if path.exists() else {}
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise ValueError("config.yaml root must be a mapping")

    plugins = config.setdefault("plugins", {})
    if not isinstance(plugins, dict):
        raise ValueError("config.plugins must be a mapping")
    enabled = list_setting(plugins, "enabled")
    if "hermes-governor" not in enabled:
        enabled.append("hermes-governor")
    disabled = plugins.get("disabled")
    if disabled is not None:
        if not isinstance(disabled, list):
            raise ValueError("config.plugins.disabled must be a YAML list")
        while "hermes-governor" in disabled:
            disabled.remove("hermes-governor")
    entries = plugins.setdefault("entries", {})
    if not isinstance(entries, dict):
        raise ValueError("config.plugins.entries must be a mapping")
    entry = entries.setdefault("hermes-governor", {})
    if not isinstance(entry, dict):
        raise ValueError("config.plugins.entries.hermes-governor must be a mapping")
    settings = entry.setdefault("settings", {})
    if not isinstance(settings, dict):
        raise ValueError("Governor settings must be a mapping")
    settings.update(SETTINGS)

    toolsets = list_setting(config, "toolsets")
    if "governor" not in toolsets:
        toolsets.append("governor")
    platform_toolsets = config.get("platform_toolsets")
    if platform_toolsets is not None:
        if not isinstance(platform_toolsets, dict):
            raise ValueError("config.platform_toolsets must be a mapping")
        cli = platform_toolsets.get("cli")
        if cli is not None:
            if not isinstance(cli, list):
                raise ValueError("config.platform_toolsets.cli must be a YAML list")
            if "governor" not in cli:
                cli.append("governor")
    checkpoints = config.setdefault("checkpoints", {})
    if isinstance(checkpoints, bool):
        checkpoints = {"enabled": checkpoints}
        config["checkpoints"] = checkpoints
    if not isinstance(checkpoints, dict):
        raise ValueError("config.checkpoints must be a mapping or boolean")
    checkpoints["enabled"] = True

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            yaml.dump(config, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    print(json.dumps({"config": str(path), "enabled": True, "checkpoints": True}))


if __name__ == "__main__":
    main()
