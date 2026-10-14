"""Tests for the Phase 0 eu_lint.py prototype.

Verifies tokenizer behaviour (including punctuation-safe protection), NULL
semantics, honest "clean"/exit handling, consistent alias↔rule resolution,
duplicate/conflicting-record handling, conditional-only flags, provenance and
disclaimer presence, and deterministic generation.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import eu_lint  # noqa: E402

DATASET = ROOT / "data" / "eu_flags.json"
FIXTURES = ROOT / "examples" / "demo_labels"
DISCLAIMER = "Regulatory information, not legal advice."


@pytest.fixture(scope="module")
def dataset():
    return eu_lint.Dataset(DATASET)


# ---------------------------------------------------------------------------
# Tokenizer.
# ---------------------------------------------------------------------------
class TestTokenizer:
    def test_splits_on_commas_and_semicolons(self):
        assert eu_lint.tokenize("water, glycerin; dimethicone") == [
            "water", "glycerin", "dimethicone"
        ]

    def test_strips_percentages(self):
        assert eu_lint.tokenize("Water 5%, Glycerin 10%") == ["water", "glycerin"]

    def test_strips_parentheses_and_whitespace(self):
        assert eu_lint.tokenize("AQUA (WATER),  Red 40 ") == ["aqua", "red 40"]

    def test_lowercases_and_strips_html(self):
        assert eu_lint.tokenize("Red&nbsp;40 &amp; Blue 1") == [
            "red 40", "blue 1"
        ]

    def test_slash_variant_splits(self):
        assert eu_lint.tokenize("WATER/EAU/AQUA") == ["water", "eau", "aqua"]

    # --- Finding 4: punctuation-safe tokenization ---
    def test_protects_ampersand_name(self, dataset):
        assert eu_lint.tokenize("FD&C Red 40, Blue 1", dataset.phrases) == [
            "fd&c red 40", "blue 1"
        ]

    def test_protects_comma_name(self, dataset):
        assert eu_lint.tokenize("1,4-dioxane, water", dataset.phrases) == [
            "1,4-dioxane", "water"
        ]

    # --- Finding 4: protection survives HTML-entity + whitespace variations ---
    def test_protects_html_entity_variant(self, dataset):
        assert eu_lint.tokenize("FD&amp;C Red 40", dataset.phrases) == [
            "fd&c red 40"
        ]

    def test_protects_multi_space_variant(self, dataset):
        assert eu_lint.tokenize("FD&C  Red 40", dataset.phrases) == [
            "fd&c red 40"
        ]

    def test_fdc_red40_resolves_not_split(self, dataset):
        r = eu_lint.check_label("FD&C Red 40", dataset)
        assert r["tokens"] == ["fd&c red 40"]
        res = r["results"][0]
        assert res["is_in_dataset"] is True
        assert res["determination"] == "restricted"
        assert res["status"] == "restricted"
        assert r["n_flagged"] == 1

    def test_dioxane_resolves_not_split(self, dataset):
        r = eu_lint.check_label("1,4-dioxane", dataset)
        assert r["tokens"] == ["1,4-dioxane"]
        assert r["results"][0]["status"] == "banned"
        assert r["n_flagged"] == 1


# ---------------------------------------------------------------------------
# Checker / NULL semantics.
# ---------------------------------------------------------------------------
class TestChecker:
    def test_unknown_token_is_not_clean(self, dataset):
        r = eu_lint.check_token("totally unknown substance", dataset)
        assert r["is_in_dataset"] is False
        assert r["status"] is None
        assert r["determination"] == "unknown"
        assert r["annex"] is None
        assert r["regulation"] is None

    def test_not_in_dataset_never_implies_ok(self, dataset):
        result = eu_lint.check_label("unicorn tears", dataset)
        assert result["n_not_in_dataset"] == 1
        assert result["clean"] is False
        assert result["verdict"] == "NOT-FULLY-VERIFIED"

    def test_identity_only_substance_is_not_clean(self, dataset):
        # "water" is a recognised identity w/o a verified EU rule -> unverified.
        result = eu_lint.check_label("water", dataset)
        res = result["results"][0]
        assert res["is_in_dataset"] is True
        assert res["determination"] == "unknown"
        assert result["n_unverified_identity"] == 1
        assert result["clean"] is False
        assert result["verdict"] == "NOT-FULLY-VERIFIED"

    def test_banned_sets_has_error(self, dataset):
        r = eu_lint.check_label("zinc pyrithione", dataset)
        assert r["has_error"] is True
        assert r["clean"] is False
        assert r["n_banned"] == 1

    def test_restricted_sets_clean_false(self, dataset):
        r = eu_lint.check_label("red 40", dataset)
        assert r["clean"] is False
        assert r["has_error"] is False
        assert r["n_restricted"] == 1

    # --- Finding 1: consistent alias/name -> rule resolution ---
    def test_aliases_resolve_to_same_rule(self, dataset):
        lilial = eu_lint.check_token("lilial", dataset)
        lily = eu_lint.check_token("lily aldehyde", dataset)
        assert lilial["status"] == lily["status"] == "banned"
        assert lilial["annex"] == lily["annex"] == "Annex II"
        # Both point to the same substance identity (same rules).
        assert lilial["rules"] == lily["rules"]

    def test_ada_alias_consistent_with_name(self, dataset):
        ada = eu_lint.check_token("ada", dataset)
        full = eu_lint.check_token("azodicarbonamide", dataset)
        assert ada["status"] == full["status"] == "restricted"
        assert ada["rules"] == full["rules"]

    def test_every_substance_has_unique_identity(self, dataset):
        # No duplicate canonical names in the dataset.
        names = [s.name.lower() for s in dataset.substances]
        assert len(names) == len(set(names))

    def test_shipped_dataset_has_multi_rule_substances(self, dataset):
        # The shipped dataset contains at least one substance subject to BOTH a
        # cosmetic and a food rule (not just a synthetic test case).
        multi = [s for s in dataset.substances if len(s.rules) >= 2]
        assert multi, "no multi-rule substance in shipped dataset"
        for s in multi:
            annexes = {r["annex"] for r in s.rules}
            assert len(annexes) >= 2

    def test_sodium_benzoate_carries_both_rules(self, dataset):
        r = eu_lint.check_token("sodium benzoate", dataset)
        annexes = {x["annex"] for x in r["rules"]}
        assert annexes == {"Annex V", "Food"}
        assert len(r["rules"]) == 2

    def test_legitimate_multiple_rules_retained(self, tmp_path):
        # A substance legitimately subject to BOTH a food rule and a cosmetic
        # rule must retain both -- the later rule must NOT overwrite the first.
        doc = {
            "meta": {"data_updated_at": "2026-01-01T00:00:00Z"},
            "substances": [{
                "id": "sodium-benzoate",
                "name": "Sodium benzoate",
                "aliases": ["e211"],
                "cas": "532-32-1",
                "ec": "208-534-8",
                "determination": "allowed_with_conditions",
                "rules": [
                    {"status": "allowed_with_conditions", "annex": "Annex V",
                     "regulation": "Regulation (EC) No 1223/2009",
                     "source_url": "https://example/cosmetic",
                     "conditions": "cosmetic preservative cap."},
                    {"status": "restricted", "annex": "Food",
                     "regulation": "Regulation (EC) No 1333/2008",
                     "source_url": "https://example/food",
                     "conditions": "food additive cap."},
                ],
            }],
        }
        p = tmp_path / "ds.json"
        p.write_text(json.dumps(doc))
        ds = eu_lint.Dataset(p)
        res = eu_lint.check_token("e211", ds)
        assert res["is_in_dataset"] is True
        # All rules retained, none dropped.
        assert len(res["rules"]) == 2
        annexes = {r["annex"] for r in res["rules"]}
        assert annexes == {"Annex V", "Food"}
        # Strongest rule reported as the headline status but not by dropping others.
        assert res["status"] == "restricted"
        assert res["determination"] == "allowed_with_conditions"


# ---------------------------------------------------------------------------
# Finding 2: conditional rules are flags (never clean).
# ---------------------------------------------------------------------------
class TestConditional:
    @pytest.mark.parametrize("ing", ["methylparaben", "propylparaben",
                                     "titanium dioxide", "octocrylene"])
    def test_conditional_is_flagged_not_clean(self, dataset, ing):
        r = eu_lint.check_label(ing, dataset)
        assert r["n_flagged"] == 1
        assert r["clean"] is False
        assert r["verdict"] == "FLAGGED"
        assert r["has_error"] is False  # not a banned substance

    def test_bht_conditional(self, dataset):
        r = eu_lint.check_label("bht", dataset)
        assert r["n_flagged"] == 1
        assert r["clean"] is False

    @pytest.mark.parametrize("ing", ["methylparaben", "bht", "titanium dioxide"])
    def test_conditional_exit_code_is_1(self, ing):
        p = _run(ing, "--format", "json")
        assert p.returncode == 1


# ---------------------------------------------------------------------------
# Finding 3: recognised-but-unverified identities are NOT affirmative.
# ---------------------------------------------------------------------------
class TestUnverified:
    def test_identity_only_never_claims_clean(self, dataset):
        for name in ["water", "glycerin", "dimethicone", "natural flavor"]:
            r = eu_lint.check_label(name, dataset)
            assert r["results"][0]["determination"] == "unknown"
            assert r["clean"] is False
            assert r["n_flagged"] == 0  # no affirmative status is invented

    def test_no_unverified_claims_no_restriction(self, dataset):
        # Identity-only substances must NOT have a rule or a non-unknown status.
        for sub in dataset.substances:
            if sub.determination == "unknown":
                assert sub.rules == []


# ---------------------------------------------------------------------------
# Finding 1 (revisited): order-independent, ambiguous-alias rejection.
# ---------------------------------------------------------------------------
class TestAliasAmbiguity:
    @pytest.fixture
    def reversed_dataset(self, tmp_path):
        import json as _json
        doc = _json.loads(DATASET.read_text())
        doc["substances"].reverse()
        p = tmp_path / "reversed.json"
        p.write_text(_json.dumps(doc))
        return eu_lint.Dataset(p)

    def test_results_order_independent(self, dataset, reversed_dataset):
        probes = ["e127", "erythrosine", "FD&C Red 3", "ci 45430", "red 40",
                  "e110", "bht", "azodicarbonamide"]
        for tok in probes:
            a = eu_lint.check_token(tok, dataset)
            b = eu_lint.check_token(tok, reversed_dataset)
            assert a["status"] == b["status"]
            assert a["rules"] == b["rules"]

    def test_no_ambiguous_aliases_in_shipped_dataset(self, dataset):
        assert dataset.ambiguous_aliases == set()

    def test_ambiguous_alias_is_not_silently_resolved(self, tmp_path):
        # Two substances claiming the same alias must be rejected as ambiguous.
        doc = {
            "meta": {"data_updated_at": "2026-01-01T00:00:00Z"},
            "substances": [
                {"id": "a", "name": "Alpha", "aliases": ["shared", "s1"],
                 "cas": None, "ec": None, "determination": "restricted",
                 "rules": [{"status": "restricted", "annex": "Food",
                            "regulation": "Reg X", "source_url": "https://x",
                            "conditions": ""}]},
                {"id": "b", "name": "Beta", "aliases": ["shared"],
                 "cas": None, "ec": None, "determination": "restricted",
                 "rules": [{"status": "restricted", "annex": "SVHC",
                            "regulation": "Reg Y", "source_url": "https://y",
                            "conditions": ""}]},
            ],
        }
        p = tmp_path / "amb.json"
        p.write_text(json.dumps(doc))
        ds = eu_lint.Dataset(p)
        assert "shared" in ds.ambiguous_aliases
        # lookup never silently picks a row.
        assert ds.lookup("shared") is None
        # The non-ambiguous alias still resolves.
        assert ds.lookup("s1") is not None

    def test_canonical_name_collision_is_rejected(self, tmp_path):
        # Two distinct substances sharing a canonical name cannot be told apart
        # by that name -> it must be ambiguous, never silently mapped to one.
        doc = {
            "meta": {"data_updated_at": "2026-01-01T00:00:00Z"},
            "substances": [
                {"id": "a", "name": "Collide", "aliases": ["solo-a"],
                 "cas": None, "ec": None, "determination": "restricted",
                 "rules": [{"status": "restricted", "annex": "Food",
                            "regulation": "Reg X", "source_url": "https://x",
                            "conditions": ""}]},
                {"id": "b", "name": "Collide", "aliases": ["solo-b"],
                 "cas": None, "ec": None, "determination": "banned",
                 "rules": [{"status": "banned", "annex": "Annex II",
                            "regulation": "Reg Y", "source_url": "https://y",
                            "conditions": ""}]},
            ],
        }
        p = tmp_path / "canon.json"
        p.write_text(json.dumps(doc))
        ds = eu_lint.Dataset(p)
        # The colliding canonical name is ambiguous -> unresolved.
        assert "collide" in ds.ambiguous_aliases
        assert ds.lookup("collide") is None
        # Non-colliding aliases still resolve to their own substance.
        assert ds.lookup("solo-a").id == "a"
        assert ds.lookup("solo-b").id == "b"
        # The colliding name is not used for phrase protection either.
        assert "collide" not in ds.phrases


# ---------------------------------------------------------------------------
# Finding 3: empty / stripped input is never clean.
# ---------------------------------------------------------------------------
class TestEmptyInput:
    @pytest.mark.parametrize("text", ["   ", ";;;", "(zinc pyrithione)", ",,"])
    def test_empty_input_never_clean(self, dataset, text):
        r = eu_lint.check_label(text, dataset)
        assert r["clean"] is False
        assert r["verdict"] == "NO-INGREDIENTS"
        assert r["no_ingredients"] is True
        assert r["n_ingredients"] == 0

    def test_cli_empty_input_exits_2(self):
        p = _run("   ", "--format", "json")
        assert p.returncode == 2

    def test_default_demo_label_parses(self, dataset):
        # The Streamlit default label must parse (no crash / no empty).
        r = eu_lint.check_label("water, red 40, titanium dioxide, bht", dataset)
        assert r["n_ingredients"] == 4
        assert r["results"][0]["determination"] == "unknown"


# ---------------------------------------------------------------------------
# Finding 2: Streamlit table must handle null status (unverified).
# ---------------------------------------------------------------------------
class TestStreamlitRendering:
    def test_status_label_handles_null_status(self, dataset):
        # Recognised-but-unverified -> status is None -> "Unverified" (no crash).
        water = eu_lint.check_token("water", dataset)
        assert water["status"] is None
        assert eu_lint.status_label(water) == "Unverified"
        # Not in dataset -> "Not in dataset".
        unknown = eu_lint.check_token("zzz no such", dataset)
        assert eu_lint.status_label(unknown) == "Not in dataset"
        # Flagged -> humanised status.
        red40 = eu_lint.check_token("red 40", dataset)
        assert eu_lint.status_label(red40) == "Restricted"

    def test_default_label_all_rows_render(self, dataset):
        r = eu_lint.check_label("water, red 40, titanium dioxide, bht", dataset)
        for row in r["results"]:
            label = eu_lint.status_label(row)
            assert isinstance(label, str) and label
            assert label != "None"  # must never render the string "None"


# ---------------------------------------------------------------------------
# Finding 5 & 6: citations, timestamp and disclaimer in every output.
# ---------------------------------------------------------------------------
@pytest.fixture
def flagged_result(dataset):
    return eu_lint.check_label("red 40, methylparaben", dataset)


class TestOutputs:
    def test_each_flagged_record_has_full_citation(self, flagged_result):
        for r in flagged_result["results"]:
            assert r["is_in_dataset"] is True
            assert r["regulation"]
            assert r["source_url"]
            assert r["data_updated_at"]
            for rule in r["rules"]:
                assert rule["regulation"]
                assert rule["source_url"]

    def test_sources_are_not_all_identical(self, dataset):
        urls = {r["source_url"] for sub in dataset.substances for r in sub.rules}
        assert len(urls) >= 2  # cosmetic vs food differ

    def test_render_json_includes_citation_and_disclaimer(self, flagged_result):
        parsed = json.loads(eu_lint.render_json(flagged_result))
        assert parsed["disclaimer"] == DISCLAIMER
        res = parsed["results"][0]
        assert res["regulation"]
        assert res["source_url"]
        assert res["data_updated_at"]

    def test_render_csv_includes_citation_and_disclaimer(self, flagged_result):
        csv_out = eu_lint.render_csv(flagged_result)
        assert "regulation" in csv_out
        assert "source_url" in csv_out
        assert "data_updated_at" in csv_out
        assert DISCLAIMER in csv_out

    def test_render_text_includes_source_and_disclaimer(self, flagged_result):
        txt = eu_lint.render_text(flagged_result)
        assert "https://" in txt
        assert "data_updated_at" in txt
        assert DISCLAIMER in txt


# ---------------------------------------------------------------------------
# Golden JSON output.
# ---------------------------------------------------------------------------
@pytest.fixture
def golden_result(dataset):
    return eu_lint.check_label("water, red 40, titanium dioxide, bht", dataset)


class TestGolden:
    def test_counts(self, golden_result):
        assert golden_result["n_ingredients"] == 4
        assert golden_result["n_flagged"] == 3  # red 40, TiO2, BHT
        assert golden_result["n_banned"] == 0
        assert golden_result["n_not_in_dataset"] == 0
        assert golden_result["n_unverified_identity"] == 1  # water
        assert golden_result["n_unverified"] == 1
        assert golden_result["clean"] is False
        assert golden_result["verdict"] == "FLAGGED"

    def test_water_is_identity_only(self, golden_result):
        water = golden_result["results"][0]
        assert water["ingredient"] == "water"
        assert water["is_in_dataset"] is True
        assert water["determination"] == "unknown"
        assert water["status"] is None
        assert water["rules"] == []

    def test_render_json_is_valid(self, golden_result):
        parsed = json.loads(eu_lint.render_json(golden_result))
        assert parsed["clean"] is False
        assert parsed["verdict"] == "FLAGGED"
        assert parsed["dataset_count"] > 0


# ---------------------------------------------------------------------------
# Known-result cases (fixtures).
# ---------------------------------------------------------------------------
class TestKnownResults:
    def test_us_cereal_southampton_dyes_plus_bht(self, dataset):
        text = (FIXTURES / "us_cereal.txt").read_text()
        result = eu_lint.check_label(text, dataset)
        found = {r["ingredient"]: r for r in result["results"]}
        assert found["red 40"]["determination"] == "restricted"
        assert found["yellow 5"]["determination"] == "restricted"
        assert found["yellow 6"]["determination"] == "restricted"
        assert found["blue 1"]["determination"] == "restricted"
        assert found["bht"]["determination"] == "restricted"
        # Unresolved ingredients (whole grain corn, etc.) -> unverified, not clean.
        assert result["n_unverified"] > 0
        assert result["clean"] is False

    def test_us_sunscreen(self, dataset):
        text = (FIXTURES / "us_sunscreen.txt").read_text()
        result = eu_lint.check_label(text, dataset)
        found = {r["ingredient"]: r for r in result["results"]}
        assert found["avobenzone"]["annex"] == "Annex VI"
        assert found["titanium dioxide"]["annex"] == "Annex IV"
        assert found["octocrylene"]["annex"] == "Annex VI"

    def test_us_lotion(self, dataset):
        text = (FIXTURES / "us_lotion.txt").read_text()
        result = eu_lint.check_label(text, dataset)
        found = {r["ingredient"]: r for r in result["results"]}
        assert found["methylparaben"]["annex"] == "Annex V"
        assert found["propylparaben"]["annex"] == "Annex V"


# ---------------------------------------------------------------------------
# CLI exit codes and formats.
# ---------------------------------------------------------------------------
def _run(*args):
    return subprocess.run(
        [sys.executable, str(ROOT / "eu_lint.py"), *args],
        capture_output=True, text=True,
    )


class TestCLI:
    def test_exit_0_when_no_flag_but_unverified(self):
        # "water, glycerin" are recognised but unverified -> no flag -> exit 0.
        p = _run("water, glycerin", "--format", "json")
        assert p.returncode == 0
        assert json.loads(p.stdout)["verdict"] == "NOT-FULLY-VERIFIED"

    def test_exit_1_on_restricted(self):
        p = _run("red 40", "--format", "json")
        assert p.returncode == 1

    def test_exit_1_on_banned(self):
        p = _run("zinc pyrithione", "--format", "json")
        assert p.returncode == 1
        assert json.loads(p.stdout)["has_error"] is True

    def test_exit_1_on_conditional(self):
        p = _run("methylparaben", "--format", "json")
        assert p.returncode == 1

    def test_not_in_dataset_does_not_set_exit(self):
        p = _run("unicorn tears", "--format", "json")
        assert p.returncode == 0

    def test_csv_format_has_disclaimer(self):
        p = _run("water, glycerin", "--format", "csv")
        assert p.returncode == 0
        lines = p.stdout.strip().splitlines()
        assert lines[0].startswith("ingredient")
        # CSV keeps machine readability: `disclaimer` is a column, and every
        # data row carries the disclaimer value.
        assert "disclaimer" in lines[0].split(",")
        assert DISCLAIMER in lines[1]

    def test_file_input_formats_include_citation(self):
        p = _run("examples/demo_labels/us_cereal.txt", "--format", "json")
        assert p.returncode in (0, 1)
        data = json.loads(p.stdout)
        assert any(r["ingredient"] == "red 40" for r in data["results"])
        assert any(r.get("source_url") for r in data["results"])

    def test_missing_input_exits_2(self):
        p = _run("--format", "json")
        assert p.returncode == 2

    def test_long_pasted_label_is_not_treated_as_filename(self):
        # Regression: `Path(label).is_file()` on a long pasted label raises
        # ENAMETOOLONG on Python 3.12. A value that exceeds the OS path limit
        # cannot be a filename, so it must be handled as a raw label and the
        # CLI must not traceback or emit empty stdout.
        label = ", ".join(
            ["water", "glycerin", "sodium lauryl sulfate", "red 40",
             "methylparaben", "fragrance", "titanium dioxide"]
            * 4
        )
        assert len(label) > 200
        p = _run(label, "--format", "json")
        assert p.returncode in (0, 1)
        assert p.stderr == ""
        data = json.loads(p.stdout)
        assert data["results"]
        # No traceback leaked into stdout; the payload is valid JSON.
        assert "Traceback" not in p.stdout


# ---------------------------------------------------------------------------
# Freshness / provenance honesty (Finding 5).
# ---------------------------------------------------------------------------
class TestFreshness:
    def test_source_urls_differ_by_source_type(self, dataset):
        # Cosmetic vs food rules point to distinct URLs; not all identical.
        cosmetic = {r["source_url"] for s in dataset.substances
                    for r in s.rules if r["annex"] != "Food"}
        food = {r["source_url"] for s in dataset.substances
                for r in s.rules if r["annex"] == "Food"}
        assert cosmetic and food
        assert cosmetic != food

    def test_source_updated_at_not_invented(self, dataset):
        # Curated data must not claim an upstream pub/update date it can't
        # verify. Every rule's source_updated_at must be None (unknown) here.
        for s in dataset.substances:
            for r in s.rules:
                assert r["source_updated_at"] is None

    def test_data_updated_at_is_build_time(self, dataset):
        # data_updated_at is defined as the artifact build time (curated).
        assert dataset.meta["data_updated_at"] == dataset.meta["build_time"]

    def test_substances_deterministic_across_runs(self, tmp_path):
        # Re-running the generator must yield identical substance records.
        # We write directly into tmp_path (never the workspace dataset) via the
        # --output argument; only build_time metadata differs.
        import subprocess as sp
        gen = ROOT / "data" / "scripts" / "build_eu_flags.py"
        first = tmp_path / "a.json"
        second = tmp_path / "b.json"
        for out in (first, second):
            sp.run([sys.executable, str(gen), "--output", str(out)],
                   check=True, capture_output=True, cwd=str(ROOT))
        a = json.loads(first.read_text())
        b = json.loads(second.read_text())
        assert a["substances"] == b["substances"]
        assert len(a["substances"]) == len(b["substances"])


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
