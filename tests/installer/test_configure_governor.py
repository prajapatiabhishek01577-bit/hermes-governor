import subprocess
import sys
from pathlib import Path

from ruamel.yaml import YAML


SCRIPT = Path(__file__).resolve().parents[2] / "installer" / "configure_governor.py"


def run_config(path: Path):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--config", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )


def test_preserves_unrelated_configuration_and_enables_governor(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "model: local-test-model\n"
        "plugins:\n  enabled: [existing-plugin]\n"
        "toolsets: [skills]\n"
        "platform_toolsets:\n  cli: [hermes-cli]\n"
        "checkpoints:\n  enabled: false\n",
        encoding="utf-8",
    )
    run_config(path)
    data = YAML().load(path)
    assert data["model"] == "local-test-model"
    assert data["plugins"]["enabled"] == ["existing-plugin", "hermes-governor"]
    assert data["plugins"]["entries"]["hermes-governor"]["settings"]["require_verification"] is True
    assert data["toolsets"] == ["skills", "governor"]
    assert data["platform_toolsets"]["cli"] == ["hermes-cli", "governor"]
    assert data["checkpoints"]["enabled"] is True
    assert not list(tmp_path.glob("*.tmp"))


def test_creates_minimal_configuration_when_missing(tmp_path):
    path = tmp_path / "config.yaml"
    run_config(path)
    data = YAML().load(path)
    assert data["plugins"]["enabled"] == ["hermes-governor"]
    assert data["toolsets"] == ["governor"]
    assert data["checkpoints"]["enabled"] is True


def test_rejects_incompatible_shapes_without_replacing_original(tmp_path):
    path = tmp_path / "config.yaml"
    original = "plugins: invalid-shape\n"
    path.write_text(original, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--config", str(path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert path.read_text(encoding="utf-8") == original
