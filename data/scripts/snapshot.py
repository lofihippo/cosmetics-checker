"""Export, verify and import an existing regulatory snapshot without fetching.

Regulatory information, not legal advice.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import shutil
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "eu-reg-lists"))
sys.path.insert(0, str(ROOT / "data" / "scripts"))

from eu_reg_lists import load  # noqa: E402
from normalize import compute_content_hash  # noqa: E402

DISCLAIMER = "Regulatory information, not legal advice."
MANIFEST = "snapshot-manifest.json"
FIXED_FILES = {
    "LICENSE", "LICENSES.md", "data/raw/fetch_manifest.json",
    "data/processed/metadata.json", "data/processed/eu_reg_lists.sqlite",
    "data/processed/eu_reg_lists.jsonl",
}
MAX_BYTES = 512 * 1024 * 1024


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _raw_path(source: str, filename: str) -> str:
    for component in (source, filename):
        if (not component or component.startswith(".") or "/" in component
                or "\\" in component or ":" in component):
            raise ValueError("invalid source file path")
    return f"data/raw/{source}/{filename}"


def validate_tree(root: Path) -> tuple[dict, set[str]]:
    """Check source hashes, format parity and normalized content identity."""
    meta = _json(root / "data/processed/metadata.json")
    for key in ("data_updated_at", "version", "content_hash"):
        if not meta.get(key):
            raise ValueError(f"snapshot metadata missing {key}")
    files = set(FIXED_FILES)
    attempts = _json(root / "data/raw/fetch_manifest.json")
    sources = meta["verified_sources"]
    for key, source in sources.items():
        hashes = set()
        for filename, details in source.get("files", {}).items():
            relative = _raw_path(key, filename)
            data = (root / relative).read_bytes()
            digest = sha256(data)
            if digest != details["sha256"] or len(data) != details["byte_len"]:
                raise ValueError(f"source integrity mismatch: {relative}")
            files.add(relative)
            hashes.add(digest)
        if source.get("accepted"):
            entry = attempts.get(key, {})
            if not entry.get("accepted") or entry.get("sha256") not in hashes:
                raise ValueError(f"accepted source has no matching active manifest: {key}")

    jsonl_path = root / "data/processed/eu_reg_lists.jsonl"
    records = [json.loads(line) for line in jsonl_path.read_text().splitlines()
               if line.strip()]
    if not records:
        raise ValueError("snapshot contains no substances")
    verified = sum(r.get("determination", "unknown") != "unknown" for r in records)
    if (len(records) != meta["count_substances"]
            or verified != meta["count_verified_rules"]
            or len(records) - verified != meta["count_identity_only"]):
        raise ValueError("snapshot counts disagree with metadata")
    if not verified:
        raise ValueError("snapshot has no verified substances to distribute")
    # Select the historical content-hash contract recorded in the snapshot. A
    # legacy bundle (created before the hash was versioned) has no
    # ``content_hash_version``. Auto-detect that case:
    #   * records carry a ``member_of`` key        -> version 2 (group membership).
    #   * records carry ``source_row`` in rule raw -> version 1 (Annex II-III).
    #   * otherwise                                -> version 0 (earliest single-annex).
    hash_version = str(meta.get("content_hash_version") or "")
    if not hash_version:
        has_member_of = any("member_of" in r for r in records)
        has_source_row = any("source_row" in ((r.get("rules") or [{}])[0].get("raw") or {})
                             for r in records if r.get("rules"))
        hash_version = "2" if has_member_of else ("1" if has_source_row else "0")
    if hash_version not in ("0", "1", "2", "3"):
        raise ValueError(f"unsupported snapshot content-hash version: {hash_version}")
    if compute_content_hash(records, sources, hash_version=hash_version) != meta["content_hash"]:
        raise ValueError("normalized content_hash mismatch")
    for record in records:
        for rule in record.get("rules", []):
            raw = rule.get("raw") or {}
            source = sources.get(raw.get("source_kind"), {})
            file = source.get("files", {}).get(raw.get("source_file"), {})
            if (not source.get("accepted") or not raw.get("accepted")
                    or not file.get("sha256")
                    or raw.get("source_sha256") != file["sha256"]):
                raise ValueError("rule provenance does not match accepted source")
    sqlite = load(root / "data/processed/eu_reg_lists.sqlite")
    jsonl = load(jsonl_path, metadata=meta)
    if sqlite.metadata != meta:
        raise ValueError("SQLite and metadata.json disagree")
    if [asdict(s) for s in sqlite.substances] != [asdict(s) for s in jsonl.substances]:
        raise ValueError("SQLite and JSONL substance/rule records disagree")
    for name in files:
        if not (root / name).is_file() or (root / name).is_symlink():
            raise ValueError(f"snapshot file missing or symlinked: {name}")
    return meta, files


def export_snapshot(root: Path, output: Path) -> dict:
    """Create a deterministic ZIP; refuse to replace an existing bundle."""
    # Validate and package copied bytes, so a concurrent refresh cannot mix the
    # validated snapshot with files read later while the ZIP is being written.
    with tempfile.TemporaryDirectory(prefix="eu-snapshot-export-") as tmp:
        tree = Path(tmp)
        meta = _json(root / "data/processed/metadata.json")
        names = set(FIXED_FILES)
        for key, source in meta["verified_sources"].items():
            names.update(_raw_path(key, name) for name in source.get("files", {}))
        for name in sorted(names):
            src = root / name
            if src.is_symlink():
                raise ValueError(f"snapshot file is symlinked: {name}")
            dst = tree / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
        meta, names = validate_tree(tree)
        payload = {name: (tree / name).read_bytes() for name in sorted(names)}
        manifest = {
            "format_version": 1,
            "content_hash": meta["content_hash"],
            "data_updated_at": meta["data_updated_at"],
            "files": {name: {"sha256": sha256(data), "byte_len": len(data)}
                      for name, data in payload.items()},
            "disclaimer": DISCLAIMER,
        }
        payload[MANIFEST] = (json.dumps(manifest, sort_keys=True, indent=2)
                             + "\n").encode()
        if sum(map(len, payload.values())) > MAX_BYTES:
            raise ValueError("snapshot exceeds bundle size limit")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=9) as archive:
            for name, data in sorted(payload.items()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, data)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as stream:
            stream.write(buffer.getvalue())
    return {"archive": str(output), "sha256": sha256(buffer.getvalue()),
            "content_hash": meta["content_hash"], "disclaimer": DISCLAIMER}


def _verify_into(archive_path: Path, tree: Path) -> dict:
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        names = [m.filename for m in members]
        if len(names) != len(set(names)):
            raise ValueError("duplicate archive entries")
        if sum(m.file_size for m in members) > MAX_BYTES:
            raise ValueError("snapshot exceeds bundle size limit")
        for member in members:
            name = member.filename
            path = PurePosixPath(name)
            if (name != path.as_posix() or path.is_absolute()
                    or any(p in (".", "..") for p in path.parts)
                    or "\\" in name or ":" in name or member.is_dir()
                    or (member.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError("invalid archive path or member type")
        manifest = json.loads(archive.read(MANIFEST))
        if manifest.get("format_version") != 1:
            raise ValueError("unsupported snapshot bundle version")
        expected = manifest["files"]
        if set(names) != set(expected) | {MANIFEST}:
            raise ValueError("archive files disagree with bundle manifest")
        if not FIXED_FILES <= set(expected):
            raise ValueError("bundle is missing required snapshot files")
        for name, details in expected.items():
            if name not in FIXED_FILES:
                parts = PurePosixPath(name).parts
                if len(parts) != 4 or parts[:2] != ("data", "raw"):
                    raise ValueError("unexpected bundle file")
                _raw_path(parts[2], parts[3])
            data = archive.read(name)
            if sha256(data) != details["sha256"] or len(data) != details["byte_len"]:
                raise ValueError(f"bundle integrity mismatch: {name}")
            dest = tree / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
    meta, actual = validate_tree(tree)
    if set(expected) != actual:
        raise ValueError("bundle file inventory disagrees with source metadata")
    if (manifest["content_hash"] != meta["content_hash"]
            or manifest["data_updated_at"] != meta["data_updated_at"]):
        raise ValueError("bundle identity disagrees with snapshot metadata")
    (tree / MANIFEST).write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    return {"content_hash": meta["content_hash"],
            "data_updated_at": meta["data_updated_at"],
            "count_substances": meta["count_substances"],
            "count_verified_substances": meta["count_verified_rules"],
            "disclaimer": DISCLAIMER}


def verify_snapshot(archive: Path, destination: Path | None = None) -> dict:
    """Verify before importing; import only into a new directory."""
    if destination is not None and destination.exists():
        raise FileExistsError("destination already exists; choose a new directory")
    with tempfile.TemporaryDirectory(prefix="eu-snapshot-verify-") as tmp:
        tree = Path(tmp)
        report = _verify_into(archive, tree)
        if destination is not None:
            # copytree's default also refuses a destination created while the
            # archive was being verified. Never overlay a live checkout.
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(tree, destination)
            report["destination"] = str(destination)
        return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="bundle an existing snapshot")
    export.add_argument("--root", type=Path, default=ROOT)
    export.add_argument("--output", type=Path, required=True)
    for action in ("verify", "import"):
        command = commands.add_parser(action, help=f"{action} a snapshot bundle")
        command.add_argument("archive", type=Path)
        if action == "import":
            command.add_argument("--destination", type=Path, required=True)
    # ``verify-tree`` validates an already-imported snapshot directory in place
    # using the same full tree-verification contract as a verified bundle. It
    # never mutates the directory.
    verify_tree = commands.add_parser(
        "verify-tree", help="fully verify an imported snapshot directory in place")
    verify_tree.add_argument("tree", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            report = export_snapshot(args.root, args.output)
        elif args.command == "verify-tree":
            meta, files = validate_tree(args.tree)
            report = {
                "content_hash": meta["content_hash"],
                "data_updated_at": meta["data_updated_at"],
                "count_substances": meta["count_substances"],
                "count_verified_substances": meta["count_verified_rules"],
                "files_verified": len(files),
                "disclaimer": DISCLAIMER,
            }
        else:
            report = verify_snapshot(args.archive, getattr(args, "destination", None))
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        print(f"Snapshot operation failed: {exc}\n{DISCLAIMER}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
