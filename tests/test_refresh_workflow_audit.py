"""Locally reproduce the refresh-data workflow audit-retention shell logic.

Regulatory information, not legal advice.

The workflow's ``Restore and refresh isolated snapshot`` step now delegates the
ENTIRE run block to the shared executable body ``data/scripts/
refresh_workflow_step.sh``. This test drives that SAME script with local
substituted paths and real local refresh runs (no-change, changed, a failing
baseline, and the missing-preflight-config cases) to prove:

  * the reviewable audit path is ALWAYS initialized (including preflight
    failures where baseline URL/digest is missing), so the workflow's
    ``if: always()`` upload always receives a populated path;
  * a failed refresh still surfaces ``status=failure`` + a non-zero exit while
    retaining the audit;
  * the changed-candidate upload stays gated on a valid changed output and no
    candidate artifact is produced on no-change/failure.

This is not a hosted Actions run: it executes the shared workflow step body with
local substituted paths and the real maintained refresh script.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "data" / "scripts" / "refresh_isolated.py"
STEP = ROOT / "data" / "scripts" / "refresh_workflow_step.sh"
SNAP = ROOT / "data" / "scripts" / "snapshot.py"
PY = str(ROOT / ".venv" / "bin" / "python") if (ROOT / ".venv" / "bin" / "python").exists() else "python3"


def _env():
    return {**os.environ, "PYTHONPATH": str(ROOT / "packages/eu-reg-lists")}


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def baseline_bundle(tmp_path_factory):
    out = tmp_path_factory.mktemp("baseline") / "snapshot-bundle.zip"
    subprocess.run([PY, str(SNAP), "export", "--root", str(ROOT),
                    "--output", str(out)],
                   check=True, capture_output=True, text=True, env=_env())
    yield out, _sha256(out)


def _run_step(tmp_path, baseline, digest, annex_ii_fixture=None):
    """Run the SHARED workflow step body against the real refresh script.

    The step is invoked directly (bash <scripts/refresh_workflow_step.sh>) with
    environment supplying WORK/OUT/URL/EXPECTED/AUDIT/GITHUB_OUTPUT/PY/SCRIPT.
    The real workflow passes ``LIVE=1``; the tests never hit the network, so
    they leave LIVE unset (offline) and stage the canonical Annex II source as
    an offline fixture for the changed-case test via ``SOURCE_FILE``.
    """
    work = tmp_path / "work"
    out = tmp_path / "out"
    audit = tmp_path / "refresh-audit"
    gho = tmp_path / "GITHUB_OUTPUT"
    env = {**_env(), "WORK": str(work), "OUT": str(out), "URL": f"file://{baseline}",
           "EXPECTED": digest, "AUDIT": str(audit), "GITHUB_OUTPUT": str(gho),
           "PY": PY, "SCRIPT": str(SCRIPT)}
    if annex_ii_fixture:
        # ``annex_ii_fixture`` is already the full ``key=path`` value; pass it
        # through verbatim as the step's SOURCE_FILE (no duplicate key prefix).
        env["SOURCE_FILE"] = annex_ii_fixture
    res = subprocess.run(["bash", str(STEP)], capture_output=True, text=True,
                         env=env, check=False, timeout=600)
    outputs = {}
    if gho.exists():
        for line in gho.read_text().splitlines():
            k, _, v = line.partition("=")
            outputs[k] = v
    return res, outputs, work, audit, out


def test_workflow_retains_audit_on_no_change(baseline_bundle, tmp_path):
    baseline, digest = baseline_bundle
    res, outputs, work, audit, _ = _run_step(tmp_path, baseline, digest)
    assert res.returncode == 0, res.stderr
    assert outputs["status"] == "no-change"
    assert outputs["selected_snapshot"] == "baseline"
    assert (audit / "refresh-audit.json").exists()
    a = json.loads((audit / "refresh-audit.json").read_text())
    assert a["status"] == "no-change"
    # No candidate bundle/production artifact was produced for no-change.
    assert not (work / "candidate/snapshot-bundle.zip").exists()


def test_workflow_retains_audit_on_changed(baseline_bundle, tmp_path):
    baseline, digest = baseline_bundle
    # A changed candidate (add a clear fixture row to Annex II) produces an out dir.
    fixture = tmp_path / "COSING_Annex_II_v2.txt"
    shutil.copy2(ROOT / "data/raw/cosing-annex-ii/COSING_Annex_II_v2.txt", fixture)
    with fixture.open("a", encoding="utf-8") as fh:
        fh.write('1768,"Fixture Test Substance, Annex II",0000-00-0,,'
                 '"(EC) 2009/1223",,,,,,01/01/2026\n')
    res, outputs, _work, audit, out = _run_step(tmp_path, baseline, digest, f"cosing-annex-ii={fixture}")
    assert res.returncode == 0, res.stderr
    assert outputs["status"] == "changed"
    assert outputs["selected_snapshot"] == "candidate"
    assert outputs["promotion_ready"] == "true"
    # The dedicated audit is retained.
    assert (audit / "refresh-audit.json").exists()
    a = json.loads((audit / "refresh-audit.json").read_text())
    assert a["status"] == "changed"
    # The candidate bundle lives in the out dir (gated upload path).
    assert (out / "snapshot-bundle.zip").exists()


def test_workflow_preserves_failure_and_retains_audit(baseline_bundle, tmp_path):
    baseline, _ = baseline_bundle
    # Bad digest -> refresh_isolated exits 2 (failure). The step body must
    # capture it, still retain the audit, and exit non-zero (job fails).
    res, outputs, work, audit, _ = _run_step(tmp_path, baseline, "0" * 64)
    assert res.returncode != 0
    # The refresh failure message is echoed to stderr; the workflow's ::error
    # (with the exit code) is echoed to stdout.
    assert "refresh failed" in res.stderr
    assert outputs.get("status") == "failure"
    assert outputs.get("promotion_ready") == "false"
    # The failure audit is retained even though the job failed.
    assert (audit / "refresh-audit.json").exists()
    a = json.loads((audit / "refresh-audit.json").read_text())
    assert a["status"] == "failure"
    assert a["promotion_ready"] is False
    assert "refresh failed" in a["message"]
    # No candidate bundle was produced on failure.
    assert not (work / "candidate/snapshot-bundle.zip").exists()


@pytest.mark.parametrize("missing", ["URL", "EXPECTED"])
def test_workflow_preflight_failure_still_writes_audit(baseline_bundle, tmp_path, missing):
    """A missing baseline URL/digest must exit non-zero AND still produce a
    reviewable audit before exiting, so the always-run upload gets a path."""
    baseline, digest = baseline_bundle
    work = tmp_path / "work"
    out = tmp_path / "out"
    audit = tmp_path / "refresh-audit"
    gho = tmp_path / "GITHUB_OUTPUT"
    env = {**_env(), "WORK": str(work), "OUT": str(out),
           "URL": (f"file://{baseline}" if missing == "EXPECTED" else ""),
           "EXPECTED": (digest if missing == "URL" else ""),
           "AUDIT": str(audit), "GITHUB_OUTPUT": str(gho),
           "PY": PY, "SCRIPT": str(SCRIPT)}
    res = subprocess.run(["bash", str(STEP)], capture_output=True, text=True,
                         env=env, check=False, timeout=60)
    # Preflight fails => the step itself exits 2 BEFORE running the refresh.
    assert res.returncode != 0
    outputs = {}
    if gho.exists():
        for line in gho.read_text().splitlines():
            k, _, v = line.partition("=")
            outputs[k] = v
    assert outputs.get("status") == "failure"
    assert outputs.get("promotion_ready") == "false"
    # A reviewable diagnostic audit is STILL produced (previously none existed).
    assert (audit / "refresh-audit.json").exists()
    a = json.loads((audit / "refresh-audit.json").read_text())
    assert a["status"] == "failure"
    assert a["promotion_ready"] is False
    assert "SNAPSHOT_BASELINE_" in a["message"]
    # No candidate artifact was produced.
    assert not (work / "candidate/snapshot-bundle.zip").exists()

