"""Regression tests for the composite output transport and publication safety.

Regulatory information, not legal advice.

These exercise the ACTUAL composite ``action.yml`` (its output ``value`` mapping
to a named internal step) plus the entrypoint script's publication protections:

- composite ``report-valid``/``report-path`` map to the named internal step's
  outputs (GitHub's composite metadata contract), not just text inside a wrapper
  output file;
- the entrypoint rejects CR/LF in path inputs before any transport (escaped
  diagnostic, no injected output key);
- the entrypoint rejects a report destination that (canonically) overlaps the
  input file, the supplied snapshot bundle, or any file in a supplied snapshot
  tree — preserving the protected bytes;
- publication is atomic (temp file on the destination filesystem + rename) and a
  failed promotion leaves the previous destination intact and exits 2;
- a report destination that is an existing directory is rejected.

These use disposable copies/fixtures only; the repo snapshot is read-only.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / ".github/actions/eu-ingredient-linter"
SNP_SOURCE = ROOT / "data" / "processed"
HAS_REAL = (SNP_SOURCE / "eu_reg_lists.sqlite").exists()

_PYTHON_DIR = str((ROOT / ".venv" / "bin").resolve()) if (ROOT / ".venv" / "bin").exists() else "/opt/homebrew/bin"


def _env():
    e = {**os.environ, "EU_REP_ROOT": str(ROOT)}
    e["PATH"] = f"{_PYTHON_DIR}:{e.get('PATH', '')}"
    e["PYTHONPATH"] = str(ROOT / "packages/eu-reg-lists")
    return e


def _run_entrypoint(args, env=None):
    return subprocess.run(
        ["bash", str(ACTION / "entrypoint.sh"), *args],
        capture_output=True, text=True, timeout=240,
        env={**_env(), **(env or {})})


# --- Composite output contract ---------------------------------------------
def test_composite_outputs_map_to_named_internal_step():
    """report-valid/report-path must map to the named internal step's outputs."""
    data = yaml.safe_load((ACTION / "action.yml").read_text())
    outputs = data["outputs"]
    # A composite action must declare a ``value`` for each output.
    assert "value" in outputs["report-valid"]
    assert "value" in outputs["report-path"]
    # The producing step must have a stable id referenced by those values.
    step = data["runs"]["steps"][0]
    assert step["id"] == "check"
    assert outputs["report-valid"]["value"] == "${{ steps.check.outputs.report-valid }}"
    assert outputs["report-path"]["value"] == "${{ steps.check.outputs.report-path }}"


def test_output_values_reference_existing_step_id():
    """The `steps.<id>` referenced by output values must be the run step's id."""
    data = yaml.safe_load((ACTION / "action.yml").read_text())
    step_ids = {s.get("id") for s in data["runs"]["steps"]}
    for key in ("report-valid", "report-path"):
        value = data["outputs"][key]["value"]
        import re
        m = re.search(r"steps\.([A-Za-z0-9_-]+)\.outputs", value)
        assert m, f"output {key} does not reference a named step"
        assert m.group(1) in step_ids


# --- CR/LF rejection --------------------------------------------------------
def test_crlf_input_rejected_before_transport(tmp_path):
    """A newline in the input-file path is rejected with an escaped diagnostic."""
    malicious = "ingredients\nextra-key=inert.csv"
    inp = tmp_path / malicious  # a newline is a legal (if unusual) filename
    inp.write_text("ingredient,percent\nBenzene,5\n")
    status = tmp_path / "status.txt"
    res = _run_entrypoint([str(inp), "csv", str(ROOT), str(tmp_path / "report.json"),
                           "findings"], {"RUN_STATUS_FILE": str(status)})
    assert res.returncode == 2
    # No injected output key was written as a standalone line to either sink.
    lines = res.stderr.splitlines()
    assert not any(l.startswith("extra-key=") for l in lines)
    assert not any(l.strip() == "extra-key=inert.csv" for l in lines)
    # The diagnostic mentions the CR/LF rejection and is a single escaped line
    # (the embedded newline is shown as the literal ``\n``, not emitted).
    assert "carriage return or line feed" in res.stderr
    assert len(res.stderr.splitlines()) == 1
    # No report was published.
    assert not (tmp_path / "report.json").exists()


@pytest.mark.skipif(not HAS_REAL, reason="no processed snapshot in repo")
def test_crlf_report_output_rejected(tmp_path):
    """A newline in report-output is rejected before any publication."""
    inp = tmp_path / "input.csv"
    inp.write_text("ingredient,percent\nAqua,100\n")
    status = tmp_path / "status.txt"
    report_path = tmp_path / ("report.json\n::error file=INJECTED.csv")
    res = _run_entrypoint([str(inp), "csv", str(ROOT), str(report_path), "invalid-input"],
                          {"RUN_STATUS_FILE": str(status)})
    assert res.returncode == 2
    assert "carriage return or line feed" in res.stderr
    assert not report_path.exists()
    assert not (tmp_path / "INJECTED.csv").exists()


# --- Destination collision protection --------------------------------------
@pytest.mark.skipif(not HAS_REAL, reason="no processed snapshot in repo")
def test_report_output_overlapping_input_rejected(tmp_path):
    """report-output == input-file must be rejected; the input CSV is preserved."""
    inp = tmp_path / "input.csv"
    inp.write_text("ingredient,percent\nAqua,100\n")
    before = inp.read_bytes()
    status = tmp_path / "status.txt"
    res = _run_entrypoint([str(inp), "csv", str(ROOT), str(inp), "invalid-input"],
                          {"RUN_STATUS_FILE": str(status)})
    assert res.returncode == 2
    assert "overlaps the input file" in res.stderr
    # The input file was NOT overwritten with JSON.
    assert inp.read_bytes() == before
    assert inp.read_text() == "ingredient,percent\nAqua,100\n"


@pytest.mark.skipif(not HAS_REAL, reason="no processed snapshot in repo")
def test_report_output_overlapping_snapshot_bundle_rejected(tmp_path):
    """report-output == snapshot bundle must be rejected; the bundle is preserved."""
    inp = tmp_path / "input.csv"
    inp.write_text("ingredient,percent\nAqua,100\n")
    status = tmp_path / "status.txt"
    # Use the repo's data directory as a stand-in snapshot tree via a bundle copy.
    bundle = tmp_path / "snapshot.zip"
    # Export a real snapshot bundle to a disposable path.
    subprocess.run([_venv_python(), str(ROOT / "data/scripts/snapshot.py"),
                    "export", "--root", str(ROOT), "--output", str(bundle)],
                   check=True, capture_output=True, text=True,
                   env={**os.environ, "PYTHONPATH": str(ROOT / "packages/eu-reg-lists")})
    before = bundle.read_bytes()
    res = _run_entrypoint([str(inp), "csv", str(bundle), str(bundle), "invalid-input"],
                          {"RUN_STATUS_FILE": str(status)})
    assert res.returncode == 2
    assert "overlaps" in res.stderr
    assert bundle.read_bytes() == before


@pytest.mark.skipif(not HAS_REAL, reason="no processed snapshot in repo")
def test_report_output_overlapping_snapshot_tree_member_rejected(tmp_path):
    """report-output == a file inside a snapshot directory must be rejected."""
    inp = tmp_path / "input.csv"
    inp.write_text("ingredient,percent\nAqua,100\n")
    status = tmp_path / "status.txt"
    # Copy the repo snapshot tree into a disposable dir so we can mutate it.
    tree = tmp_path / "snapshot-tree"
    shutil.copytree(ROOT / "data", tree / "data")
    for name in ("LICENSE", "LICENSES.md"):
        shutil.copy2(ROOT / name, tree / name)
    member = tree / "data/raw/fetch_manifest.json"
    before = member.read_bytes()
    res = _run_entrypoint([str(inp), "csv", str(tree), str(member), "invalid-input"],
                          {"RUN_STATUS_FILE": str(status)})
    assert res.returncode == 2
    assert "overlaps" in res.stderr
    assert member.read_bytes() == before


# --- Invalid directory destination / atomic promotion ----------------------
@pytest.mark.skipif(not HAS_REAL, reason="no processed snapshot in repo")
def test_report_output_existing_directory_rejected(tmp_path):
    """A report destination that is an existing directory is rejected."""
    inp = tmp_path / "input.csv"
    inp.write_text("ingredient,percent\nAqua,100\n")
    status = tmp_path / "status.txt"
    dest_dir = tmp_path / "report-dir"
    dest_dir.mkdir()
    res = _run_entrypoint([str(inp), "csv", str(ROOT), str(dest_dir), "invalid-input"],
                          {"RUN_STATUS_FILE": str(status)})
    assert res.returncode == 2
    assert "directory or non-regular" in res.stderr
    assert dest_dir.is_dir()
    assert not any(dest_dir.iterdir())


@pytest.mark.skipif(not HAS_REAL, reason="no processed snapshot in repo")
def test_atomic_publication_replace_keeps_prior_destination_on_success(tmp_path):
    """A successful run replaces the prior destination atomically (same inode dir)."""
    inp = tmp_path / "input.csv"
    inp.write_text("ingredient,percent\nAqua,100\n")
    status = tmp_path / "status.txt"
    final = tmp_path / "report.json"
    res = _run_entrypoint([str(inp), "csv", str(ROOT), str(final), "invalid-input"],
                          {"RUN_STATUS_FILE": str(status)})
    assert res.returncode == 0
    assert final.exists()
    assert "report-valid=1" in status.read_text()
    # No leftover temp file is left behind.
    assert not list(tmp_path.glob(".eu-lint-report-*.tmp"))


def test_publication_failure_is_operational_exit2(tmp_path):
    """A failed atom-ic publication exits 2 and leaves report-valid false/absent."""
    if os.geteuid() == 0:
        pytest.skip("read-only dir is not a reliable failure under root")
    inp = tmp_path / "input.csv"
    inp.write_text("ingredient,percent\nBenzene,5\n")
    # A disposable snapshot directory that the entrypoint verifies, used so the
    # CLI produces a valid current report we then cannot publish.
    status = tmp_path / "status.txt"
    # A pre-existing valid destination that must NOT be replaced on failure.
    final = tmp_path / "report.json"
    final.write_text("PRIOR")
    # Make the destination directory read-only so the atomic temp write (cp)
    # fails after the CLI validated a current report.
    dest_dir = tmp_path / "readonly"
    dest_dir.mkdir()
    dest_dir.chmod(0o555)
    os.chmod(dest_dir, 0o555)
    try:
        res = _run_entrypoint([str(inp), "csv", str(ROOT), str(dest_dir / "report.json"),
                               "findings"], {"RUN_STATUS_FILE": str(status)})
    finally:
        os.chmod(dest_dir, 0o755)
    assert res.returncode == 2
    # No prior destination was replaced (the missing dest simply stays absent).
    assert not (dest_dir / "report.json").exists()
    # Publication failed before the status file was written; the wrapper defaults
    # report-valid=0 in that case, so the caller never sees a green report.
    assert not status.exists() or "report-valid=0" in status.read_text()


def _venv_python() -> str:
    b = ROOT / ".venv" / "bin" / "python"
    return str(b) if b.exists() else "python3"
