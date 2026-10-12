"""Integration regressions for the reusable Action's failure/verification path.

Regulatory information, not legal advice.

These are executable regressions around the ACTUAL wrapper/entrypoint/CLI, not
YAML string searches. Where a Docker daemon is available they exercise the full
composite-wrapper contract (build from the action repository root, bind-mount the
consuming workspace, propagate exit codes, surface report-valid/report-path).
Where Docker is unavailable they run the entrypoint script directly against a
locally-created snapshot so the snapshot-verification, current-run-report and
exit-policy logic is still exercised.

The container check intentionally runs ``--network=none`` so no regulatory
network fetch can occur. This file performs no data mutation: it builds a fresh
snapshot tree in a temp directory from a hand-written fixture and reads the real
processed snapshot read-only.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "data" / "scripts"
SNP_SOURCE = ROOT / "data" / "processed"

_PYTHON_DIR = str((ROOT / ".venv" / "bin").resolve()) if (ROOT / ".venv" / "bin").exists() else "/opt/homebrew/bin"


def _env():
    e = {**os.environ, "EU_REP_ROOT": str(ROOT)}
    e["PATH"] = f"{_PYTHON_DIR}:{e.get('PATH', '')}"
    e["PYTHONPATH"] = str(ROOT / "packages/eu-reg-lists")
    return e


def _has_docker() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(["docker", "version"], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=15)
    except Exception:
        return False
    return subprocess.run(["docker", "version"], check=False,
                          stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                          timeout=15).returncode == 0


HAS_REAL_SNAPSHOT = (SNP_SOURCE / "eu_reg_lists.sqlite").exists()


def _run_entrypoint(entry, args, report_status, policy, tmp):
    return subprocess.run(
        ["bash", str(entry), *args],
        capture_output=True, text=True, timeout=240,
        env={**_env(), "RUN_STATUS_FILE": str(report_status)})


def _copy_prepared_tree(dest: Path) -> Path:
    tree = dest / "tree"
    shutil.copytree(ROOT / "data", tree / "data")
    shutil.copy2(ROOT / "LICENSE", tree / "LICENSE")
    shutil.copy2(ROOT / "LICENSES.md", tree / "LICENSES.md")
    return tree


@pytest.mark.skipif(not HAS_REAL_SNAPSHOT, reason="no processed snapshot in repo")
def test_verify_tree_accepts_real_snapshot(tmp_path):
    import sys as _sys
    _sys.path.insert(0, str(SCRIPTS))
    import snapshot
    tree = _copy_prepared_tree(tmp_path)
    try:
        meta, files = snapshot.validate_tree(tree)
        assert meta["count_substances"] == 2375
        assert meta["count_verified_rules"] == 2298
        assert files
    finally:
        shutil.rmtree(tree, ignore_errors=True)


def test_verify_tree_rejects_empty_tree(tmp_path):
    import sys as _sys
    _sys.path.insert(0, str(SCRIPTS))
    import snapshot
    bad = tmp_path / "bad"
    (bad / "data/processed").mkdir(parents=True)
    with pytest.raises(Exception):
        snapshot.validate_tree(bad)


def test_directory_snapshot_verification_rejected_by_entrypoint(tmp_path):
    """A nonempty-SQLite-but-otherwise-empty tree must NOT be accepted."""
    bad = tmp_path / "bad-snapshot"
    (bad / "data/processed").mkdir(parents=True)
    (bad / "data/processed/eu_reg_lists.sqlite").write_bytes(b"\0" * 16)
    entry = ROOT / ".github/actions/eu-ingredient-linter/entrypoint.sh"
    inp = tmp_path / "input.csv"
    inp.write_text("ingredient,percent\nBenzene,5\n")
    res = _run_entrypoint(entry, [str(inp), "csv", str(bad),
                                  str(tmp_path / "report.json"), "findings"],
                          tmp_path / "status.txt", None, tmp_path)
    assert res.returncode == 2
    assert not (tmp_path / "report.json").exists()


@pytest.mark.skipif(not HAS_REAL_SNAPSHOT, reason="no processed snapshot in repo")
def test_current_run_required_for_both_policies(tmp_path, monkeypatch):
    """A CLI exit 0/1 WITHOUT a valid new report must fail operationally."""
    entry = ROOT / ".github/actions/eu-ingredient-linter/entrypoint.sh"
    inp = tmp_path / "input.csv"
    inp.write_text("ingredient,percent\nBenzene,5\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    mock = bin_dir / "eu-ingredient-linter"
    mock.write_text("#!/usr/bin/env bash\necho 'no report' >&2\nexit 1\n")
    mock.chmod(0o755)
    env = _env()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    for policy in ("findings", "invalid-input"):
        status = tmp_path / f"status-{policy}.txt"
        res = subprocess.run(
            ["bash", str(entry), str(inp), "csv", str(ROOT),
             str(tmp_path / f"report-{policy}.json"), policy],
            capture_output=True, text=True, timeout=240, env=env)
        assert res.returncode == 2, (policy, res.stderr)
        # report-valid is not recorded on an operational failure (no report).
        if status.exists():
            assert "report-valid=0" in status.read_text()
        else:
            assert not status.exists()
        # No report should have been promoted.
        assert not (tmp_path / f"report-{policy}.json").exists()


@pytest.mark.skipif(not HAS_REAL_SNAPSHOT, reason="no processed snapshot in repo")
def test_stale_report_not_promoted_after_failed_run(tmp_path):
    """A prior valid report is not replaced by a failed run at the same path."""
    entry = ROOT / ".github/actions/eu-ingredient-linter/entrypoint.sh"
    good_inp = tmp_path / "good.csv"
    good_inp.write_text("ingredient,percent\nAqua,100\n")
    final = tmp_path / "report.json"
    status = tmp_path / "status.txt"
    # Produce a valid Aqua report at the final path.
    res = _run_entrypoint(entry, [str(good_inp), "csv", str(ROOT), str(final),
                                  "invalid-input"], status, None, tmp_path)
    assert res.returncode == 0
    assert final.exists()
    before = hashlib.sha256(final.read_bytes()).hexdigest()
    # A malformed-input run at the SAME final path must not overwrite it.
    bad_inp = tmp_path / "bad.csv"
    bad_inp.write_text('ingredient,percent\n"Benzene,5\n')
    entry2 = subprocess.run(
        ["bash", str(entry), str(bad_inp), "csv", str(ROOT), str(final), "findings"],
        capture_output=True, text=True, timeout=240, env=_env())
    assert entry2.returncode == 2
    assert hashlib.sha256(final.read_bytes()).hexdigest() == before


@pytest.mark.skipif(not HAS_REAL_SNAPSHOT, reason="no processed snapshot in repo")
def test_invalid_input_exits_2_under_both_policies(tmp_path):
    """Malformed CSV under findings and invalid-input must both fail (exit 2)."""
    entry = ROOT / ".github/actions/eu-ingredient-linter/entrypoint.sh"
    inp = tmp_path / "bad.csv"
    inp.write_text('ingredient,percent\n"Benzene,5\n')
    for policy in ("findings", "invalid-input"):
        status = tmp_path / f"status-{policy}.txt"
        res = _run_entrypoint(entry, [str(inp), "csv", str(ROOT),
                                      str(tmp_path / f"never-{policy}.json"), policy],
                              status, None, tmp_path)
        assert res.returncode == 2, (policy, res.stderr)
        assert not (tmp_path / f"never-{policy}.json").exists()


def test_command_injection_in_missing_filename_is_escaped():
    """A newline + injected ::error in a missing filename cannot start a new command."""
    entry = ROOT / ".github/actions/eu-ingredient-linter/entrypoint.sh"
    missing = "lost\n::error file=INJECTED-REVIEW-MARKER.csv"
    res = subprocess.run(
        ["bash", str(entry), missing, "csv", "/tmp/__snap__", "/tmp/__r__.json", "findings"],
        capture_output=True, text=True, timeout=120, env=_env())
    assert res.returncode == 2
    for line in res.stderr.splitlines():
        assert not line.startswith("::error file=INJECTED-REVIEW-MARKER")


@pytest.mark.skipif(not HAS_REAL_SNAPSHOT, reason="no processed snapshot in repo")
def test_snapshot_preserved_after_runs():
    """The repo snapshot must be byte-identical after the entrypoint runs."""
    before = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in SNP_SOURCE.glob("*") if p.is_file()
    }
    inp = Path("/tmp/__preserve_aqua__.csv")
    inp.write_text("ingredient,percent\nAqua,100\n")
    subprocess.run(["bash", str(ROOT / ".github/actions/eu-ingredient-linter/entrypoint.sh"),
                    str(inp), "csv", str(ROOT), "/tmp/__preserve_report__.json", "invalid-input"],
                   capture_output=True, text=True, timeout=240, env=_env())
    after = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in SNP_SOURCE.glob("*") if p.is_file()
    }
    assert before == after
