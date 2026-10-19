# Offline snapshot setup

A new checkout does not contain the gitignored regulatory data. Transfer an
existing, verified snapshot with these transfer commands; verify/import do not
fetch or normalize data. The accepted local checkpoint now includes CosIng
Annex II–VI and the supported REACH entry-70 branches. The file names below are examples, not a claim that all bundles have identical coverage.
Use source code compatible with the bundle and inspect verification output.

## Acquiring the official CosIng exports

The CosIng annex list pages are an Angular SPA; the export files are downloaded
from the Commission's export API when you click the **CSV** control on an annex
page (an official export download, not page scraping):

* Annex II list: `https://ec.europa.eu/growth/tools-databases/cosing/reference/annexes/list/II` → `COSING_Annex_II_v2.txt`
* Annex III list: `https://ec.europa.eu/growth/tools-databases/cosing/reference/annexes/list/III` → `COSING_Annex_III_v2.txt`

The configured official export endpoints are recorded in `data/scripts/fetch.py`.
Availability can change. Failed acquisition is recorded and preserves previously
accepted source coverage; it does not create new verified rules. ECHA acquisition
remains disabled. A CosIng download alone does not provide entry-70 coverage:
that requires the separately retained official legal document and accepted mapping.

## Export from the working checkout

```bash
python3 data/scripts/snapshot.py export --root . \
  --output .local-reference/snapshots/cosmetics-snapshot.zip
python3 data/scripts/snapshot.py verify .local-reference/snapshots/cosmetics-snapshot.zip
```

The ZIP includes SQLite, JSONL, metadata, the active fetch manifest, raw files
referenced by metadata, LICENSE and LICENSES.md. Rejected files retained for
provenance remain rejected; staging, previous versions and attempt logs are
excluded. Each member has a SHA-256 and byte count in snapshot-manifest.json.
Verification checks source hashes, accepted provenance, normalized content hash,
record counts, SQLite/JSONL parity and matching metadata. The legacy metadata
field `count_verified_rules` counts verified substances.

The **content hash is versioned** (`content_hash_version` in metadata). A bundle
is verified under its own recorded history contract; legacy bundles with no
recorded version are auto-detected (``member_of`` present → group-membership
contract; ``source_row`` in rule raw → Annex II-III contract; otherwise the
earliest single-annex contract). An unsupported hash version is rejected
(never silently reinterpreted), so historical bundles re-verify/import without
rewriting their recorded hash.

Identical input bytes produce identical ZIP bytes through fixed order and
timestamps. Snapshot dates are preserved. Record the archive SHA-256 printed by
export separately when transferring it. Integrity checks do not independently
authenticate the publisher: use a trusted bundle and retain source attribution.

## Restore beside a fresh code checkout

Copy the bundle through your normal file-transfer process, then run from the new
checkout:

```bash
python3 data/scripts/snapshot.py verify /absolute/path/cosmetics-snapshot.zip
python3 data/scripts/snapshot.py import /absolute/path/cosmetics-snapshot.zip \
  --destination .local-reference/restored-cosmetics

python3 -m venv .venv
.venv/bin/python -m pip install -e packages/eu-reg-lists \
  -e packages/eu-ingredient-linter -e packages/eu-reg-api

EU_REG_SNAPSHOT="$PWD/.local-reference/restored-cosmetics/data/processed/eu_reg_lists.sqlite" \
  .venv/bin/eu-reg-api
```

Import itself is offline and uses Python's standard library plus this checkout's
source code. Dependency installation may need a network or a populated package
cache. Python 3.11+ is recommended for this preview.

Import requires a **new destination directory**; export also refuses to replace
an existing bundle. Use new names for subsequent snapshots. Validation completes
before the destination is created. The active dataset is never overlaid.

## Confirm the preview

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/v1/substances/71-43-2
curl --get http://127.0.0.1:8000/v1/check \
  --data-urlencode 'ingredients=Aqua, Benzene, TotallyFake123'
```

Compare health's hash, date, counts and coverage with the verification output
for the exact bundle you restored. Do not refresh data merely to make historical
counts match. The accepted October 14 local content hash begins `5145f70e`, with
2,376 identities and 2,298 ordinary-rule verified identities. No public download
URL for that bundle is promised by this guide. Its coverage is
CosIng II–VI plus the supported entry-70 branches; ECHA exports remain excluded.
Older bundles retain their own counts, timestamps and narrower coverage.

Benzene and 1,4-dioxane are flagged with citations; Aqua and an unmatched
ingredient remain unknown. Conditional rules retain unresolved requirements.
Entry-70 evaluation is enabled only by a compatible accepted source mapping in
the loaded snapshot, not simply by installing newer code.

This completes local snapshot transfer and restoration, not unattended weekly
refresh, publication, or public hosting. Regulatory information, not legal
advice.

## Isolated unattended refresh + verified bundle preparation

`data/scripts/refresh_isolated.py` is the reviewable end-to-end path for
refreshing the verified snapshot **without ever writing into the live
checkout**. It reuses the existing `fetch` (staging-first, active-snapshot
fallback), `normalize.build_processed` (explicit inputs/output dirs) and
`snapshot` (export/import/verify) tooling; it does not implement a second
parser or content-hash algorithm.

```bash
python3 data/scripts/refresh_isolated.py \
  --work /tmp/eu-snapshot-work \
  --baseline-url "https://HOST/verified-baseline.zip" \
  --baseline-sha256 "TRUSTED-INDEPENDENT-SHA256" \
  --live \
  --out /tmp/candidate-snapshot
```

What it does:

1. **Restore a trusted baseline** into the run-owned `--work` dir, verifying the
   independently-supplied SHA-256 and importing through the full bundle
   verifier. A missing/corrupt baseline (bad URL, digest mismatch, failed
   verify/import) **fails clearly**; it never starts from zero coverage or a
   curated seed. The live checkout is never overlaid.
2. **Refresh only inside the isolated tree.** A real official fetch (`--live`)
   is best-effort; a 403 / HTML / timeout / header-only / unparseable attempt
   keeps that source's last accepted bytes and rules (the accepted snapshot is
   preserved — never wiped to zero). Retrieval timestamps stay separate from
   the snapshot's `data_updated_at` data-freshness. Without `--live`, only
   explicitly staged offline fixtures are available (acceptance scenarios).
3. **Validate the complete candidate** (source hashes, accepted provenance,
   versioned content hash, SQLite/JSONL parity, counts, no symlinks) before any
   selection, and emit a reviewable source/coverage diff. A loss of an entire
   previously-covered source list (or a verified-coverage drop) is **surfaced
   for review**, never silently treated as clean — while legitimate official
   removals are still reported with evidence, not assumed invalid.
4. **No-change keeps the baseline.** A semantically unchanged (or fully-failed)
   refresh returns the accepted baseline and its snapshot timestamp **without
   producing a newly-dated distribution snapshot**.
5. **A genuine change exports the verified consumer bundle.** For a valid
   changed candidate it writes `snapshot-bundle.zip` + a `.sha256` into `--out`,
   re-imports/re-verifies that bundle before marking it distributable, and
   records a concise `<work>/refresh-result.json` with status/diff/export.
   Staging files and private `.local-reference` content are excluded.

Controlled publication is **not** automated here. Publishing means moving the
verified bundle to a hosted, trusted URL and updating the consumer's
`SNAPSHOT_URL`/`SNAPSHOT_SHA256` **together** (a bundle digest and its URL must
change as one unit). The `.github/workflows/refresh-data.yml` job uses minimal
`contents: read`, a concurrency group so only one refresh runs at a time, and
uploads the verified candidate artifact only after validation. It does **not**
fabricate a release URL, initialize Git, push, deploy, or treat an upload as
completed durable publication. Actual hosted acceptance remains a separately
recorded step until a repository and destination host are configured.

Synthetic update fixtures (`--source-file KEY=PATH`) are **test fixtures only**
and are not promoted to the verified dataset.
