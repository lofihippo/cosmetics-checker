"""Shared fixtures for eu-reg-api tests.

The in-memory registry used by unit tests lives here so both the API tests and
the frontend (Playwright) tests can build an app from the same deterministic
snapshot without touching the workspace dataset.
"""
from __future__ import annotations

import eu_reg_lists as erl
import pytest

DATA_UPDATED_AT = "2026-10-08T14:35:34.921319+00:00"
RETRIEVED_AT = "2026-10-08T14:35:34.921319+00:00"


def make_registry():
    """A small in-memory Annex-II registry (Benzene, Aqua, 1,4-dioxane)."""
    raw = {
        "source_kind": "cosing-annex-ii",
        "source_file": "COSING_Annex_II_v2.txt",
        "source_sha256": "abc",
        "row_reference": "COSING_Annex_II_v2.txt#49",
        "entry_number": 49,
        "reference_number": 47,
        "official_reference": 47,
        "accepted": True,
        "retrieved_at": RETRIEVED_AT,
    }
    benzene = erl.Substance(
        id="benzene", name="Benzene", aliases=["benzol"], cas="71-43-2",
        ec="200-753-7", determination="banned",
        rules=[erl.Rule(status="banned", annex="Annex II",
                        regulation="(EC) 2009/1223",
                        source_url="https://ec.europa.eu/growth/tools-databases/cosing/",
                        source_updated_at=None,
                        resolution="cosing-annex-ii", raw=raw)],
        sources=["cosing-annex-ii"],
    )
    water = erl.Substance(id="water", name="Aqua", aliases=["water"],
                          cas="7732-18-5", ec="231-791-2",
                          determination="unknown", rules=[])
    dioxane = erl.Substance(
        id="1-4-dioxane", name="1,4-dioxane", aliases=["1,4-dioxane", "dioxane"],
        cas="123-91-1", ec="204-661-8", determination="banned",
        rules=[erl.Rule(status="banned", annex="Annex II",
                        regulation="(EC) 2009/1223",
                        source_url="https://ec.europa.eu/growth/tools-databases/cosing/",
                        source_updated_at=None, resolution="cosing-annex-ii",
                        raw={"source_kind": "cosing-annex-ii",
                             "source_file": "COSING_Annex_II_v2.txt",
                             "source_sha256": "abc",
                             "row_reference": "COSING_Annex_II_v2.txt#1234",
                             "entry_number": 1234,
                             "reference_number": 1234,
                             "official_reference": 1234,
                             "accepted": True,
                             "retrieved_at": RETRIEVED_AT})],
        sources=["cosing-annex-ii"],
    )
    zno = erl.Substance(
        id="zinc-oxide", name="Zinc oxide", cas="1314-13-2", ci="CI 77947",
        determination="allowed_with_conditions",
        rules=[erl.Rule(status="allowed_with_conditions", annex="Annex IV",
                        regulation="(EU) 2017/1413",
                        source_url="https://ec.europa.eu/growth/tools-databases/cosing/",
                        source_updated_at=None, resolution="cosing-annex-iv",
                        conditions="Maximum concentration in ready for use preparation: 25%",
                        raw={"source_kind": "cosing-annex-iv",
                             "source_file": "COSING_Annex_IV_v2.csv",
                             "source_sha256": "abc",
                             "row_reference": "COSING_Annex_IV_v2.csv#145",
                             "entry_number": 145, "reference_number": 144,
                             "official_reference": 144, "accepted": True,
                             "retrieved_at": RETRIEVED_AT,
                             "conditions_list": [{"field": "max_concentration",
                                                  "label": "Maximum concentration in ready for use preparation",
                                                  "value": "25%"}]}),
               erl.Rule(status="allowed_with_conditions", annex="Annex VI",
                        regulation="(EU) 2016/621",
                        source_url="https://ec.europa.eu/growth/tools-databases/cosing/",
                        source_updated_at=None, resolution="cosing-annex-vi",
                        conditions="Maximum concentration in ready for use preparation: 25%",
                        raw={"source_kind": "cosing-annex-vi",
                             "source_file": "COSING_Annex_VI_v2.csv",
                             "source_sha256": "abc",
                             "row_reference": "COSING_Annex_VI_v2.csv#29",
                             "entry_number": 29, "reference_number": "30a",
                             "official_reference": "30a", "accepted": True,
                             "retrieved_at": RETRIEVED_AT,
                             "conditions_list": [{"field": "max_concentration",
                                                  "label": "Maximum concentration in ready for use preparation",
                                                  "value": "25%"}]})],
        sources=["cosing-annex-iv", "cosing-annex-vi"],
    )
    verified_sources = {
        "cosing-annex-ii": {"kind": "cosing-annex", "present": True,
                            "accepted": True, "fetched": True,
                            "retrieval_error": None},
        "cosing-annex-iv": {"kind": "cosing-annex", "present": True,
                            "accepted": True, "fetched": True,
                            "retrieval_error": None},
        "cosing-annex-vi": {"kind": "cosing-annex", "present": True,
                            "accepted": True, "fetched": True,
                            "retrieval_error": None},
        "echa-svhc": {"kind": "echa-svhc", "present": False,
                      "accepted": False, "fetched": False,
                      "retrieval_error": "HTTPError"},
    }
    metadata = {
        "title": "test", "version": "0.2.0",
        "data_updated_at": DATA_UPDATED_AT,
        "content_hash": "testhash1234",
        "count_substances": 4, "count_verified_rules": 3,
        "count_identity_only": 1, "count_quarantined": 0,
        "verified_sources": verified_sources,
        "snapshot": verified_sources,
    }
    reg = erl.Registry([benzene, water, dioxane, zno],
                       data_updated_at=DATA_UPDATED_AT,
                       verified_sources=verified_sources, metadata=metadata)
    return reg, metadata


@pytest.fixture(scope="module")
def registry():
    return make_registry()
