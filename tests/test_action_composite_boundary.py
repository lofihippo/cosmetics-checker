"""Regression tests for the composite workflow/Action boundary.

Regulatory information, not legal advice.

These exercise the ACTUAL composite `action.yml` (its `env:`/`run:` split) and
the report contract so that:

- Action inputs are passed as *data* (via step `env` + quoted shell vars), never
  interpolated into shell source where a `$(...)`/backtick value could execute;
- the run body references `${{ inputs.* }}` ONLY inside `env`, never inline;
- a fresh per-invocation report is validated before the finding policy and a
  stale/absent report is never published as current (report-valid/report-path).

The injection test substitutes the downstream wrapper with a stub that records
its argv, runs the RENDERED composite run script (replacing the two supported
GitHub refs), and asserts the inert marker is not created and the malicious
value arrives as data. No Docker is required.
"""
from __future__ import annotations

import json as _json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / ".github/actions/eu-ingredient-linter"
SNP = ROOT / "data" / "processed"
HAS_REAL = (SNP / "eu_reg_lists.sqlite").exists()

def _env():
    # Use the ACTIVE interpreter's directory, never a hardcoded venv/Homebrew path.
    e = {**os.environ, "EU_REP_ROOT": str(ROOT)}
    e["PATH"] = f"{Path(sys.executable).parent}:{e.get('PATH', '')}"
    e["PYTHONPATH"] = str(ROOT / "packages/eu-reg-lists")
    return e


def test_action_inputs_live_only_in_env_not_run_body():
    """Inputs must be referenced in `env` (data), never injected into the run body."""
    data = yaml.safe_load((ACTION / "action.yml").read_text())
    step = data["runs"]["steps"][0]
    run = step["run"]
    env = step.get("env", {})
    for key in ("INPUT_FILE", "INPUT_FORMAT", "SNAPSHOT_PATH", "REPORT_OUTPUT", "FAIL_ON"):
        assert key in env, f"missing env key {key}"
    # No `inputs.*` expression may appear inline in the run body.
    for name in ("input-file", "input-format", "snapshot-path", "report-output", "fail-on"):
        assert f"inputs.{name}" not in run, f"inputs.{name} leaked into run body"
    # The only hub expressions the run body may contain are the action/workspace refs.
    for ref in ("github.action_path", "github.workspace"):
        assert ref in run


def test_injection_value_stays_data_no_marker(tmp_path):
    """A `$(...)`/backtick value passed via env is data and does not execute."""
    data = yaml.safe_load((ACTION / "action.yml").read_text())
    step = data["runs"]["steps"][0]
    run = step["run"]

    marker = tmp_path / "INJECTED-MARKER.txt"
    malicious = f"$(touch {marker})"
    argrec = tmp_path / "argv.txt"

    # Stub wrapper: record argv to a file, exit 0, never create a marker.
    stub = tmp_path / "docker-wrapper.sh"
    stub.write_text(f'#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "{argrec}"\n')
    stub.chmod(0o755)

    # Render: substitute the two supported GitHub refs with the stub dir + tmp ws.
    run = run.replace("${{ github.action_path }}", str(tmp_path))
    run = run.replace("${{ github.workspace }}", str(tmp_path))
    script = tmp_path / "rendered.sh"
    script.write_text(run)

    env = {
        **os.environ,
        "INPUT_FILE": malicious,
        "INPUT_FORMAT": "csv",
        "SNAPSHOT_PATH": "/tmp/snapshot-bundle.zip",
        "REPORT_OUTPUT": "/tmp/report.json",
        "FAIL_ON": "findings",
    }
    res = subprocess.run(["bash", str(script)], capture_output=True, text=True,
                         env=env, cwd=str(tmp_path), timeout=60)
    assert res.returncode == 0, res.stderr
    # The command substitution must NOT have executed (no marker file).
    assert not marker.exists()
    # But the literal value must have reached the wrapper as data.
    assert argrec.exists()
    assert "INJECTED-MARKER" in argrec.read_text()


def test_report_validation_flag(tmp_path):
    """`--check-report` (via the CLI validator) rejects missing/empty/malformed and accepts a valid report."""
    sys.path.insert(0, str(ROOT / "packages/eu-ingredient-linter"))
    sys.path.insert(0, str(ROOT / "packages/eu-reg-lists"))
    from eu_ingredient_linter.__main__ import _validate_report
    assert _validate_report(str(tmp_path / "nope.json")) == 2
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    assert _validate_report(str(bad)) == 2
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{ not json")
    assert _validate_report(str(malformed)) == 2
    ok = tmp_path / "ok.json"
    ok.write_text(_json.dumps({
        "disclaimer": "Regulatory information, not legal advice.",
        "data_updated_at": "2026-01-01T00:00:00+00:00",
        "evaluations": [{"ingredient": "Aqua", "determination": "unknown"}],
    }))
    assert _validate_report(str(ok)) == 0
