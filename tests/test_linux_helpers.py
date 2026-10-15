"""Exercise helper failure behavior without requiring a Docker daemon."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def executable(path, body):
    path.write_text(body)
    path.chmod(0o755)


def test_linux_builder_preserves_existing_output(tmp_path):
    bundle = tmp_path / "snapshot with spaces.zip"
    bundle.write_bytes(b"fixture")
    out = tmp_path / "existing output"
    out.mkdir()
    sentinel = out / "keep.txt"
    sentinel.write_text("keep")
    env = dict(os.environ, RELEASE_KIT_BUNDLE=str(bundle), RELEASE_KIT_OUT=str(out))
    result = subprocess.run(["bash", str(ROOT / "deploy/linux/build_linux_kit.sh")],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 2
    assert sentinel.read_text() == "keep"
    assert "output already exists" in result.stderr


@pytest.mark.parametrize("bad_status, expected", [(False, 0), (True, 1)])
def test_tls_helper_propagates_assertion_failure_and_owns_cleanup(tmp_path, bad_status, expected):
    """Run the actual shell orchestration with explicit fake external transports."""
    kit = tmp_path / "kit with spaces"
    (kit / ".venv/bin").mkdir(parents=True)
    for name in ("run-eu-reg-api.sh", "run-scan-to-check.sh", ".venv/bin/python", "release-manifest.json"):
        (kit / name).write_text("fixture")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.jsonl"
    executable(bin_dir / "docker", f'''#!{sys.executable}
import json,os,sys
with open(os.environ["DOCKER_LOG"], "a") as f: f.write(json.dumps(sys.argv[1:])+"\\n")
sys.exit(0)
''')
    executable(bin_dir / "openssl", "#!/bin/sh\nexit 0\n")
    executable(bin_dir / "sleep", "#!/bin/sh\nexit 0\n")
    executable(bin_dir / "curl", f'''#!{sys.executable}
import json,os,pathlib,sys
args=sys.argv[1:]
out=pathlib.Path(args[args.index("-o")+1])
values={{"health.json":{{"content_hash":"fixture"}}, "benzene.json":{{"verdict":"FLAGGED"}}, "echa.json":{{"error":"coverage_unavailable"}}, "formula.json":{{"error":"invalid_formula"}}, "manifest.json":{{"icons":[{{"src":"test.png"}}]}}, "check.json":{{"verdict":"FLAGGED","not_included":["echa-svhc"]}}}}
out.write_text("CACHE_NAMESPACE" if out.name=="sw.js" else json.dumps(values[out.name]))
status="422" if out.name in ("echa.json","formula.json") else "200"
if os.environ["BAD_STATUS"]=="1" and out.name=="benzene.json": status="500"
print(status,end="")
''')
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", RELEASE_KIT=str(kit),
               DOCKER_LOG=str(log), BAD_STATUS="1" if bad_status else "0")
    result = subprocess.run(["bash", str(ROOT / "deploy/linux/validate_local_tls.sh")],
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == expected, result.stdout + result.stderr
    assert ("FAIL benzene" if bad_status else "PASS benzene") in result.stdout
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    created = [call[call.index("--name") + 1] for call in calls if "--name" in call]
    removals = [call for call in calls if call[:2] == ["rm", "-f"]]
    assert len(created) == 2
    assert len(removals) == 1
    assert set(removals[0][2:]) == set(created)
    assert all(name.startswith("cosmetics-") for name in created)
