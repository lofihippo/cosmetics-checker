#!/usr/bin/env python3
"""Reproducible acceptance/demo for cosmetics-checker (original V1 goals).

Regulatory information, not legal advice.

This is the reproducibility layer for the original-project acceptance evidence.
It runs the ACTUAL installed entry points as subprocesses with an explicit
environment (no special PYTHONPATH) and, where appropriate, outside the source
checkout. It captures the full cited output, verifies counts / citation /
provenance and exit behavior, and records commands, input hashes, snapshot and
timing.

Two independent sections, always labelled:

  1) TIMING (offline, deterministic) -- reproduces the real contracts:
       - Phase-0 prototype `eu_lint.py` (source entry point): a representative
         20-ingredient label, `--format json`, must run < 2s and exit 1 (flags).
       - Installed `eu-ingredient-linter` CLI (``python -m eu_ingredient_linter``)
         with a 1,000-ingredient batch file, `--format json`, must run < 30s and
         exit 1 (flags) with a valid full cited JSON report.
     Correctness, citation and provenance are verified from the captured output
     before the timing target is claimed. An expected findings exit 1 is NOT an
     operational failure; an operational failure (wrong counts, missing citation
     fields, failed process, timeout) is. A ``--verify-exit`` sub-benchmark proves
     a deliberately INVALID input yields the documented non-zero exit code.

  2) LIVE PRODUCT DEMONSTRATION (network) OR a RECORDED REPLAY. For each barcode
     it calls the existing ``scan_to_check.obf.OpenBeautyFactsClient``, records
     the typed outcome, name/brand, source URL, the provider retrieval time, the
     real ingredient text and the provider-reported country evidence
     (``countries_tags``), then runs the SHARED checker
     ``eu_ingredient_linter.ingredient_report`` and retains the FULL per-ingredient
     report. A US-market assessment is emitted only from an exact ``en:united-states``
     string tag in ``countries_tags``; a missing/invalid tag is reported as a GAP,
     never a success. Recorded replays retain the ORIGINAL provider retrieval
     timestamp and report the replay execution time separately, never fabricating
     freshness.

This script is independent of any gitignored historical reference directory: all
generated inputs and retained cited CLI reports are written to a caller-selected
``--out-dir`` or a run-owned temporary directory.

Usage:
    # Timing only, offline (uses local data + installed packages; no provider calls)
    python examples/acceptance_demo.py --out-dir /tmp/acceptance

    # Timing + live provider lookups
    python examples/acceptance_demo.py --live --barcodes 0646670548871 0850056288972 0810021406954

    # Timing + recorded replay of a saved provider search (no network)
    python examples/acceptance_demo.py --replay <recorded.json>

    # Missing/invalid input probe: verifies a failing condition is reported
    python examples/acceptance_demo.py --verify-exit

    # Write the combined acceptance JSON to a path
    python examples/acceptance_demo.py --out /tmp/acceptance.json --out-dir /tmp/acceptance

The process exits non-zero if any REQUIRED timing/correctness/provenance/exit
condition fails; it exits 0 only when every required condition passes.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# --- deterministic 20-ingredient Phase-0 prototype label -------------------
PROTO_LABEL = (
    "water, red 40, titanium dioxide, bht, zinc pyrithione, lilial, "
    "formaldehyde, dibutyl phthalate, hydroquinone, 1,4-dioxane, "
    "acrylamide, isobutylparaben, isopropylparaben, benzylparaben, "
    "butylparaben, methylparaben, propylparaben, boric acid, "
    "sodium benzoate, sorbic acid"
)
PROTO_TARGET_SECONDS = 2.0
LINTER_TARGET_SECONDS = 30.0

US_TAG = "en:united-states"
DISCLAIMER = "Regulatory information, not legal advice."
SNAPSHOT = ROOT / "data" / "processed" / "eu_reg_lists.sqlite"


def _env() -> dict:
    return {
        "machine": platform.machine(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "interpreter": sys.executable,
    }


def _snapshot_info() -> dict:
    from eu_reg_lists import load
    reg = load(str(SNAPSHOT))
    return {
        "path": str(SNAPSHOT),
        "content_hash": (reg.metadata or {}).get("content_hash"),
        "data_updated_at": reg.data_updated_at,
        "registry_size": len(reg),
    }


def _clean_env() -> dict:
    """An explicit environment with any special PYTHONPATH removed."""
    env = {}
    for k, v in os.environ.items():
        if k == "PYTHONPATH":
            continue
        env[k] = v
    return env


def _run(cmd, cwd, env):
    """Run a subprocess, capturing stdout/stderr; never raises on non-zero."""
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                          env=env, check=False)


# --- Phase-0 prototype source entry point ----------------------------------
def _proto_source() -> tuple:
    spec = importlib.util.spec_from_file_location("eu_lint", str(ROOT / "eu_lint.py"))
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load eu_lint.py")
    el = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(el)
    return el, el.Dataset(str(ROOT / "data" / "eu_flags.json"))


def _generate_1000_batch(path: Path) -> Path:
    """Deterministically generate a 100-item / 1000-ingredient batch compatible
    with the one-label-per-line batch CLI (no embedded newlines)."""
    from eu_ingredient_linter.tokenize import tokenize
    from eu_reg_lists import load
    reg = load(str(SNAPSHOT))
    phrases = list(reg.phrases)
    single = []
    for p in phrases:
        t = tokenize(p, reg.phrases)
        if len(t) == 1 and t[0] == p and p.strip() and "\n" not in p and "\r" not in p:
            single.append(p)
        if len(single) >= 1000:
            break
    tokens = sorted(single[:1000])
    assert len(tokens) == 1000, len(tokens)
    lines = [", ".join(tokens[i:i + 10]) for i in range(0, 1000, 10)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _sha16(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _write_artifact(out_dir: Path, name: str, data: bytes) -> dict:
    p = out_dir / name
    p.write_bytes(data)
    return {"path": str(p), "sha256": hashlib.sha256(data).hexdigest()}


def timing(out_dir: Path) -> dict:
    """Run the two ACTUAL CLI contracts as subprocesses, verify output, and time.

    Both derived inputs and full cited CLI reports are written into ``out_dir``
    (retained, never summarised away). Any required truth condition that is
    false is recorded as a FAILED condition in ``required_conditions``.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    result: dict = {"mode": "offline-deterministic", "disclaimer": DISCLAIMER}
    result["environment"] = _env()
    result["snapshot"] = _snapshot_info()
    result["required_conditions"] = []
    conds: list = result["required_conditions"]

    def condition(name, passed, detail, kind="correctness"):
        conds.append({"name": name, "passed": bool(passed), "kind": kind,
                      "detail": detail})

    clean_env = _clean_env()

    # --- prototype: actual source CLI, cwd = repo root, env cleaned ---
    proto_cmd = [sys.executable, "eu_lint.py", PROTO_LABEL, "--market", "eu", "--format", "json"]
    t0 = time.perf_counter()
    proc = _run(proto_cmd, ROOT, clean_env)
    proto_secs = time.perf_counter() - t0
    proto = json.loads(proc.stdout) if proc.stdout.strip() else {}
    proto_artifact = _write_artifact(
        out_dir, "prototype-report.json", proc.stdout.encode("utf-8"))
    result["prototype"] = {
        "command": " ".join(proto_cmd),
        "exit_code": proc.returncode,
        "measured_seconds": proto_secs,
        "target_seconds": PROTO_TARGET_SECONDS,
        "passed_seconds": proto_secs < PROTO_TARGET_SECONDS,
        "input_sha256": _sha16(PROTO_LABEL.encode()),
        "n_ingredients": proto.get("n_ingredients"),
        "n_flagged": proto.get("n_flagged"),
        "verdict": proto.get("verdict"),
        "exit_flag_expected": proc.returncode == 1,
        "report_artifact": proto_artifact,
    }
    # Correctness. Findings exit 1 is EXPECTED (not an operational failure); a
    # different exit, wrong counts or missing citation fields are failures.
    el, ds = _proto_source()
    condition("prototype_exit_flag_expected", proc.returncode == 1,
              f"exit code {proc.returncode} (expected 1 from findings)",
              kind="exit")
    condition("prototype_n_ingredients", proto.get("n_ingredients") == 20,
              f"n_ingredients={proto.get('n_ingredients')} (expected 20)")
    condition("prototype_flagged", proto.get("n_flagged", 0) >= 1,
              f"n_flagged={proto.get('n_flagged')} (expected >=1)")
    condition("prototype_verdict", proto.get("verdict") == "FLAGGED",
              f"verdict={proto.get('verdict')} (expected FLAGGED)")
    condition("prototype_tokenized", len(el.tokenize(PROTO_LABEL, ds.phrases)) == 20,
              f"{len(el.tokenize(PROTO_LABEL, ds.phrases))} tokens (expected 20)")
    sample = proto.get("results", [{}])[0]
    condition("prototype_citations",
              isinstance(sample, dict) and "source_url" in sample
              and "regulation" in sample and "status" in sample,
              "citation fields (source_url/regulation/status) present on first result")
    condition("prototype_timing", proto_secs < PROTO_TARGET_SECONDS,
              f"{proto_secs:.4f}s < {PROTO_TARGET_SECONDS}s")

    # --- installed linter CLI with the saved 100-item / 1000-token batch ---
    batch_path = out_dir / "batch-1000-input.txt"
    _generate_1000_batch(batch_path)
    lines = [line.strip() for line in batch_path.read_text(encoding="utf-8").splitlines()
             if line.strip()]

    # Installed-package check runs OUTSIDE the source checkout with PYTHONPATH
    # removed. Editable installs resolve the same source, but the environment and
    # cwd are explicit so a developer's PYTHONPATH cannot leak in.
    linter_cmd = [sys.executable, "-m", "eu_ingredient_linter",
                  "--registry", str(SNAPSHOT), "--batch", str(batch_path),
                  "--format", "json"]
    t0 = time.perf_counter()
    proc = _run(linter_cmd, tempfile.gettempdir(), clean_env)
    linter_secs = time.perf_counter() - t0
    rep = json.loads(proc.stdout) if proc.stdout.strip() else {}
    linter_artifact = _write_artifact(
        out_dir, "linter-report.json", proc.stdout.encode("utf-8"))
    result["linter"] = {
        "command": " ".join(linter_cmd),
        "cwd": tempfile.gettempdir(),
        "exit_code": proc.returncode,
        "measured_seconds": linter_secs,
        "target_seconds": LINTER_TARGET_SECONDS,
        "passed_seconds": linter_secs < LINTER_TARGET_SECONDS,
        "input_sha256": _sha16(batch_path.read_bytes()),
        "n_items": len(lines),
        "total_ingredients": rep.get("total_ingredients"),
        "total_flagged": rep.get("total_flagged"),
        "is_valid": rep.get("is_valid"),
        "exit_flag_expected": proc.returncode == 1,
        "report_artifact": linter_artifact,
    }
    # Correctness: 100 items / 1000 tokens, valid, full cited JSON.
    condition("linter_exit_flag_expected", proc.returncode == 1,
              f"exit code {proc.returncode} (expected 1 from findings)",
              kind="exit")
    condition("linter_n_items", len(lines) == 100,
              f"{len(lines)} items (expected 100)")
    condition("linter_total_ingredients", rep.get("total_ingredients") == 1000,
              f"total_ingredients={rep.get('total_ingredients')} (expected 1000)")
    condition("linter_is_valid", rep.get("is_valid") is True,
              f"is_valid={rep.get('is_valid')}")
    condition("linter_disclaimer",
              rep.get("disclaimer") == DISCLAIMER,
              "disclaimer present in report")
    condition("linter_items_present",
              "items" in rep and len(rep["items"]) == 100,
              f"items present ({len(rep.get('items', []))}/100)")
    # Check citation/provenance on flagged + linked (membership) results.
    flagged_with_citation = 0
    flagged_total = 0
    membership_with_citation = 0
    membership_total = 0
    for item in rep.get("items", []):
        for r in item.get("results", []):
            if r.get("rules"):
                flagged_total += 1
                if r.get("source_url") and (r.get("regulation") or r.get("annex")):
                    flagged_with_citation += 1
            linked = r.get("linked_rules")
            if linked:
                membership_total += 1
                if linked[0].get("source_url"):
                    membership_with_citation += 1
    condition("linter_flagged_citation",
              flagged_total > 0 and flagged_with_citation == flagged_total,
              f"{flagged_with_citation}/{flagged_total} flagged findings carry "
              "source_url + regulation/annex")
    condition("linter_linked_citation",
              membership_total == 0 or membership_with_citation == membership_total,
              f"{membership_with_citation}/{membership_total} linked/membership "
              "findings carry source_url")
    condition("linter_timing", linter_secs < LINTER_TARGET_SECONDS,
              f"{linter_secs:.4f}s < {LINTER_TARGET_SECONDS}s")

    return result


def library_microbenchmark() -> dict:
    """Separately-labelled library timings (after import+load). These are NOT
    used to claim the CLI acceptance target; they show the library call cost."""
    from eu_ingredient_linter import batch_report
    from eu_ingredient_linter.tokenize import tokenize
    from eu_reg_lists import load

    reg = load(str(SNAPSHOT))
    phrases = list(reg.phrases)
    single = []
    for p in phrases:
        t = tokenize(p, reg.phrases)
        if len(t) == 1 and t[0] == p and p.strip() and "\n" not in p and "\r" not in p:
            single.append(p)
        if len(single) >= 1000:
            break
    tokens = sorted(single[:1000])
    items = [", ".join(tokens[i:i + 10]) for i in range(0, 1000, 10)]
    t0 = time.perf_counter()
    brep = batch_report(items, reg)
    secs = time.perf_counter() - t0
    assert brep["total_ingredients"] == 1000, brep["total_ingredients"]
    return {
        "label": "library microbenchmark (NOT the CLI target)",
        "measured_seconds": secs,
        "total_ingredients": brep["total_ingredients"],
        "total_flagged": brep["total_flagged"],
    }


def verify_exit_behavior(out_dir: Path) -> dict:
    """Prove a deliberately INVALID input yields the documented non-zero exit.
    Findings exit 1 is expected and is NOT an operational failure."""
    out_dir.mkdir(parents=True, exist_ok=True)
    empty = out_dir / "acceptance-empty-batch.txt"
    empty.write_text("\n\n", encoding="utf-8")
    cmd = [sys.executable, "-m", "eu_ingredient_linter", "--registry", str(SNAPSHOT),
           "--batch", str(empty), "--format", "json"]
    proc = _run(cmd, tempfile.gettempdir(), _clean_env())
    empty.unlink(missing_ok=True)
    return {
        "invalid_input_exit_code": proc.returncode,
        "invalid_input_expected_2": proc.returncode == 2,
        "stderr_has_error": "error" in (proc.stderr or "").lower(),
    }


def _is_us_tag(tag) -> bool:
    """Country evidence is valid only as an exact ``en:united-states`` string."""
    return isinstance(tag, str) and tag == US_TAG


# --- live / replay provider demonstration ---------------------------------
def live(barcodes, replay_record=None) -> list:
    """Run the real provider adapter (or a recorded replay) for each barcode and
    run the shared checker, retaining the FULL shared report and raw country
    evidence. Recorded replays preserve the original provider retrieval
    timestamp (never fabricate a fresh one)."""
    from eu_ingredient_linter import ingredient_report
    from eu_reg_lists import load

    reg = load(str(SNAPSHOT))
    rows = []
    for b in barcodes:
        res = _lookup_one(b, replay_record)
        row = {
            "barcode": str(b),
            "status": res.get("status"),
            "product_type": res.get("product_type"),
            "name": res.get("name"),
            "brands": res.get("brands"),
            "source_url": res.get("source_url"),
            "retrieved_at": res.get("retrieved_at"),
            "ingredients_text": res.get("ingredients_text"),
            "countries_tags": res.get("countries_tags") or [],
            "countries": res.get("countries"),
            "error": res.get("error"),
        }
        # Country evidence: exact provider-reporter market tag, not substring.
        us = any(_is_us_tag(c) for c in (row["countries_tags"] or []))
        row["us_market_evidence"] = {
            "present": bool(us),
            "tag": US_TAG,
            "source": "provider countries_tags (exact en:united-states)",
            "note": ("provider-reported market evidence, not independently "
                     "confirmed retail availability" if us else
                     "no exact en:united-states provider tag: reported as GAP, "
                     "not success"),
        }
        if res.get("product_type") and res["product_type"] != "beauty":
            row["note"] = f"not a cosmetic ({res['product_type']}); not checked"
        elif res.get("ingredients_text"):
            rep = ingredient_report(res["ingredients_text"], reg)
            row["check"] = _full_check(rep, reg)
        rows.append(row)
    return rows


def _lookup_one(barcode, replay_record=None):
    """Return a provider record for ``barcode``. Uses the recorded replay when
    present (``provider:path``), otherwise the real adapter."""
    if replay_record is not None:
        rec = replay_record.get(str(barcode)) or replay_record.get(barcode)
        if rec is None:
            raise LookupError(f"barcode {barcode} not in recorded replay")
        return rec
    from scan_to_check.obf import OpenBeautyFactsClient
    res = OpenBeautyFactsClient().lookup(str(barcode))
    return {
        "status": res.status,
        "product_type": res.product_type,
        "name": res.name,
        "brands": res.brands,
        "source_url": res.source_url,
        "retrieved_at": res.retrieved_at,
        "ingredients_text": res.ingredients_text,
        "countries_tags": res.countries_tags,
        "countries": res.countries,
        "error": res.error,
    }


def _full_check(rep: dict, reg) -> dict:
    """Retain the FULL shared report (direct + linked rules, conditions,
    citations, determination, is_in_dataset, unknown outcomes)."""
    return {
        "n_ingredients": rep["n_ingredients"],
        "n_flagged": rep["n_flagged"],
        "n_banned": rep["n_banned"],
        "n_not_in_dataset": rep["n_not_in_dataset"],
        "n_unverified_identity": rep["n_unverified_identity"],
        "n_unverified": rep["n_unverified"],
        "verdict": rep["verdict"],
        "clean": rep["clean"],
        "data_updated_at": rep["data_updated_at"],
        "content_hash": (reg.metadata or {}).get("content_hash"),
        "disclaimer": rep["disclaimer"],
        "coverage": rep["coverage"],
        "results": rep["results"],
    }


def record_replay(barcodes, out_path):
    """Fetch live provider records for ``barcodes`` and save them so a later
    ``--replay`` run needs no network. Records attribution/source URLs and the
    ORIGINAL provider retrieval time."""
    from eu_reg_lists import load
    reg = load(str(SNAPSHOT))
    out = {"disclaimer": DISCLAIMER,
           "snapshot_data_updated_at": reg.data_updated_at,
           "products": {}}
    for b in barcodes:
        out["products"][str(b)] = _lookup_one(b)
    Path(out_path).write_text(json.dumps(out, indent=2, default=str),
                              encoding="utf-8")
    return out


class LookupError(Exception):
    pass


def _overall_passed(out: dict) -> bool:
    """Required timing/correctness/provenance/exit conditions + product checks."""
    for cond in out.get("timing", {}).get("required_conditions", []):
        if not cond["passed"]:
            return False
    if "verify_exit_behavior" in out:
        if not out["verify_exit_behavior"]["invalid_input_expected_2"]:
            return False
    for row in (out.get("live", {}) or {}).get("products", []):
        evidence = row.get("us_market_evidence") or {}
        if evidence.get("present") is not True:
            return False
    return True


def main(argv) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true", help="also perform live provider lookups")
    ap.add_argument("--barcodes", nargs="*", default=None, help="barcodes to look up live")
    ap.add_argument("--replay", default=None,
                    help="a recorded provider-response JSON to replay instead of live calls")
    ap.add_argument("--record-replay", default=None,
                    help="fetch live provider records and save them to this path")
    ap.add_argument("--out", default=None,
                    help="write the combined JSON result here")
    ap.add_argument("--out-dir", default=None,
                    help="directory for generated inputs and retained cited CLI reports")
    ap.add_argument("--verify-exit", action="store_true",
                    help="also verify a failing acceptance condition yields non-zero exit")
    args = ap.parse_args(argv)

    # Every generated input and retained cited report goes to a caller-selected
    # dir (default: a run-owned temp dir) -- no dependence on .local-reference.
    out_dir = Path(args.out_dir if args.out_dir else tempfile.mkdtemp(prefix="acceptance-"))
    out: dict = {"timing": timing(out_dir),
                 "library_microbenchmark": library_microbenchmark()}
    out["out_dir"] = str(out_dir)
    if args.verify_exit:
        out["verify_exit_behavior"] = verify_exit_behavior(out_dir)
    if args.record_replay:
        if not args.barcodes:
            print("--record-replay requires --barcodes", file=sys.stderr)
            return 2
        out["recorded_replay_written"] = record_replay(args.barcodes, args.record_replay)
    if args.replay:
        rec = json.loads(Path(args.replay).read_text(encoding="utf-8"))
        products = rec.get("products", rec)
        _barcodes = args.barcodes or list(products.keys())
        replay_started = time.time()
        out["live"] = {"mode": "recorded-replay",
                       "replay_path": args.replay,
                       "replay_executed_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00",
                                                            time.gmtime()),
                       "replay_elapsed_seconds": time.time() - replay_started,
                       "products": live(_barcodes, products)}
    elif args.live:
        barcodes = args.barcodes or []
        if not barcodes:
            print("--live requires at least one --barcodes value", file=sys.stderr)
            return 2
        out["live"] = {"mode": "live-network", "products": live(barcodes)}

    passed = _overall_passed(out)
    out["acceptance_passed"] = passed
    out["disclaimer"] = DISCLAIMER

    text = json.dumps(out, indent=2, default=str)
    print(text)
    if args.out:
        Path(args.out).write_text(text)
    if not passed:
        print("acceptance FAILED: at least one required condition is false.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
