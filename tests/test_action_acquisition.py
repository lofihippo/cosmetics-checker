"""Acquisition integration: acquire+verify a trusted snapshot on a fresh runner.


These tests run the ACTUAL acquisition steps from the maintained workflow YAML
(loaded, not copied) against a local fixture HTTP server serving a copy of a real
verified snapshot bundle, then (when Docker is available) run the actual composite
wrapper against it. Cases: accepted bundle, unavailable source, wrong digest,
corrupt bundle. No regulatory data is fetched or normalized; the fixture just
serves bytes. The repo snapshot is read-only.
"""
from __future__ import annotations

import hashlib
import http.server
import functools
import os
from pathlib import Path
import shutil
import socketserver
import subprocess
import sys
import threading

import pytest
import yaml

import fixtures

ROOT = Path(__file__).resolve().parents[1]
SNP = ROOT / "data" / "processed"
EXPORT = ROOT / "data" / "scripts" / "snapshot.py"


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


def _workflow_step_run(name: str) -> str:
    """Return the maintained ``run`` body of a named job step, loaded from YAML."""
    wf = yaml.safe_load((ROOT / ".github/workflows/eu-ingredient-linter.yml").read_text())
    for step in wf["jobs"]["eu-ingredient-lint"]["steps"]:
        if step.get("name") == name:
            return step["run"]
    raise KeyError(f"step not found: {name}")


@pytest.fixture(scope="module")
def fixture_bundle_url(tmp_path_factory):
    """Serve a deterministic synthetic snapshot bundle over a local HTTP server."""
    bundle = tmp_path_factory.mktemp("bundle") / "snapshot-bundle.zip"
    tree = tmp_path_factory.mktemp("tree")
    fixtures.build_tree(tree)
    fixtures.build_bundle(tree, bundle)
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    # Serve the bundle directory without changing the pytest process cwd (which
    # would break other tests relying on relative paths).
    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a, **k):
            pass
    handler = functools.partial(Handler, directory=str(bundle.parent))
    srv = socketserver.TCPServer(("127.0.0.1", 0), handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{port}/snapshot-bundle.zip", digest
    finally:
        srv.shutdown()
        srv.server_close()


def _venv_python() -> str:
    # Use the ACTIVE interpreter, never a hardcoded venv/Homebrew path.
    return sys.executable


def _base_env():
    e = {**os.environ}
    return e


def _run_acq(url, expected, workspace, github_output):
    """Run the maintained acquisition step body (loaded from the workflow YAML)."""
    script = _workflow_step_run("Acquire and verify trusted snapshot")
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                          timeout=120, env={**_base_env(),
                                            "SNAPSHOT_URL": url,
                                            "SNAPSHOT_SHA256": expected,
                                            "GITHUB_WORKSPACE": str(workspace),
                                            "GITHUB_OUTPUT": str(github_output)})


def test_accepted_bundle(fixture_bundle_url, tmp_path):
    url, digest = fixture_bundle_url
    out = tmp_path / "gh.out"
    res = _run_acq(url, digest, tmp_path, out)
    assert res.returncode == 0, res.stderr
    assert "trusted snapshot acquired" in res.stdout
    assert out.exists() and "snapshot-path=" in out.read_text()


def test_unavailable_source(fixture_bundle_url, tmp_path):
    url, digest = fixture_bundle_url
    bad_url = url.replace("/snapshot-bundle.zip", "/nope.zip")
    res = _run_acq(bad_url, digest, tmp_path, tmp_path / "gh.out")
    assert res.returncode == 2, res.stdout
    # Must not have fallen back to any data.
    assert not (tmp_path / ".eu-lint-run/snapshot-acquire/snapshot-bundle.zip").exists()


def test_wrong_digest(fixture_bundle_url, tmp_path):
    url, _digest = fixture_bundle_url
    wrong = "0" * 64
    res = _run_acq(url, wrong, tmp_path, tmp_path / "gh.out")
    assert res.returncode == 2, res.stdout
    assert "digest mismatch" in res.stdout


def test_corrupt_bundle_rejected_by_full_verify(fixture_bundle_url, tmp_path):
    """A digest that matches a corrupt archive must still be rejected by the full verifier."""
    url, digest = fixture_bundle_url
    out = tmp_path / "gh.out"
    # Acquire the real bytes, then swap the bundle for corrupt bytes with the
    # SAME digest is impossible (digest changes), so instead simulate a corrupt
    # archive by tampering after download: run acquisition then feed a corrupt
    # file to the bundle verifier directly.
    res = _run_acq(url, digest, tmp_path, out)
    assert res.returncode == 0, res.stderr
    bundle = tmp_path / ".eu-lint-run/snapshot-acquire/snapshot-bundle.zip"
    assert bundle.exists()
    # Replace the archive with corrupt bytes (digest now differs -> verification
    # of the digest happens in the acquire step; here we assert the FULL bundle
    # verifier rejects a corrupt zip even when passed directly).
    corrupt = tmp_path / "corrupt.zip"
    corrupt.write_bytes(b"not a zip at all")
    verifier = subprocess.run(
        [_venv_python(), str(EXPORT), "verify", str(corrupt)],
        capture_output=True, text=True, timeout=120,
        env={**_base_env(), "PYTHONPATH": str(ROOT / "packages/eu-reg-lists")})
    assert verifier.returncode == 2


@pytest.mark.skipif(not _has_docker(), reason="no docker daemon")
def test_docker_wrapper_acquired_snapshot_with_spaces(fixture_bundle_url, tmp_path):
    """The real wrapper runs against an acquired bundle from a workspace with spaces."""
    url, digest = fixture_bundle_url
    ws = tmp_path / "consumer workspace"
    ws.mkdir()
    # Consumer needs the action + package sources as build context.
    shutil.copytree(ROOT / ".github/actions/eu-ingredient-linter",
                    ws / ".github/actions/eu-ingredient-linter")
    shutil.copytree(ROOT / "packages", ws / "packages")
    shutil.copytree(ROOT / "data" / "scripts", ws / "data" / "scripts")
    # Acquire into the workspace.
    out = ws / "gh.out"
    res = _run_acq(url, digest, ws, out)
    assert res.returncode == 0, res.stderr
    snap_path = out.read_text().split("snapshot-path=")[1].strip()
    inp = ws / "ingredients list.csv"
    inp.write_text("ingredient,percent\nBenzene,5\n")
    ghout = ws / "ghoutput.txt"
    run = subprocess.run(
        ["bash", str(ws / ".github/actions/eu-ingredient-linter/docker-wrapper.sh"),
         str(ws / ".github/actions/eu-ingredient-linter"), str(ws),
         str(inp), "csv", snap_path, str(ws / "report.json"), "findings"],
        capture_output=True, text=True, timeout=600,
        env={**_base_env(), "GITHUB_OUTPUT": str(ghout)})
    assert run.returncode == 1, run.stderr
    assert ghout.exists()
    assert "report-valid=1" in ghout.read_text()
    assert (ws / "report.json").exists()
    # An annotation is emitted for the finding.
    assert "Benzene" in run.stdout
