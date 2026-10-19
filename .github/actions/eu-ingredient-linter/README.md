# EU Ingredient Linter — reusable GitHub Action

A reusable Action that checks a cosmetic ingredient file (CSV/TSV/JSON) against a
**fully verified, explicitly supplied** EU-regulatory snapshot, emits GitHub
workflow annotations, and preserves a cited JSON report for artifact upload. It
reuses the installed `eu-ingredient-linter` CLI and the repo's snapshot
verify/import tooling — it does **not** implement a second checker and does
**not** fetch any official export.

## Layout and how it is invoked

This Action is a **composite** wrapper (`action.yml` → `runs.using: composite`)
that builds the container from the **action repository root** and runs it with the
**consuming workspace** bind-mounted at the same absolute path. This is why a
Docker *Action* (`image: Dockerfile` with the Dockerfile's parent as build
context) could not see its own `packages/`/`data/scripts/`: the composite wrapper
fixes that by building the existing `Dockerfile` from the repository root.

| Term | Meaning |
| --- | --- |
| Action source directory | `${{ github.action_path }}` (has `Dockerfile`, `entrypoint.sh`, `docker-wrapper.sh`). |
| Action repository root | The directory owning `packages/` and `data/scripts/` (found by walking up from the action source). This is the Docker build context. |
| Consuming workspace | `${{ github.workspace }}`. Bind-mounted into the container at the same absolute path, so snapshot / input / report paths resolve unchanged inside. |
| Container | `python:3.11-slim` with the local `eu-reg-lists` + `eu-ingredient-linter` installed; runs `entrypoint.sh`. |

The wrapper builds the image (tag `eu-ingredient-linter-action:local`), then runs
`docker run --network=none` (the check makes no regulatory network fetch). A
later step in a workflow may re-use the wrapper (which rebuilds if source
changed) or you may build once and run directly.

## Snapshot acquisition & verification contract (fresh runner)

Raw and processed regulatory data are gitignored, so they are NOT present on a
fresh runner. The Action requires an explicit snapshot source and never falls
back to a curated/empty registry:

- **Snapshot must be supplied** via the `snapshot-path` input — either:
  - a **bundle `.zip`** produced by `python data/scripts/snapshot.py export
    --root . --output snapshot.zip`, or
  - an **imported directory** (a real snapshot tree containing
    `data/processed/eu_reg_lists.sqlite`, `metadata.json`, the JSONL, the raw
    source files and `fetch_manifest.json`).
- **Both forms are fully verified before use**: a bundle is verified (hash +
  purity + full tree contract) and imported into a temp dir; a directory is
  verified in place via the same `validate_tree` contract (source hashes, format
  parity, normalized content hash, counts, no symlinks). Missing, tampered,
  empty, and identity-only snapshots are **rejected** before any ingredient is
  checked. The supplied snapshot is never mutated.
- **Acquisition on a fresh runner.** The example workflow acquires a trusted
  bundle from a maintainer-configured source (`SNAPSHOT_URL`) into a fresh
  workspace directory, checks it against an independently supplied expected
  `SNAPSHOT_SHA256`, then passes the verified bundle to the Action (which runs
  the full bundle verifier/importer). Wrong digest, unavailable source and
  corrupt bundle all fail without falling back to unverified data. No official
  export is fetched in the PR job; build/package dependencies may use the
  network but the checking container runs `--network=none`.
- The Action runs without fetching official exports and needs no network to the
  regulatory sources (the check runs `--network=none`).

`action.yml` inputs:

| Input | Required | Description |
| --- | --- | --- |
| `input-file` | yes | Path to the ingredient file (CSV/TSV/JSON). One file = one formula. Relative paths resolve against `github.workspace`. |
| `input-format` | no | `csv` (default), `tsv`, or `json`. |
| `snapshot-path` | yes | Snapshot bundle `.zip` or a fully-imported snapshot directory. Both fully verified. |
| `report-output` | no | Path to write the cited JSON report (default `eu-ingredient-report.json`, in the workspace root if relative). A valid current report is only written here on success; a prior file at the same path is never overwritten by a failed run. |
| `fail-on` | no | `findings` (default) or `invalid-input`. Never a compliance claim. |

`action.yml` outputs:

| Output | Meaning |
| --- | --- |
| `report-valid` | `"1"` if THIS invocation produced and validated a fresh current JSON report; `"0"` otherwise. Gate artifact upload on this; never upload a stale/absent report. |
| `report-path` | Absolute path to the validated current JSON report, meaningful only when `report-valid` is `"1"`. |

## Behavior

- **Inputs are data, not shell code.** Every `${{ inputs.* }}` is passed through a
  step `env` and referenced as a quoted shell variable in the `run` script, so a
  value containing `$()`, backticks, quotes or other shell syntax is carried as
  data and never executed. The same applies to the workflow's manual inputs and
  repository variables (mapped to `env`).
- **One shared evaluation, per-invocation report.** The Action runs the CLI once
  with `--format github` (annotations to stdout) and `--output <staging report>`
  then validates the staged report and only on success promotes it to the
  caller's `report-output`. Both annotations and the saved report derive from the
  same evaluation and no empty/stale report is presented as current.
- **A current-run report is required.** After the CLI returns, the staged report
  is validated (it must parse as JSON, be an object, carry the required
  disclaimer and `data_updated_at`, and contain at least one ingredient record).
  A missing/malformed report is **not** treated as an exit-1 finding: a CLI that
  exits 1 (or 0) without a valid report fails operationally (exit 2) under BOTH
  `fail-on` policies. A genuine finding (valid evidence report) maps per the
  policy.
- **Annotations.** One `::error`/`::warning`/`::notice` per direct/linked rule
  and one `notice` for an unknown / not-in-dataset ingredient. Severity is
  presentational, not a regulatory classification: `error` for a definitive
  prohibition (`prohibition_finding == "unambiguous"`) or a supported-limit
  exceedance; `warning` for identified restriction/conditional evidence
  **and** for `scope_unresolved`/`exception_unresolved` assessments; `notice`
  for an unresolved/unknown identity. A source rule's `banned` status does not by
  itself force `error`, and linked-group evidence is never promoted to a
  definitive prohibition. A record with both direct and linked evidence labels
  each annotation's `origin=direct` / `origin=linked group` and, for linked
  evidence, the source group/reference.
- **Input-file attribution.** The consumer's input-file path is passed as the
  escaped `file=` property (workspace-relative recommended). The Action never
  uses a regulatory source `row_reference` as the user's input-file line and
  never invents a `line`.
- **Escaping.** Annotations and the shell-wrapper diagnostics escape `%`→`%25`,
  CR→`%0D`, LF→`%0A`, and (for command properties) `,`/`:` as well, so untrusted
  ingredient/text/path values cannot inject a second workflow command.
- **Required disclaimer** is always present in the annotation output.
- **Exit policy.**
  - `0` — no finding **and** a valid current report was produced; **never a
    compliance certificate** (an unknown identity is not "clean").
  - `1` — a finding under `fail-on=findings` (annotated; valid report; step fails).
  - `2` — **operational failure** under BOTH policies: invalid/unparseable input,
    snapshot verification failure, missing/malformed current report, report-write
    failure, or an unexpected CLI exit. The JSON report is not left as a
    success-shaped artifact and a stale report at the output path is not
    overwritten.
  - `fail-on=invalid-input` fails only on invalid input — a finding alone does
    not fail the step, but a valid report is still written.
- Paths with spaces are handled (quoted args, arrays, bind-mount).

## PR-check workflow

See `.github/workflows/eu-ingredient-linter.yml` for the runnable `pull_request`
(+ `workflow_dispatch`) example. It uses a **normal (non-privileged)** checkout
`permissions: contents: read`, no secrets, and **preserves the failed job
conclusion** after uploading the report (it does not use `continue-on-error` or a
successful echo to turn a checker failure green).

### What a consuming repository must configure

1. **Trusted snapshot source** — set the repository variables `SNAPSHOT_URL`
   (a maintainer-published/attached verified snapshot bundle `.zip`) and
   `SNAPSHOT_SHA256` (the trusted expected SHA-256 of that exact bundle). The
   workflow downloads it into a fresh workspace directory, checks the digest, and
   the Action runs the full bundle verifier/importer. Nothing is fetched from a
   regulatory source; nothing falls back to curated/empty data.
2. **Ingredient file** — on `pull_request`, set the `INGREDIENT_FILE` variable
   (defaults to `examples/formulas/structured-formula.csv`); on
   `workflow_dispatch` it is a manual input. This file is the content under
   review, kept separate from the trusted snapshot.
3. **`input-format`** — a `INPUT_FORMAT` variable (`csv`/`tsv`/`json`, default
   `csv`).
4. **`fail-on`** — optional `FAIL_ON` variable (`findings` default or
   `invalid-input`).
5. **Path trigger** — adjust the `pull_request.paths` globs to the ingredient file
   locations you actually check.

### Minimal snippet

```yaml
- name: Check ingredients
  id: check
  uses: ./.github/actions/eu-ingredient-linter
  with:
    input-file: ./examples/formulas/structured-formula.csv
    input-format: csv
    snapshot-path: "${{ github.workspace }}/.eu-lint-run/snapshot-acquire/snapshot-bundle.zip"
    report-output: "${{ github.workspace }}/eu-ingredient-report.json"
    fail-on: "${{ vars.FAIL_ON || 'findings' }}"

- name: Upload cited JSON report
  if: always() && steps.check.outputs.report-valid == '1'
  uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4.6.2
  with:
    name: eu-ingredient-report
    path: "${{ steps.check.outputs.report-path }}"
    if-no-files-found: warn
```

> A maintainer must supply `SNAPSHOT_URL` + `SNAPSHOT_SHA256` (verified snapshot
> bundle + expected digest). The Action never fetches official exports and never
> falls back to curated/empty data. Upload is gated on `report-valid`, so a stale
> or absent report is never published as current.

## Security / permissions

- Minimal workflow permissions (`contents: read`); no secrets required.
- The Action runs its own installed packages/tooling in a container on a normal
  (non-privileged) checkout and makes no regulatory network fetch. It does not
  use `pull_request_target`. It is **not** a claim that checked-out PR code never
  runs — keep any write/trusted token out of this context.

## Local verification

You can exercise the composite wrapper locally (requires a running Docker
daemon) by supplying the action directory, the workspace, and the five inputs:

```sh
.github/actions/eu-ingredient-linter/docker-wrapper.sh \
  "$PWD/.github/actions/eu-ingredient-linter" "$PWD" \
  "examples/formulas/structured-formula.csv" csv \
  "$PWD/snapshot-bundle.zip" "$PWD/report.json" findings
```

Regression tests: `tests/test_action_integration.py`,
`tests/test_action_composite_boundary.py`, `tests/test_action_acquisition.py`,
`tests/test_snapshot.py` and
`packages/eu-ingredient-linter/tests/test_github_annotation_semantics.py`.
