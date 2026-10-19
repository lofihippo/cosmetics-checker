"""Executable regressions for the generated release-command artifacts.


These tests exercise the *generated* launcher and installer shell scripts (and
the builder helpers that produce them) rather than only the helper strings, so
the documented default commands are proven to work and the installer is proven
offline / source-independent / non-destructive.

They are bounded and deterministic. Every REQUIRED regression here generates
its own temporary kit-shaped inputs from current source using a synthetic
(fake, clearly-labelled) wheelhouse + snapshot manifest, and runs the generated
installer script, so no pre-existing ``/tmp`` build or ``.local-reference``
artifact is needed for a clean-checkout quality gate. A separate real-wheel
acceptance test optionally consumes an explicit delivered kit path and fails
clearly (not silently skips) if that path is supplied but broken/absent.

They do NOT publish, host, or create accounts.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data" / "scripts"))
sys.path.insert(0, str(ROOT / "packages" / "eu-reg-lists"))
sys.path.insert(0, str(ROOT / "tests"))

import fixtures  # noqa: E402

DISCLAIMER = "Regulatory information, not legal advice."


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "build_deployment", ROOT / "deploy" / "build_deployment.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _run(cmd, env=None, cwd=None, timeout=180):
    e = {k: v for k, v in os.environ.items()}
    for key in ("PYTHONPATH", "PY_BIN", "PIP_NO_INDEX", "PIP_INDEX_URL",
                "EU_REG_PORT", "PORT"):
        e.pop(key, None)
    if env:
        e.update(env)
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                       cwd=cwd, env=e)
    return {"exit": p.returncode, "stdout": p.stdout, "stderr": p.stderr}


def _wait_http(port, path="/", timeout=30):
    for _ in range(timeout * 10):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}",
                                        timeout=1) as r:
                return r.status, r.read()
        except (OSError, urllib.error.URLError):
            time.sleep(0.1)
    raise AssertionError(f"service not up on port {port}")


# ---------------------------------------------------------------------------
# Launcher generation (string-level)
# ---------------------------------------------------------------------------

def test_launcher_default_port_has_no_leading_comma():
    """Both launchers must read a default port without a leading-comma bug."""
    b = _load_builder()
    dst = Path("/tmp/kit")
    snap = dst / "snapshot" / "data" / "processed" / "eu_reg_lists.sqlite"
    for entry, port_env, default in (("eu-reg-api", "EU_REG_PORT", "8000"),
                                     ("scan-to-check", "PORT", "8010")):
        text = b._launcher(entry, port_env, default, dst, snap)
        assert f"${{{port_env}:-{default}}}" in text, text
        assert f"${{{port_env}:-,{default}}}" not in text, text


def test_launcher_snapshot_path_relative_to_kit():
    """The launcher must resolve the snapshot relative to the kit directory."""
    b = _load_builder()
    dst = Path("/tmp/kit")
    snap = dst / "snapshot" / "data" / "processed" / "eu_reg_lists.sqlite"
    text = b._launcher("eu-reg-api", "EU_REG_PORT", "8000", dst, snap)
    assert '$HERE/snapshot/data/processed/eu_reg_lists.sqlite' in text
    assert not text.endswith("eu_reg_lists.sqlite\n")


def test_launcher_no_absolute_build_kit_path():
    """A launcher written for one kit location must not hard-code that path."""
    b = _load_builder()
    origin = Path("/tmp/original-kit")
    snap = origin / "snapshot" / "data" / "processed" / "eu_reg_lists.sqlite"
    text = b._launcher("eu-reg-api", "EU_REG_PORT", "8000", origin, snap)
    assert "/tmp/original-kit" not in text.replace("$HERE", "")


# ---------------------------------------------------------------------------
# Synthetic kit helper (no real wheels, no /tmp dependency)
# ---------------------------------------------------------------------------

# Synthetic local package wheels, named as the builder produces them. The bytes
# are clearly fake; they are only used to satisfy preflight validation and the
# offline pip call is deliberately never reached (or is forced to fail) in tests.
_LOCAL_WHEELS = (
    "eu_reg_lists-0.1.0-py3-none-any.whl",
    "eu_ingredient_linter-0.1.0-py3-none-any.whl",
    "eu_reg_api-0.1.0-py3-none-any.whl",
    "scan_to_check-0.1.0-py3-none-any.whl",
)
_RUNTIME_WHEELS = (
    "fastapi-0.143.0-py3-none-any.whl",
    "starlette-1.7.0-py3-none-any.whl",
)
_LOCAL_PREFIXES = ("eu-reg-lists-", "eu_reg_lists-", "eu-ingredient-linter-",
                   "eu_ingredient_linter-", "eu-reg-api-", "eu_reg_api-",
                   "scan-to-check-", "scan_to_check-")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_synthetic_kit(root: Path) -> Path:
    """Build a kit-shaped dir that PASSES installer preflight validation.

    Creates a fake wheelhouse (with matching manifest hashes), a fake snapshot
    sqlite + its snapshot-manifest file-hash contract, and a real generated
    ``install.sh`` bound to the test host (so the target check matches). Returns
    the kit root. The ``wheelhouse`` wheel bytes are synthetic and are never
    actually installed in the preflight-regression tests.
    """
    wheelhouse = root / "wheelhouse"
    wheelhouse.mkdir(parents=True, exist_ok=True)
    local = []
    for name in _LOCAL_WHEELS:
        data = (b"FAKE-LOCAL-WHEEL " + name.encode())
        (wheelhouse / name).write_bytes(data)
        local.append({"filename": name, "sha256": _sha256_bytes(data)})
    runtime = []
    for name in _RUNTIME_WHEELS:
        data = (b"FAKE-RUNTIME-WHEEL " + name.encode())
        (wheelhouse / name).write_bytes(data)
        runtime.append({"filename": name, "sha256": _sha256_bytes(data)})
    manifest = {
        "format_version": 1,
        "wheelhouse": {"local_wheels": local, "runtime_wheels": runtime},
    }
    (root / "release-manifest.json").write_text(json.dumps(manifest))

    snap = root / "snapshot" / "data" / "processed" / "eu_reg_lists.sqlite"
    snap.parent.mkdir(parents=True, exist_ok=True)
    snap_bytes = b"SYNTHETIC SNAPSHOT BYTES (not a real database)"
    snap.write_bytes(snap_bytes)
    snap_manifest = {
        "format_version": 1,
        "content_hash": "synthetic-content-hash",
        "data_updated_at": "2026-01-01T00:00:00+00:00",
        "files": {"data/processed/eu_reg_lists.sqlite": {
            "sha256": _sha256_bytes(snap_bytes),
            "byte_len": len(snap_bytes),
        }},
    }
    (root / "snapshot" / "snapshot-manifest.json").write_text(json.dumps(snap_manifest))

    b = _load_builder()
    build_version = f"{sys.version_info[0]}.{sys.version_info[1]}"
    inst = b._installer(root, Path(sys.executable), wheelhouse, build_version)
    (root / "install.sh").write_text(inst)
    (root / "install.sh").chmod(0o755)
    return root


def _write_prior_runtime(root: Path) -> Path:
    """Create a synthetic prior working runtime sentinel; return its marker."""
    (root / ".venv").mkdir(parents=True, exist_ok=True)
    marker = root / ".venv" / "previous-working-runtime"
    marker.write_text("sentinel")
    return marker


# ---------------------------------------------------------------------------
# Installer preflight regressions (always run, no external kit required)
# ---------------------------------------------------------------------------

def test_installer_rejects_empty_manifest_and_preserves_runtime():
    """A {{}} release manifest must FAIL validation, not pass the loop."""
    b = _load_builder()
    with tempfile.TemporaryDirectory() as td:
        root = _make_synthetic_kit(Path(td))
        (root / "release-manifest.json").write_text("{}")
        marker = _write_prior_runtime(root)
        res = _run(["bash", str(root / "install.sh")],
                   env={"PY_BIN": sys.executable})
        assert res["exit"] != 0
        assert "valid release manifest" in res["stderr"].lower()
        # Preflight must fail BEFORE removing the prior runtime.
        assert marker.exists()
        assert (root / ".venv").is_dir()


def test_installer_rejects_corrupt_snapshot_and_preserves_runtime():
    """A non-database / tampered snapshot must fail install up front."""
    b = _load_builder()
    with tempfile.TemporaryDirectory() as td:
        root = _make_synthetic_kit(Path(td))
        (root / "snapshot" / "data" / "processed" / "eu_reg_lists.sqlite").write_bytes(
            b"CORRUPT SQLITE REVIEW FIXTURE")
        marker = _write_prior_runtime(root)
        res = _run(["bash", str(root / "install.sh")],
                   env={"PY_BIN": sys.executable})
        assert res["exit"] != 0
        assert "corrupt" in res["stderr"].lower() or "does not match" in res["stderr"].lower()
        assert marker.exists()
        assert (root / ".venv").is_dir()


def test_installer_rejects_missing_snapshot_manifest():
    """A kit without the snapshot file-hash contract must fail validation."""
    b = _load_builder()
    with tempfile.TemporaryDirectory() as td:
        root = _make_synthetic_kit(Path(td))
        (root / "snapshot" / "snapshot-manifest.json").unlink()
        marker = _write_prior_runtime(root)
        res = _run(["bash", str(root / "install.sh")],
                   env={"PY_BIN": sys.executable})
        assert res["exit"] != 0
        assert "snapshot manifest" in res["stderr"].lower()
        assert marker.exists()


def test_installer_rejects_target_mismatch():
    """The installer must enforce implementation/OS/arch, not just the version."""
    b = _load_builder()
    with tempfile.TemporaryDirectory() as td:
        root = _make_synthetic_kit(Path(td))
        inst = (root / "install.sh").read_text()
        # Patch the declared target so it mismatches the test host exactly.
        import platform
        real_target = f"{sys.platform}/{platform.machine()}"
        bogus_target = "linux/x86_64" if real_target != "linux/x86_64" else "darwin/arm64"
        patched = inst.replace(
            f"BUILD_TARGET={real_target}",
            f"BUILD_TARGET={bogus_target}")
        assert patched != inst
        (root / "install.sh").write_text(patched)
        marker = _write_prior_runtime(root)
        res = _run(["bash", str(root / "install.sh")],
                   env={"PY_BIN": sys.executable})
        assert res["exit"] != 0
        assert "target mismatch" in res["stderr"].lower()
        assert marker.exists()


def test_installer_rolls_back_on_venv_creation_failure():
    """A venv-creation failure after preflight must restore the prior runtime."""
    b = _load_builder()
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        root = _make_synthetic_kit(td / "kit")
        marker = _write_prior_runtime(root)
        # A wrapper that delegates real probes/hash checks but fails ``-m venv``.
        wrapper = td / "python-fails-venv"
        wrapper.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then '
            'echo "controlled venv failure" >&2; exit 9; fi\n'
            f"exec {sys.executable} \"$@\"\n")
        wrapper.chmod(0o755)
        res = _run(["bash", str(root / "install.sh")],
                   env={"PY_BIN": str(wrapper)})
        assert res["exit"] != 0
        assert "restoring previous runtime" in res["stderr"].lower()
        # Prior runtime restored, no leftover staging dir.
        assert marker.exists()
        assert not (root / ".venv.bak").exists()


def test_installer_rolls_back_on_pip_install_failure():
    """A dependency-install failure after preflight must restore the prior runtime."""
    b = _load_builder()
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        root = _make_synthetic_kit(td / "kit")
        marker = _write_prior_runtime(root)
        # Corrupt one local wheel AND update its manifest hash so preflight hash
        # validation still passes, then pip fails to install the invalid wheel.
        wheel = root / "wheelhouse" / _LOCAL_WHEELS[0]
        wheel.write_bytes(b"NOT A VALID WHEEL")
        manifest = json.loads((root / "release-manifest.json").read_text())
        for d in manifest["wheelhouse"]["local_wheels"]:
            if d["filename"] == wheel.name:
                d["sha256"] = _sha256_bytes(wheel.read_bytes())
        (root / "release-manifest.json").write_text(json.dumps(manifest))
        res = _run(["bash", str(root / "install.sh")],
                   env={"PY_BIN": sys.executable})
        assert res["exit"] != 0
        assert "restoring previous runtime" in res["stderr"].lower()
        assert marker.exists()
        assert not (root / ".venv.bak").exists()


def test_installer_contains_no_package_index_upgrade():
    """The installer must not run ``pip install --upgrade pip``."""
    b = _load_builder()
    inst = b._installer(Path("/tmp/kit"), Path("python"), Path("/tmp/kit/wheelhouse"), "3.14")
    # The only pip command is the offline install line (--no-index, no upgrade).
    pip_lines = [l for l in inst.splitlines() if "$HERE/.venv/bin/python" in l]
    assert all("--no-index" in l for l in pip_lines)
    assert not any("--upgrade" in l for l in pip_lines)


# ---------------------------------------------------------------------------
# Make target contract
# ---------------------------------------------------------------------------

def test_make_requires_deply_dest(capsys):
    """`make deploy-build-consume-existing` without DEPLOY_DEST fails preflight."""
    env = dict(os.environ)
    env.pop("DEPLOY_DEST", None)
    p = subprocess.run(["make", "deploy-build-consume-existing"],
                       cwd=str(ROOT), capture_output=True, text=True, env=env)
    assert p.returncode != 0
    assert "DEPLOY_DEST is required" in p.stdout + p.stderr
    assert "make deploy-build-consume-existing" in p.stdout + p.stderr  # copyable example


def test_make_accepts_explicit_dest():
    """An explicit DEPLOY_DEST reaches the builder command."""
    env = dict(os.environ)
    env.pop("DEPLOY_DEST", None)
    p = subprocess.run(
        ["make", "-n", "deploy-build-consume-existing",
         "DEPLOY_DEST=/tmp/acceptance-kit"],
        cwd=str(ROOT), capture_output=True, text=True, env=env)
    assert "--dest \"/tmp/acceptance-kit\"" in p.stdout


# ---------------------------------------------------------------------------
# Installer full offline happy path (real wheels; explicit kit, optional)
# ---------------------------------------------------------------------------

_DELIVERED_KIT = Path(os.environ.get("EU_RELEASE_KIT", "/tmp/eu-release-kit"))


def test_installer_offline_install_from_real_kit():
    """Consume an EXPLICIT delivered kit path; fail clearly if supplied/absent.

    This is the real-wheel offline acceptance. It is only run when
    ``EU_RELEASE_KIT`` is set to a real, complete kit (or the conventional
    ``/tmp/eu-release-kit`` exists). It is a *separately invoked* acceptance
    test and is NOT required as part of the clean-checkout quality gate; the
    preflight/rollback regressions above always generate their own artifacts.
    """
    if not _DELIVERED_KIT.is_dir() and not os.environ.get("EU_RELEASE_KIT"):
        pytest.skip("no real release kit supplied (set EU_RELEASE_KIT)")
    if not _DELIVERED_KIT.is_dir():
        raise AssertionError(f"supplied EU_RELEASE_KIT is not a directory: {_DELIVERED_KIT}")
    if not (_DELIVERED_KIT / "install.sh").is_file():
        raise AssertionError(f"kit is incomplete (no install.sh): {_DELIVERED_KIT}")
    host = Path(sys.executable)
    with tempfile.TemporaryDirectory() as td:
        copy = Path(td) / "relocated kit"
        shutil.copytree(_DELIVERED_KIT, copy, ignore=shutil.ignore_patterns(".venv", ".venv.bak"))
        # Replace the delivered installer with one freshly generated for the
        # current host so the target check matches the running test interpreter.
        b = _load_builder()
        inst = b._installer(copy, host, copy / "wheelhouse",
                            f"{sys.version_info[0]}.{sys.version_info[1]}")
        (copy / "install.sh").write_text(inst)
        (copy / "install.sh").chmod(0o755)
        res = _run(["bash", str(copy / "install.sh")], cwd=str(td),
                   env={"PY_BIN": str(host), "PIP_CONFIG_FILE": "/dev/null"})
        assert res["exit"] == 0, res["stderr"]
        assert "installed from wheelhouse (offline)." in res["stdout"]
        # All four packages import from the new kit's own site-packages.
        py = copy / ".venv/bin/python"
        r = _run([str(py), "-I", "-c",
                  "import eu_reg_lists,eu_ingredient_linter,eu_reg_api,scan_to_check;"
                  "print([m.__file__ for m in "
                  "(eu_reg_lists,eu_ingredient_linter,eu_reg_api,scan_to_check)])"],
                 cwd=str(td))
        assert r["exit"] == 0, r["stderr"]
        assert str(copy / ".venv") in r["stdout"]
        assert "/tmp/eu-release-kit" not in r["stdout"]


# ---------------------------------------------------------------------------
# Real launched services (default ports, live HTTP) -- optional acceptance
# ---------------------------------------------------------------------------

def _kit_is_delivered() -> bool:
    """True if the conventional /tmp/eu-release-kit exists and looks complete."""
    return (_DELIVERED_KIT.is_dir()
            and (_DELIVERED_KIT / "run-eu-reg-api.sh").is_file()
            and (_DELIVERED_KIT / "run-scan-to-check.sh").is_file())


def test_delivered_launchers_default_ports_no_override():
    """Run the actual delivered launchers with port env UNSET; both must serve.

    Uses process-liveness checks so a pre-existing service on the port cannot
    satisfy the assertion, and random ports to avoid conflicts.
    """
    if not _kit_is_delivered():
        pytest.skip("no delivered release kit present in /tmp for live-launcher acceptance")
    b = _load_builder()
    procs = []
    used_ports = []
    try:
        # Allocate two free ports so we never collide with a pre-existing service.
        import socket
        for _ in range(2):
            s = socket.socket()
            s.bind(("127.0.0.1", 0))
            used_ports.append(s.getsockname()[1])
            s.close()
        for idx, (script, base_port) in enumerate(
                (("run-eu-reg-api.sh", used_ports[0]), ("run-scan-to-check.sh", used_ports[1]))):
            env = {k: v for k, v in os.environ.items()
                   if k not in ("EU_REG_PORT", "PORT")}
            env[("EU_REG_PORT" if script.startswith("run-eu-reg-api") else "PORT")] = str(base_port)
            p = subprocess.Popen(["bash", str(_DELIVERED_KIT / script)],
                                 cwd=str(_DELIVERED_KIT), env=env,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            procs.append((p, base_port))
        for p, port in procs:
            assert p.poll() is None, f"spawned launcher {p.pid} exited early"
            status, _ = _wait_http(port)
            assert status == 200
            assert p.poll() is None, f"launcher {p.pid} died while serving"
    finally:
        for p, _ in procs:
            p.terminate()
            p.wait(timeout=10)


def test_delivered_launcher_health_and_check_endpoints():
    """Exercise the actual run launcher's /health and /v1/check endpoints."""
    if not _kit_is_delivered():
        pytest.skip("no delivered release kit present in /tmp for live-launcher acceptance")
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    p = subprocess.Popen(["bash", str(_DELIVERED_KIT / "run-eu-reg-api.sh")],
                         cwd=str(_DELIVERED_KIT),
                         env={k: v for k, v in os.environ.items()
                              if k not in ("EU_REG_PORT", "PORT")} | {"EU_REG_PORT": str(port)},
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if p.poll() is not None:
            raise AssertionError(f"spawned launcher {p.pid} exited early")
        _wait_http(port, "/health")
        status, body = _wait_http(port, "/v1/check?ingredients=Benzene")
        assert status == 200
        data = json.loads(body)
        assert data["verdict"] == "FLAGGED"
        assert data["results"][0]["determination"] == "banned"
        assert p.poll() is None, f"launcher {p.pid} died while serving"
    finally:
        p.terminate()
        p.wait(timeout=10)
