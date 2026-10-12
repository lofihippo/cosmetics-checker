"""Tests for the eu-reg-lists package.

Covers: Registry load (SQLite/JSONL), search by CAS/INCI/E-number/alias,
honest unknown semantics, CheckResult completeness, collision detection
(ambiguous alias + canonical name), and the normalized pipeline outputs.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]  # repo root
PACKAGE = ROOT / "packages" / "eu-reg-lists"
sys.path.insert(0, str(PACKAGE))
sys.path.insert(0, str(ROOT))

import eu_reg_lists as erl  # noqa: E402
from eu_reg_lists import Rule, Substance  # noqa: E402

PROCESSED = ROOT / "data" / "processed"
PROCESSED_SQLITE = PROCESSED / "eu_reg_lists.sqlite"
PROCESSED_JSONL = PROCESSED / "eu_reg_lists.jsonl"


def _sample_substances():
    return [
        Substance(
            id="red-40", name="Red 40", aliases=["allura red", "e129", "fd&c red 40"],
            cas="25956-17-6", ec="247-368-0", e_number="E129", ci="16035",
            determination="restricted",
            rules=[Rule(status="restricted", annex="Food",
                        regulation="Reg (EC) 1333/2008",
                        source_url="https://example/food",
                        source_updated_at="2022-01-01",
                        conditions="food colour cap", resolution="food-colour")],
        ),
        Substance(
            id="water", name="Water", aliases=["aqua", "eau"],
            cas="7732-18-5", ec="231-791-2", determination="unknown", rules=[],
        ),
        Substance(
            id="sodium-benzoate", name="Sodium benzoate", aliases=["e211"],
            cas="532-32-1", ec="208-534-8", e_number="E211",
            determination="restricted",
            rules=[
                Rule(status="allowed_with_conditions", annex="Annex V",
                     regulation="Reg (EC) 1223/2009",
                     source_url="https://example/cosmetic",
                     source_updated_at="2022-01-01",
                     conditions="cosmetic cap", resolution="cosmetic-preservative"),
                Rule(status="restricted", annex="Food",
                     regulation="Reg (EC) 1333/2008",
                     source_url="https://example/food",
                     source_updated_at="2022-01-01",
                     conditions="food cap", resolution="food-additive"),
            ],
        ),
    ]


class TestRegistry:
    def test_lookup_by_name_and_alias(self):
        reg = erl.Registry(_sample_substances())
        assert reg.lookup("Red 40").id == "red-40"
        assert reg.lookup("allura red").id == "red-40"
        assert reg.lookup("e129").id == "red-40"

    def test_lookup_punctuation_alias(self):
        reg = erl.Registry(_sample_substances())
        assert reg.lookup("FD&C Red 40").id == "red-40"

    def test_lookup_by_cas_and_ec_identifier(self):
        reg = erl.Registry(_sample_substances())
        # Exact CAS resolves directly (not just via search).
        assert reg.lookup("25956-17-6").id == "red-40"
        assert reg.lookup("532-32-1").id == "sodium-benzoate"
        # Exact EC resolves too.
        assert reg.lookup("247-368-0").id == "red-40"
        assert reg.lookup("231-791-2").id == "water"

    def test_search_by_cas_inci_ename(self):
        reg = erl.Registry(_sample_substances())
        assert reg.search("25956-17-6")[0].id == "red-40"
        assert reg.search("Red 40")[0].id == "red-40"
        assert reg.search("E129")[0].id == "red-40"

    def test_unknown_identity_has_no_rule(self):
        reg = erl.Registry(_sample_substances())
        water = reg.lookup("water")
        assert water.determination == "unknown"
        assert water.rules == []
        assert water.has_verified_rule is False

    def test_multi_rule_substance_retains_both(self):
        reg = erl.Registry(_sample_substances())
        sub = reg.lookup("e211")
        annexes = {r.annex for r in sub.rules}
        assert annexes == {"Annex V", "Food"}
        # strongest rule surfaced as headline
        assert sub.strongest_rule.status == "restricted"

    def test_ambiguous_alias_rejected(self):
        subs = _sample_substances() + [
            Substance(id="other", name="E129", aliases=["solo"],
                      cas="1-1-1", determination="banned",
                      rules=[])  # canonical name clashes with an alias of red-40
        ]
        reg = erl.Registry(subs)
        assert "e129" in reg.ambiguous_keys
        assert reg.lookup("e129") is None

    def test_canonical_name_collision_rejected(self):
        base = _sample_substances()
        a = Substance(id="a", name="Collide", aliases=["solo-a"], determination="restricted")
        b = Substance(id="b", name="Collide", aliases=["solo-b"], determination="banned")
        reg = erl.Registry(base + [a, b])
        assert "collide" in reg.ambiguous_keys
        assert reg.lookup("collide") is None
        assert reg.lookup("solo-a").id == "a"
        assert reg.lookup("solo-b").id == "b"


class TestChecker:
    def test_check_unknown_token(self):
        reg = erl.Registry(_sample_substances())
        r = erl.check_token("zzz", reg, data_updated_at="2022-01-01")
        assert r.is_in_dataset is False
        assert r.determination == "unknown"
        assert r.status is None

    def test_check_recognised_unverified_not_clean(self):
        reg = erl.Registry(_sample_substances())
        r = erl.check_token("water", reg, data_updated_at="2022-01-01")
        assert r.is_in_dataset is True
        assert r.determination == "unknown"
        assert r.status is None
        # released as a label -> NOT clean (unresolved determination)
        summary = erl.check_label("water", reg, data_updated_at="2022-01-01")
        assert summary["clean"] is False
        assert summary["verdict"] == "NOT-FULLY-VERIFIED"
        assert summary["n_unverified"] == 1

    def test_check_verified_flag_has_full_citation(self):
        reg = erl.Registry(_sample_substances())
        r = erl.check_token("red 40", reg, data_updated_at="2022-01-01")
        assert r.status == "restricted"
        assert r.regulation
        assert r.source_url
        assert r.data_updated_at == "2022-01-01"
        assert r.rules[0].resolution  # explicit resolution method

    def test_check_multi_rule_all_retained(self):
        reg = erl.Registry(_sample_substances())
        r = erl.check_token("e211", reg, data_updated_at="2022-01-01")
        assert len(r.rules) == 2
        annexes = {x.annex for x in r.rules}
        assert annexes == {"Annex V", "Food"}

    def test_check_token_defaults_to_registry_timestamp(self):
        reg = erl.Registry(_sample_substances(), data_updated_at="2022-06-01T00:00:00Z")
        # omitting data_updated_at must NOT give None; it uses the registry's.
        r = erl.check_token("red 40", reg)
        assert r.data_updated_at == "2022-06-01T00:00:00Z"
        r2 = erl.check_token("unknown zz", reg)
        assert r2.data_updated_at == "2022-06-01T00:00:00Z"


class TestPersistence:
    def test_jsonl_roundtrip(self, tmp_path):
        reg = erl.Registry(_sample_substances())
        lines = [json.dumps(s.to_dict()) for s in reg.substances]
        p = tmp_path / "reg.jsonl"
        p.write_text("\n".join(lines) + "\n")
        reg2 = erl.Registry.from_jsonl_metadata(p, {"data_updated_at": "2022-01-01"})
        assert len(reg2) == len(reg)
        assert reg2.lookup("red 40").determination == "restricted"
        assert reg2.lookup("e211").rules[1].annex == "Food"

    def test_sqlite_roundtrip(self, tmp_path):
        reg = erl.Registry(_sample_substances())
        p = tmp_path / "reg.db"
        con = sqlite3.connect(str(p))
        con.executescript("""
            CREATE TABLE substances (id TEXT PRIMARY KEY, name TEXT, aliases TEXT,
                cas TEXT, ec TEXT, e_number TEXT, ci TEXT, determination TEXT, sources TEXT);
            CREATE TABLE rules (substance_id TEXT, status TEXT, annex TEXT,
                regulation TEXT, source_url TEXT, source_updated_at TEXT,
                conditions TEXT, resolution TEXT, effective_date TEXT, raw TEXT);
            CREATE TABLE metadata (k TEXT PRIMARY KEY, v TEXT);
        """)
        for s in reg.substances:
            con.execute("INSERT INTO substances VALUES (?,?,?,?,?,?,?,?,?)",
                        (s.id, s.name, json.dumps(s.aliases), s.cas, s.ec,
                         s.e_number, s.ci, s.determination, "[]"))
            for r in s.rules:
                con.execute("INSERT INTO rules VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (s.id, r.status, r.annex, r.regulation, r.source_url,
                             r.source_updated_at, r.conditions, r.resolution,
                             r.effective_date, "{}"))
        con.execute("INSERT INTO metadata VALUES (?,?)",
                    ("data_updated_at", "2022-01-01"))
        con.commit()
        con.close()
        reg2 = erl.Registry.from_sqlite(p)
        assert len(reg2) == len(reg)
        assert reg2.lookup("e129").id == "red-40"
        assert reg2.lookup("e211").rules[0].annex == "Annex V"

    def test_sqlite_metadata_json_decoded(self, tmp_path):
        # Metadata values stored as JSON strings must come back decoded (no
        # literal quotes), and read/reg JSONL must agree.
        reg = erl.Registry(_sample_substances())
        p = tmp_path / "reg.db"
        con = sqlite3.connect(str(p))
        con.executescript("""
            CREATE TABLE substances (id TEXT PRIMARY KEY, name TEXT, aliases TEXT,
                cas TEXT, ec TEXT, e_number TEXT, ci TEXT, determination TEXT, sources TEXT);
            CREATE TABLE rules (substance_id TEXT, status TEXT, annex TEXT,
                regulation TEXT, source_url TEXT, source_updated_at TEXT,
                conditions TEXT, resolution TEXT, effective_date TEXT, raw TEXT);
            CREATE TABLE metadata (k TEXT PRIMARY KEY, v TEXT);
        """)
        con.execute("INSERT INTO substances VALUES (?,?,?,?,?,?,?,?,?)",
                    ("red-40", "Red 40", "[]", "25956-17-6", "247-368-0",
                     "E129", "16035", "restricted", "[]"))
        con.execute("INSERT INTO metadata VALUES (?,?)",
                    ("data_updated_at", json.dumps('2022-06-01T00:00:00Z')))
        con.commit()
        con.close()
        reg2 = erl.Registry.from_sqlite(p)
        assert reg2.data_updated_at == "2022-06-01T00:00:00Z"
        assert not reg2.data_updated_at.startswith('"')  # no literal quotes

    def test_jsonl_parity_with_sqlite_metadata(self, tmp_path):
        # JSONL + SQLite loaders must surface the same data_updated_at.
        reg = erl.Registry(_sample_substances(), data_updated_at="2022-06-01T00:00:00Z")
        jl = tmp_path / "reg.jsonl"
        jl.write_text("\n".join(json.dumps(s.to_dict()) for s in reg.substances) + "\n")
        reg_j = erl.Registry.from_jsonl_metadata(jl, {"data_updated_at": "2022-06-01T00:00:00Z"})
        assert reg_j.data_updated_at == reg.data_updated_at


class TestNormalizedPipeline:
    def test_processed_metadata_consistent(self):
        assert PROCESSED_SQLITE.exists()
        assert PROCESSED_JSONL.exists()
        meta = json.loads((PROCESSED / "metadata.json").read_text())
        assert meta["count_substances"] > 0
        assert meta["count_identity_only"] + meta["count_verified_rules"] == \
            meta["count_substances"]
        assert "data_updated_at" in meta
        assert "reconcile" in meta
        assert "count_quarantined" in meta

    def test_processed_jsonl_consistent_with_metadata(self):
        subs = [json.loads(l) for l in PROCESSED_JSONL.read_text().splitlines() if l.strip()]
        assert len(subs) > 0
        meta = json.loads((PROCESSED / "metadata.json").read_text())
        n_verified = sum(1 for s in subs if s["determination"] != "unknown")
        assert n_verified == meta["count_verified_rules"]
        n_identity = sum(1 for s in subs if s["determination"] == "unknown")
        assert n_identity == meta["count_identity_only"]
        # no substance may carry rules without a verified determination
        for s in subs:
            if s["determination"] == "unknown":
                assert s["rules"] == []

    def test_processed_sqlite_loadable(self):
        reg = erl.load(PROCESSED_SQLITE)
        assert len(reg) == json.loads((PROCESSED / "metadata.json").read_text())["count_substances"]
        hit = reg.search("zinc  pyrithione")
        assert isinstance(hit, list)

    def test_processed_sources_provenance(self):
        meta = json.loads((PROCESSED / "metadata.json").read_text())["verified_sources"]
        for key, info in meta.items():
            assert "kind" in info
            assert "present" in info
            assert "accepted" in info
            if info["present"]:
                assert info["files"], f"source {key} present but no hash recorded"
                for _, h in info["files"].items():
                    assert len(h["sha256"]) == 64  # sha256 hex

    def test_each_verified_rule_has_traceable_provenance(self, tmp_path):
        # A rule parsed from a real CosIng export (annex declared in the preamble)
        # must carry the source file + hash + row.
        import hashlib as _h
        content = ('"File creation date: 08/10/2026"\n'
                   '"ANNEX II","Last update: 06/10/2026"\n'
                   '"LIST OF SUBSTANCES PROHIBITED IN COSMETIC PRODUCTS"\n'
                   '"Substance identification"\n'
                   '"Reference Number","Chemical name / INN","CAS Number",'
                   '"EC Number",Regulation,"Other Directives/Regulations",'
                   '"SCCS opinions","Chemical/IUPAC Name",'
                   '"Identified INGREDIENTS or substances e.g.",CMR,"Update Date"\n'
                   '1,"Zinc pyrithione",13463-41-7,236-671-3,"(EU) 2021/1902",'
                   ',,,,,15/10/2010\n')
        (tmp_path / "cosing-annex-ii").mkdir()
        (tmp_path / "cosing-annex-ii" / "COSING_Annex_II_v2.txt").write_text(content)
        (tmp_path / "fetch_manifest.json").write_text(json.dumps({
            "cosing-annex-ii": {
                "fetched": True, "accepted": True,
                "filename": "COSING_Annex_II_v2.txt",
                "sha256": _h.sha256(content.encode()).hexdigest(),
                "retrieved_at": "2026-01-01T00:00:00+00:00",
            }
        }))
        import sys as _sys
        _sys.path.insert(0, str(ROOT / "data" / "scripts"))
        import normalize
        out = tmp_path / "out"
        normalize.build_processed(output_dir=out, inputs_dir=tmp_path)
        subs = [json.loads(l) for l in (out / "eu_reg_lists.jsonl")
                .read_text().splitlines() if l.strip()]
        zp = [s for s in subs if s["name"] == "Zinc pyrithione"][0]
        rule = zp["rules"][0]
        # provenance lives in rule["raw"] (file, hash, row, retrieval).
        raw = rule["raw"]
        assert raw["source_sha256"]  # file hash present
        assert raw["source_file"] == "COSING_Annex_II_v2.txt"
        assert raw["row_reference"].startswith("COSING_Annex_II_v2.txt#1")
        assert raw["accepted"] is True
        assert rule["resolution"].startswith("cosing")
