"""Command-level regressions for the acceptance runner (examples/acceptance_demo.py).

Regulatory information, not legal advice.

These verify the two reproduced defects are fixed:
  * a required condition that fails (impossible timing target / wrong exit probe)
    makes the REAL command return a non-zero status (previously it returned 0);
  * the runner is independent of the gitignored ``.local-reference`` directory.

Each test drives the actual ``main()`` entry point (the real command) so the
process-status behaviour is asserted end to end, without issuing live provider
requests. Run from a checkout that has the data snapshot installed.
"""

import contextlib
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_demo():
    spec = importlib.util.spec_from_file_location(
        "acceptance_demo", ROOT / "examples" / "acceptance_demo.py")
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    return demo


def _run_main(demo, argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = demo.main(argv)
    return code, json.loads(buf.getvalue())


def test_failing_timing_condition_returns_nonzero(tmp_path):
    """Impossible timing targets must make the command exit non-zero."""
    demo = _load_demo()
    demo.PROTO_TARGET_SECONDS = -1
    demo.LINTER_TARGET_SECONDS = -1
    code, report = _run_main(demo, ["--out-dir", str(tmp_path / "ev")])
    assert code == 1
    assert report["acceptance_passed"] is False
    failed = {c["name"] for c in report["timing"]["required_conditions"] if not c["passed"]}
    assert "prototype_timing" in failed
    assert "linter_timing" in failed


def test_wrong_invalid_exit_probe_returns_nonzero(tmp_path):
    """A failing invalid-input exit probe must make the command exit non-zero."""
    demo = _load_demo()
    demo.verify_exit_behavior = lambda out_dir: {
        "invalid_input_exit_code": 0, "invalid_input_expected_2": False,
        "stderr_has_error": False}
    code, report = _run_main(demo, ["--verify-exit", "--out-dir", str(tmp_path / "ev")])
    assert code == 1
    assert report["acceptance_passed"] is False
    assert report["verify_exit_behavior"]["invalid_input_expected_2"] is False


def test_runner_does_not_depend_on_local_reference(tmp_path):
    """The runner must not require the gitignored ``.local-reference`` dir."""
    # Calling main() for the timing section must not touch .local-reference: it
    # writes its own inputs/reports into the caller-selected out-dir.
    demo = _load_demo()
    assert demo.SNAPSHOT.is_file(), "test needs the processed snapshot installed"
    code, report = _run_main(demo, ["--out-dir", str(tmp_path / "ev")])
    assert code == 0
    assert report["acceptance_passed"] is True
    # The retained cited reports are written to the caller-selected out-dir.
    for artifact in (report["timing"]["prototype"]["report_artifact"],
                     report["timing"]["linter"]["report_artifact"]):
        assert Path(artifact["path"]).exists()
        assert ".local-reference" not in Path(artifact["path"]).parts


def test_us_tag_requires_exact_match():
    """Country evidence requires an exact ``en:united-states`` string tag."""
    from pathlib import Path as _P
    demo = _load_demo()
    assert demo._is_us_tag("en:united-states") is True
    assert demo._is_us_tag("united-states") is False
    assert demo._is_us_tag("en:us") is False
    assert demo._is_us_tag(["en:united-states"]) is False
    assert demo._is_us_tag("En:United-States") is False
