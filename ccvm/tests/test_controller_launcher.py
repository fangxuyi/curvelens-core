"""The host adapter must preserve freshness guards without widening commands."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "host_launcher", Path(__file__).resolve().parents[2] / "agent/run_controller.py",
)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


@pytest.mark.parametrize("product", ["wti", "gold", "corn", "silver"])
def test_daily_guard_passes_through_for_every_active_product(product):
    args = ["start", "--date", "2026-09-17", "--expected-date", "2026-09-18"]
    assert launcher.validated_arguments(args, product) == args


@pytest.mark.parametrize("args", [
    ["start", "--date", "2026-09-18", "--restart"],
    ["learn", "--date", "2026-09-18", "--expected-date", "2026-09-18"],
    ["promote-learning", "--date", "2026-09-18"],
    ["start", "--date", "20260918"],
    ["start", "--date", "2026-09-18", "--expected-date", "not-a-date"],
])
def test_rejects_invalid_or_privileged_arguments(args):
    with pytest.raises(ValueError):
        launcher.validated_arguments(args, "wti")


def test_product_is_never_implicit():
    with pytest.raises(ValueError):
        launcher.validated_arguments(["start", "--date", "2026-09-18"], None)


def test_launcher_forwards_guard_and_subprocess_status(monkeypatch):
    args = ["advance", "--date", "2026-09-17", "--expected-date", "2026-09-18"]
    monkeypatch.setenv("CCVM_PRODUCT", "wti")
    monkeypatch.delenv("CODEX_SANDBOX", raising=False)
    monkeypatch.setattr(launcher.sys, "argv", ["launcher", *args])
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return 1
    monkeypatch.setattr(launcher.subprocess, "call", run)
    assert launcher.main() == 1
    assert calls[0][2:] == args


def test_sandbox_preflight_does_not_launch(monkeypatch, capsys):
    monkeypatch.setenv("CCVM_PRODUCT", "wti")
    monkeypatch.setenv("CODEX_SANDBOX", "seatbelt")
    monkeypatch.setattr(launcher.sys, "argv", ["launcher", "inspect", "--date", "2026-09-18"])
    monkeypatch.setattr(launcher.subprocess, "call", lambda *a, **k: pytest.fail("must not launch"))
    assert launcher.main() == 1
    assert "PROVIDER_PERMISSION_REQUIRED" in capsys.readouterr().out
