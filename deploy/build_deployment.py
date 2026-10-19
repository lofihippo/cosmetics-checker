#!/usr/bin/env python3
"""Build a reproducible LOCAL release kit for the eu-reg services.


This script produces a self-contained, source-independent local release kit
directory that you can inspect, install and run locally. It does NOT publish,
deploy to a host, create accounts, or incur charges. Hosting is a separately
documented manual step (see ``docs`` notes and the generated runbook).

What it does
------------
1. Verifies a trusted snapshot bundle (``--snapshot-bundle``) with the full
   snapshot verifier, checks that it is **release-eligible** (no deferred ECHA
   data in the input tree), and imports it into the kit's ``snapshot/`` tree.
   The snapshot carries the verified CosIng Annex II–VI data + provenance.
   ECHA is deliberately NOT included (deferred); its absence is a coverage gap,
   never a clean result.
2. Builds **ordinary wheels** for the four maintained packages
   (``eu-reg-lists``, ``eu-ingredient-linter``, ``eu-reg-api``,
   ``scan-to-check``) and downloads their **exact pinned** runtime dependency
   wheels into a retained ``wheelhouse/``. No package is installed editable.
3. Creates a fresh virtualenv **at the kit's final installation location**
   (``<dest>/.venv``) and installs the local wheels + runtime dependencies from
   the retained wheelhouse with ``--no-index``, so installation is reproducible
   from the kit alone and does not require the original source tree or a network
   dependency resolution.
4. Writes launcher scripts for both services, a release manifest, license
   notices and a runbook.

Source independence is proven by installing/running the kit with the disposable
build checkout moved away and ``PYTHONPATH`` unset.

Usage::

    python3 deploy/build_deployment.py \
      --snapshot-bundle /path/to/verified-annex-bundle.zip \
      --dest /path/to/release-kit

``--dest`` must be a NEW, non-existent directory (never overlay a live checkout)
and must not be inside the live checkout. Both services bind to ``127.0.0.1``
(loopback only; there is no ``--host`` option because changing the bind address
is a hosting decision, not a packaging one).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DISCLAIMER = "Regulatory information, not legal advice."
PACKAGES = ("eu-reg-lists", "eu-ingredient-linter", "eu-reg-api", "scan-to-check")
LOOPBACK_HOST = "127.0.0.1"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run(cmd: list[str], cwd: Path) -> None:
    print(f"  $ {' '.join(shlex.quote(str(c)) for c in cmd)}")
    subprocess.run(cmd, cwd=cwd, check=True)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, data: dict) -> None:
    _write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def _py_of(venv: Path) -> Path:
    py = venv / "bin" / "python"
    if not py.exists():
        py = venv / "Scripts" / "python.exe"  # Windows
    if not py.exists():
        raise RuntimeError("could not locate venv python")
    return py


def _validate_host_python(py: Path) -> str | None:
    """Return an actionable error string or ``None`` if ``py`` is a supported host.

    The release kit builds and downloads platform/CPython-specific runtime
    wheels for the *build* interpreter and platform. A ``--python`` that points
    at a different interpreter or platform would make the venv unable to use the
    retained wheelhouse (or, worse, silently resolve/pull a different set). We
    therefore require a CPython at the same major.minor version and on the same
    OS/architecture as the build host, and reject anything else.
    """
    if not py.exists():
        return f"--python interpreter not found: {py}"
    if not os.access(py, os.X_OK):
        return f"--python interpreter is not executable: {py}"
    probe = """import platform, sys
impl = sys.implementation.name
plat = sys.platform
machine = platform.machine()
ver = (sys.version_info[0], sys.version_info[1])
print(f"{impl} {ver[0]}.{ver[1]} {plat} {machine}", end="")
"""
    expected = f"{sys.implementation.name} {sys.version_info[0]}.{sys.version_info[1]} {sys.platform} {platform.machine()}"
    try:
        out = subprocess.check_output([str(py), "-c", probe], timeout=30, text=True).strip()
    except subprocess.CalledProcessError as exc:
        return f"--python is not a working interpreter ({py}): {exc}"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"could not run --python ({py}): {exc}"
    if out != expected:
        return (
            f"--python is incompatible with the release wheelhouse: got {out!r}, "
            f"expected a host of {expected!r}. The retained runtime wheels are "
            f"built for the build interpreter/platform, so a different "
            f"CPython/OS/arch would not install offline. Pass a matching "
            f"CPython 3.{sys.version_info[1]} on {sys.platform}/{platform.machine()}, "
            f"or rebuild the kit with that interpreter.")
    return None


def _build_local_wheels(dest: Path, wheelhouse: Path) -> list[dict]:
    """Build ordinary wheels for the four packages into ``wheelhouse``.

    Returns the hashes of just the four local package wheels.
    """
    print("[build] building local wheels")
    _run([sys.executable, "-m", "pip", "wheel", "--no-deps",
          "--wheel-dir", str(wheelhouse)] + [str(ROOT / "packages" / p) for p in PACKAGES],
         cwd=ROOT)
    return _local_wheel_hashes(wheelhouse)


def _local_wheel_hashes(wheelhouse: Path) -> list[dict]:
    """Return hashes of the four local package wheels."""
    local_prefixes = {f"{p.replace('-', '_')}-" for p in PACKAGES}
    return sorted(
        (d for d in _hashes_of(wheelhouse)
         if any(d["filename"].startswith(n) for n in local_prefixes)),
        key=lambda d: d["filename"],
    )


def _runtime_wheel_hashes(wheelhouse: Path) -> list[dict]:
    """Return hashes of the third-party runtime wheels (excludes the local ones)."""
    local = _local_wheel_hashes(wheelhouse)
    local_names = {d["filename"] for d in local}
    return sorted(
        (d for d in _hashes_of(wheelhouse) if d["filename"] not in local_names),
        key=lambda d: d["filename"],
    )


def _download_runtime_wheels(dest: Path, wheelhouse: Path, lock: Path) -> list[dict]:
    """Download the pinned runtime wheels into ``wheelhouse``.

    This is the only network step and it consumes the exact pinned versions from
    ``deploy/runtime.lock``. The resulting wheelhouse is retained in the kit so
    a later install is fully offline (``--no-index``).
    """
    print("[build] downloading pinned runtime wheels")
    if not lock.exists():
        raise FileNotFoundError(f"runtime lock not found: {lock}")
    _run([sys.executable, "-m", "pip", "download", "--dest", str(wheelhouse),
          "-r", str(lock)], cwd=ROOT)
    return _runtime_wheel_hashes(wheelhouse)


def _hashes_of(directory: Path) -> list[dict]:
    out = []
    for p in sorted(directory.glob("*.whl")):
        out.append({"filename": p.name, "sha256": _sha256_file(p)})
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-bundle", type=Path, required=True,
                        help="trusted, verified, release-eligible snapshot bundle (.zip)")
    parser.add_argument("--dest", type=Path, required=True,
                        help="NEW release-kit directory (must not already exist)")
    parser.add_argument("--python", default=sys.executable,
                        help="host CPython (>=3.14) used to create the venv (default: current)")
    args = parser.parse_args(argv)

    bundle = args.snapshot_bundle.resolve()
    dest = args.dest.resolve()
    if not bundle.exists():
        print(f"error: snapshot bundle not found: {bundle}", file=sys.stderr)
        return 2
    if dest.exists():
        print(f"error: destination already exists (choose a new dir): {dest}",
              file=sys.stderr)
        return 2
    if dest == ROOT or ROOT in dest.parents:
        print("error: destination must not be the live checkout or inside it",
              file=sys.stderr)
        return 2
    verr = _validate_host_python(Path(args.python))
    if verr:
        print(f"error: {verr}", file=sys.stderr)
        return 2

    dest.mkdir(parents=True, exist_ok=False)

    try:
        return _build_release(args, bundle, dest)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        # Never leave a success-shaped, half-built release kit behind on a
        # failure: an expected build/verification/eligibility failure is
        # surfaced as an error and the destination is removed so a caller
        # cannot mistake it for a valid kit.
        shutil.rmtree(dest, ignore_errors=True)
        print(f"error: release kit build failed: {exc}", file=sys.stderr)
        return 2


def _build_release(args: argparse.Namespace, bundle: Path, dest: Path) -> int:
    """Perform the kit build into ``dest`` (already created); returns 0 on success.

    Raises on any expected failure so ``main`` can remove a partial kit.
    """

    # --- 1. verify + import snapshot; enforce release eligibility -----------
    print("[1/6] verify + import snapshot bundle into release kit")
    data_import = dest / "snapshot"
    _run([sys.executable, str(ROOT / "data" / "scripts" / "snapshot.py"),
          "verify", str(bundle)], cwd=ROOT)
    _run([sys.executable, str(ROOT / "data" / "scripts" / "snapshot.py"),
          "import", str(bundle), "--destination", str(data_import)], cwd=ROOT)
    meta_path = data_import / "data" / "processed" / "metadata.json"
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    content_hash = metadata.get("content_hash")
    data_updated_at = metadata.get("data_updated_at")
    # Derive effective coverage + ECHA deferral from the imported snapshot.
    snapshot_sqlite = data_import / "data" / "processed" / "eu_reg_lists.sqlite"
    if not snapshot_sqlite.exists():
        raise ValueError(f"imported snapshot missing {snapshot_sqlite}")
    sys.path.insert(0, str(ROOT / "packages" / "eu-reg-lists"))
    from eu_reg_lists.release_policy import (  # noqa: E402
        coverage_dict, release_blockers, deferred_with_raw_data_in_tree,
    )
    cov = coverage_dict(metadata.get("verified_sources") or {})
    echa_deferred = cov["not_included"]
    # Release eligibility is separate from integrity: a bundle that verifies but
    # carries deferred (ECHA) data must be rejected for distribution. This is a
    # second, explicit boundary check (the snapshot export guard enforces the
    # same policy; a pre-existing or hand-built bundle could bypass it).
    blockers = release_blockers(metadata.get("verified_sources") or {})
    raw_blockers = deferred_with_raw_data_in_tree(data_import)
    all_blockers = sorted(set(blockers) | set(raw_blockers))
    if all_blockers:
        raise ValueError(
            "snapshot bundle is not release-eligible: deferred (ECHA) source(s) "
            f"present: {', '.join(all_blockers)}. ECHA is deliberately excluded "
            "from this release; build and verify an explicit CosIng-only release "
            "copy instead.")

    # --- 2. retained wheelhouse (local wheels + pinned runtime) ------------
    print("[2/6] build release wheelhouse (ordinary wheels + pinned runtime)")
    wheelhouse = dest / "wheelhouse"
    wheelhouse.mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "deploy" / "runtime.lock", dest / "runtime.lock")
    local_wheel_hashes = _build_local_wheels(dest, wheelhouse)
    runtime_wheel_hashes = _download_runtime_wheels(dest, wheelhouse, dest / "runtime.lock")

    # --- 3. create venv at final location and install from wheelhouse ------
    print(f"[3/6] create virtualenv at {dest / '.venv'}")
    env = dest / ".venv"
    _run([str(Path(args.python)), "-m", "venv", str(env)], cwd=ROOT)
    py = _py_of(env)
    # NOTE: no ``pip install --upgrade pip``. The venv's bundled pip installs the
    # retained wheels with --no-index; every installation dependency is a local
    # artifact. Build-time dependency downloads (wheel building, runtime pins)
    # are a separate, documented network step and never happen at install time.
    _run([str(py), "-m", "pip", "install", "--quiet", "--no-index",
          "--find-links", str(wheelhouse)] + list(PACKAGES), cwd=ROOT)

    # --- 4. launchers + release manifest -----------------------------------
    print("[4/6] write launchers + release manifest")
    _write_text(dest / "run-eu-reg-api.sh",
                _launcher("eu-reg-api", "EU_REG_PORT", "8000", dest, snapshot_sqlite))
    _write_text(dest / "run-scan-to-check.sh",
                _launcher("scan-to-check", "PORT", "8010", dest, snapshot_sqlite))
    for f in ("run-eu-reg-api.sh", "run-scan-to-check.sh"):
        os.chmod(dest / f, 0o755)

    manifest = {
        "disclaimer": DISCLAIMER,
        "format_version": 1,
        "built_at_utc": _now_iso(),
        "target_platform": f"{sys.platform}/{platform.machine()}",
        "interpreter": f"{sys.implementation.name} {sys.version.split()[0]}",
        "host_python": str(Path(args.python)),
        "bind_host": LOOPBACK_HOST,
        "snapshot": {
            "bundle": str(bundle),
            "bundle_sha256": _sha256_file(bundle),
            "content_hash": content_hash,
            "data_updated_at": data_updated_at,
            "release_eligible": True,
            "source_kinds_available": cov["available"],
            "source_kinds_missing": cov["missing"],
            "source_kinds_not_included": echa_deferred,
        },
        "echa_not_included": True,
        "deferred_sources": echa_deferred,
        "services": {
            "eu-reg-api": {"script": "run-eu-reg-api.sh", "port": 8000,
                           "bind": LOOPBACK_HOST,
                           "description": "label checker + playground"},
            "scan-to-check": {"script": "run-scan-to-check.sh", "port": 8010,
                              "bind": LOOPBACK_HOST,
                              "description": "barcode -> ingredients -> checker + PWA"},
        },
        "wheelhouse": {
            "local_wheels": local_wheel_hashes,
            "runtime_wheels": runtime_wheel_hashes,
        },
        "runtime_requirements_lock": "runtime.lock",
        "note": ("ECHA lists are deliberately not included (deferred). Absence is "
                 "a coverage gap, never a clean result. The kit installs from the "
                 "retained wheelhouse (no source tree, no network dependency "
                 "re-resolution at install time)."),
    }
    _write_json(dest / "release-manifest.json", manifest)

    # --- 5. license notices -------------------------------------------------
    print("[5/6] copy license notices")
    _write_text(dest / "LICENSE", (ROOT / "LICENSE").read_text(encoding="utf-8"))
    _write_text(dest / "LICENSES.md", (ROOT / "LICENSES.md").read_text(encoding="utf-8"))

    # --- 6. runbook + installer --------------------------------------------
    print("[6/6] write runbook + installer")
    _write_text(dest / "install.sh",
                _installer(dest, Path(args.python), wheelhouse,
                           f"{sys.version_info[0]}.{sys.version_info[1]}"))
    os.chmod(dest / "install.sh", 0o755)
    _write_text(dest / "README.md", _runbook_text(dest, content_hash, data_updated_at,
                                                  echa_deferred, coverage_dict(metadata.get("verified_sources") or {})))
    deps = subprocess.check_output([str(py), "-m", "pip", "list", "--format=freeze"],
                                   cwd=dest, text=True)
    (dest / "runtime-pip-freeze.txt").write_text(deps, encoding="utf-8")

    print("\nrelease kit built at", dest)
    print("  eu-reg-api      :", dest / "run-eu-reg-api.sh")
    print("  scan-to-check   :", dest / "run-scan-to-check.sh")
    print("  install offline :", dest / "install.sh")
    print("\nRegulatory information, not legal advice.")
    return 0


def _launcher(entry: str, port_env: str, default_port: str,
              dest: Path, snapshot_sqlite: Path) -> str:
    """Generate a launcher shell script without treating paths as shell code.

    The snapshot is resolved relative to the kit's install directory (``$HERE``,
    the launcher's own directory = ``dest``) so the script still works after the
    kit is copied to a new final location. The venv is created at that location
    by the installer, so no path points back to the original build kit.
    """
    rel = os.path.relpath(snapshot_sqlite, start=dest)
    quoted_snapshot = shlex.quote(rel)
    return f"""#!/usr/bin/env bash
# Start the {entry} service locally (binds to {LOOPBACK_HOST}).
# Regulatory information, not legal advice.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
export EU_REG_SNAPSHOT="$HERE/{quoted_snapshot}"
export {port_env}="${{{port_env}:-{default_port}}}"
cd "$HERE"
exec ".venv/bin/{entry}"
"""


def _installer(dest: Path, py_bin: Path, wheelhouse: Path,
               build_version: str = f"{sys.version_info[0]}.{sys.version_info[1]}") -> str:
    """Write a reusable offline installer that recreates the venv from wheels.

    The venv is created at the kit's final location (``.venv``) and installs
    ONLY from the retained ``wheelhouse`` with ``--no-index``. It does not need
    the original source tree and does not re-resolve dependencies over the
    network.

    The installer resolves a supported host CPython at install time, in this
    order: (1) ``PY_BIN`` env var (documented caller override); (2) ``python3``
    on ``PATH`` (a discoverable supported host CPython). It never depends on the
    build checkout's interpreter being present, and never uses the kit's own
    venv python (which it deletes and recreates).

    Required kit inputs are validated BEFORE the existing ``.venv`` is touched:
    manifest structure (non-empty wheelhouse with all expected artifacts), the
    retained snapshot file-hash/integrity contract, and the resolved host
    interpreter's implementation / major.minor / OS / architecture (not just the
    version). Any preflight failure returns before the runtime is modified.

    The runtime is then replaced in place (``.venv`` -> ``.venv.bak``, fresh
    ``.venv`` built at the FINAL location so absolute entry-point paths stay
    correct, installed, then ``.venv.bak`` removed). If venv creation or the
    offline install fails, the fresh partial ``.venv`` is discarded and the
    prior ``.venv.bak`` is restored, so a failed replacement never destroys a
    working runtime.
    """
    impl = sys.implementation.name
    target = f"{sys.platform}/{platform.machine()}"
    return f"""#!/usr/bin/env bash
# Recreate the kit virtualenv from the retained wheelhouse (fully offline).
# Regulatory information, not legal advice.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
WHEELHOUSE="$HERE/wheelhouse"
MANIFEST="$HERE/release-manifest.json"
SNAPSHOT_MANIFEST="$HERE/snapshot/snapshot-manifest.json"
SNAPSHOT="$HERE/snapshot/data/processed/eu_reg_lists.sqlite"
# The build host's interpreter is recorded only as a last-resort hint; at
# install time we prefer an explicit PY_BIN override, then a discoverable
# python3. We NEVER depend on the original build checkout being present.
BUILD_PY={shlex.quote(str(py_bin))}
# This kit's declared target (from the build host that produced the retained
# wheels). The installer enforces the SAME implementation / major.minor /
# OS / architecture, not just a version prefix.
BUILD_IMPL={shlex.quote(impl)}
BUILD_VERSION={shlex.quote(build_version)}
BUILD_TARGET={shlex.quote(target)}

# ---- preflight: validate required kit inputs BEFORE touching the runtime ----
if [ ! -d "$WHEELHOUSE" ]; then
  echo "error: wheelhouse missing at $WHEELHOUSE" >&2
  echo "       the kit is incomplete; re-run the builder" >&2
  exit 2
fi
if [ ! -f "$MANIFEST" ]; then
  echo "error: release manifest missing at $MANIFEST" >&2
  exit 2
fi
if [ ! -f "$SNAPSHOT" ]; then
  echo "error: snapshot database missing at $SNAPSHOT" >&2
  exit 2
fi
if [ ! -f "$SNAPSHOT_MANIFEST" ]; then
  echo "error: snapshot manifest missing at $SNAPSHOT_MANIFEST (cannot verify snapshot integrity)" >&2
  exit 2
fi

# Resolve a supported host CPython, in priority order:
#   1. PY_BIN (a documented caller override)
#   2. python3 on PATH (a discoverable supported host CPython)
#   3. BUILD_PY (the build host interpreter, recorded only as a last-resort
#      hint; never required, and only used if it is still present and runnable).
PY_BIN="${{PY_BIN:-}}"
if [ -z "$PY_BIN" ] && command -v python3 >/dev/null 2>&1; then
  PY_BIN="$(command -v python3)"
fi
if [ -z "$PY_BIN" ] && [ -n "${{BUILD_PY:-}}" ] && [ -f "$BUILD_PY" ] && [ -x "$BUILD_PY" ]; then
  PY_BIN="$BUILD_PY"
fi
if [ -z "$PY_BIN" ]; then
  echo "error: no supported host CPython found. Set PY_BIN to a CPython {build_version}." >&2
  exit 2
fi

# ---- consolidated preflight + target validation (all-or-nothing) ----------
# This python probe validates EVERYTHING before the existing runtime is touched:
#   * the resolved PY_BIN is a runnable CPython of the same implementation,
#     major.minor, OS and architecture as the build host (not just the version);
#   * release-manifest.json is a well-formed release manifest with a NON-EMPTY,
#     complete wheelhouse (a {{}} or truncated manifest is a hard failure);
#   * every retained wheel artifact matches its recorded sha256;
#   * the snapshot is intact against its retained snapshot-manifest file-hash
#     contract (so a non-database / tampered snapshot is rejected up front).
# It exits non-zero on ANY mismatch, and nothing is modified until it passes.
"$PY_BIN" - "$MANIFEST" "$WHEELHOUSE" "$SNAPSHOT_MANIFEST" "$SNAPSHOT" \
  "$BUILD_IMPL" "$BUILD_VERSION" "$BUILD_TARGET" <<'PYEOF'
import hashlib, json, pathlib, platform, sys
manifest_path, wheelhouse, snap_manifest_path, snap_path = (
    pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]),
    pathlib.Path(sys.argv[3]), pathlib.Path(sys.argv[4]))
expected_impl, expected_version, expected_target = sys.argv[5], sys.argv[6], sys.argv[7]

bad = []

# --- host interpreter target: implementation / version / OS / arch ---------
impl = sys.implementation.name
ver = f"{{sys.version_info[0]}}.{{sys.version_info[1]}}"
plat = sys.platform
machine = platform.machine()
target = f"{{plat}}/{{machine}}"
if impl != expected_impl:
    bad.append(f"unsupported interpreter implementation {{impl!r}}; expected {{expected_impl!r}}")
if ver != expected_version:
    bad.append(f"unsupported interpreter version {{ver}}; this kit requires CPython {{expected_version}}")
if target != expected_target:
    bad.append(f"target mismatch: got {{target!r}}; this kit was built for {{expected_target!r}}")

# --- manifest structure -----------------------------------------------------
expected_artifacts = []
try:
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
except Exception as exc:
    bad.append(f"release-manifest.json is not valid JSON: {{exc}}")
    manifest = None
if manifest is None:
    bad.append("release-manifest.json is not a readable release manifest")
elif not isinstance(manifest, dict) or not manifest:
    # An EMPTY ({{}}) or non-dict manifest is an alpha/truncated release, NOT a
    # valid one; never accept it as successful validation.
    bad.append("release-manifest.json is empty or not an object; a valid release manifest is required")
elif manifest.get('format_version') != 1:
    bad.append("release-manifest.json is not a release manifest (format_version != 1)")
elif not isinstance(manifest.get('wheelhouse'), dict):
    bad.append("release-manifest.json is missing a wheelhouse section")
else:
    wh = manifest.get('wheelhouse')
    for kind in ('local_wheels', 'runtime_wheels'):
        items = wh.get(kind)
        if not isinstance(items, list) or not items:
            bad.append(f"wheelhouse.{{kind}} is empty or missing (an empty manifest is not a valid release)")
        for item in items or []:
            if not isinstance(item, dict) or not item.get('filename') or not item.get('sha256'):
                bad.append(f"wheelhouse.{{kind}} has a malformed artifact entry: {{item!r}}")
            else:
                expected_artifacts.append(item)

# --- retained wheel hashes --------------------------------------------------
if expected_artifacts:
    local_expected = 0
    seen = set()
    for item in expected_artifacts:
        name = item['filename']
        f = wheelhouse / name
        if name in seen:
            bad.append('duplicate wheel entry: ' + name)
        seen.add(name)
        if not f.exists():
            bad.append('missing wheel: ' + name)
        elif hashlib.sha256(f.read_bytes()).hexdigest() != item['sha256']:
            bad.append('hash mismatch: ' + name)
        if name.startswith(('eu-reg-lists-', 'eu_reg_lists-', 'eu-ingredient-linter-',
                            'eu_ingredient_linter-', 'eu-reg-api-', 'eu_reg_api-',
                            'scan-to-check-', 'scan_to_check-')):
            local_expected += 1
    # The recorded wheelhouse must not be satisfiable by an EMPTY directory;
    # at least the local package wheels must be present (pip would otherwise
    # fail offline later).
    if not local_expected:
        bad.append('wheelhouse has no local package wheels (offline install would fail)')

# --- snapshot integrity against the retained snapshot-manifest contract -----
try:
    snap_manifest = json.loads(snap_manifest_path.read_text(encoding='utf-8'))
except Exception as exc:
    bad.append(f"snapshot-manifest.json is not valid JSON: {{exc}}")
    snap_manifest = {{}}
if snap_manifest:
    files = snap_manifest.get('files')
    if not isinstance(files, dict):
        bad.append('snapshot-manifest.json has no files section')
        files = {{}}
    entry = files.get('data/processed/eu_reg_lists.sqlite')
    if not isinstance(entry, dict) or not entry.get('sha256'):
        bad.append('snapshot-manifest.json does not record the snapshot database hash')
    else:
        data = snap_path.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry['sha256']:
            bad.append('snapshot database is corrupt or tampered (hash does not match snapshot-manifest.json)')
        elif len(data) != entry.get('byte_len'):
            bad.append('snapshot database size does not match snapshot-manifest.json')

if bad:
    print('\\n'.join(bad), file=sys.stderr)
    sys.exit(1)
PYEOF

# ---- all preflight checks passed; now replace the runtime in place ---------
# Back up the prior runtime BEFORE touching it so a failed replacement can be
# rolled back. The fresh venv is built at the FINAL location (.venv) so its
# absolute entry-point paths stay correct; on failure the partial .venv is
# discarded and the prior .venv.bak is restored.
rollback() {{
  echo "install failed; restoring previous runtime." >&2
  rm -rf "$HERE/.venv"
  if [ -d "$HERE/.venv.bak" ]; then
    mv "$HERE/.venv.bak" "$HERE/.venv"
  fi
}}
if [ -d "$HERE/.venv" ]; then
  rm -rf "$HERE/.venv.bak"
  mv "$HERE/.venv" "$HERE/.venv.bak"
fi
if ! "$PY_BIN" -m venv "$HERE/.venv"; then
  rollback
  exit 2
fi
# Use the venv's bundled pip; there is deliberately NO ``--upgrade pip`` and no
# package-index access. Every dependency is a retained local wheel.
if ! "$HERE/.venv/bin/python" -m pip install --quiet --no-index \\
  --find-links "$WHEELHOUSE" eu-reg-lists eu-ingredient-linter eu-reg-api scan-to-check; then
  rollback
  exit 2
fi
rm -rf "$HERE/.venv.bak"
echo "installed from wheelhouse (offline)."
"""


def _runbook_text(dest: Path, content_hash: str | None, data_updated_at: str | None,
                  echa_deferred: list, cov: dict) -> str:
    avail = ", ".join(cov.get("available", [])) or "(none)"
    missing = ", ".join(cov.get("missing", [])) or "(none)"
    excluded = ", ".join(echa_deferred) or "(none)"
    return f"""# Local release kit runbook

Regulatory information, not legal advice.

This is a local, self-contained, source-independent release kit of the
**eu-reg-api** and **scan-to-check** services over the supplied verified
snapshot. Exact coverage is listed below. It is for inspection and local validation only — it is **not**
published, hosted or deployed.

## What's here

- `snapshot/` — the verified, **release-eligible** snapshot tree (source data +
  provenance) imported from the trusted bundle.
  `snapshot/data/processed/eu_reg_lists.sqlite` is the snapshot the services
  read.
- `wheelhouse/` — **ordinary wheels** for the four maintained packages plus the
  **exact pinned** runtime dependency wheels (see `runtime.lock`). Installation
  uses `--no-index` from this retained wheelhouse, so no source tree and no
  network dependency re-resolution are required.
- `.venv/` — the runtime environment created AT THIS LOCATION (it is not a
  relocatable pre-made venv) and installed non-editably from the wheelhouse.
- `run-eu-reg-api.sh` — start the label checker + playground (default port 8000,
  loopback only).
- `run-scan-to-check.sh` — start the barcode -> ingredients -> checker + PWA
  (default port 8010, loopback only).
- `install.sh` — recreate `.venv` from the retained wheelhouse (offline).
- `release-manifest.json` — build provenance: snapshot bundle SHA-256, snapshot
  content hash, wheel artifact hashes, services, ECHA deferral.
- `runtime-pip-freeze.txt` — the exact installed runtime dependency versions.
- `LICENSE`, `LICENSES.md` — code and data-source license notices.

## Supported platform / interpreter

This kit was built and installed on **`{dest}`** using
**`{sys.implementation.name} {sys.version.split()[0]}`** on platform
**`{sys.platform}/{platform.machine()}`**. The retained runtime wheels
(especially platform-specific `uvloop`/`websockets`/`httptools`) are bound to
exactly that platform + CPython, so reinstallation requires a host CPython of
the **same major.minor version on the same OS/architecture**. The installer
validates the resolved host interpreter and rejects anything else (it does not
silently resolve/build for a different interpreter). Rebuilding for another
OS/arch/Python requires a fresh build; the already-created `.venv` is bound to
this exact location and must not be copied to another machine.

## Run locally

```bash
cd <release-kit-dir>

# Service 1: label checker + browser playground (loopback)
./run-eu-reg-api.sh          # http://{LOOPBACK_HOST}:8000/

# Service 2: barcode -> ingredients -> checker + PWA (loopback)
./run-scan-to-check.sh       # http://{LOOPBACK_HOST}:8010/
```

Both read the same snapshot (`EU_REG_SNAPSHOT`) and are independent processes.
Override the default port with `EU_REG_PORT` / `PORT` as documented in the
launchers. The services bind only to `{LOOPBACK_HOST}`; changing the bind
address is a hosting decision, not a packaging one.

## Reproduce / reinstall from the kit (no source tree)

The kit installs entirely from the retained wheelhouse, fully offline. Copy the
kit to a new final location (without its prebuilt `.venv`), then run the
installer there:

```bash
cd /path/to/new-release-kit

# Recreate .venv from the retained wheelhouse. The interpreter is resolved at
# install time: PY_BIN (if set) then a supported CPython 3.x on PATH. All
# required kit inputs and wheel hashes are validated before the existing .venv
# is replaced, so a failure never destroys a working runtime. Every dependency
# is an on-disk retained wheel (--no-index); there is no package-index access.
./install.sh
```

To build a kit (not required once delivered, and it needs the source tree and a
build-time network step for the pinned wheels):

```bash
python3 deploy/build_deployment.py \\
  --snapshot-bundle /path/to/verified-annex-bundle.zip \\
  --dest /path/to/new-release-kit
```

The builder verifies the bundle with the full snapshot verifier AND enforces
the release boundary (no deferred ECHA data) before importing. A corrupt,
tampered **or release-ineligible** bundle fails clearly rather than starting
from zero coverage or distributing out-of-scope data. Build-time wheel
building/download is a separate, documented network step; installation never
touches the network. See `release-manifest.json` for the exact snapshot bundle
SHA-256, wheel hashes and target platform.

## Snapshot identity

- content hash: `{content_hash}`
- data_updated_at: `{data_updated_at}`
- release-eligible: yes

## Coverage honesty

Coverage is determined by this kit's snapshot, including entry 70 only when its
accepted source mapping is present. This is not complete EU regulatory coverage.

- available: {avail}
- missing:   {missing}
- not-included (deliberately deferred): {excluded}

**ECHA (SVHC / Annex XIV / Annex XVII) is deliberately NOT included** in this
release (acquisition is disabled / deferred). Its absence is reported by the
services as a coverage gap / "not included" — **never** as a clean result. See
`LICENSES.md`.

Product lookup and ingredient checking still require the appropriate running
services and, for the barcode->product flow, a network/Open Beauty Facts
provider. This is **not** an offline checker.

## Hosting decisions (NOT done here)

This package is intentionally local-only. Before any hosted deployment you must
decide and confirm, off-repo:

1. **Host provider & region** and whether the data (CosIng EU-reuse + ODbL
   product data) may be redistributed there under its license/attribution terms.
2. **Ports / TLS / reverse proxy** for both services, and a stable domain.
3. **Snapshot delivery** — how the verified snapshot bundle is fetched and its
   SHA-256 pinned (see `release-manifest.json` in this kit for the source bundle
   hash and `snapshot/data/processed/metadata.json` for the snapshot identity),
   including a refresh cadence.
4. **Auth/scaling** — this release binds to loopback with no auth. Public
   hosting requires an access-control decision (the V1 scope has no accounts;
   rate limits remain backlog).
5. **Cost & accounts** — public hosting creates provider charges/accounts; NOT
   done here.

Regulatory information, not legal advice.
"""


if __name__ == "__main__":
    sys.exit(main())
