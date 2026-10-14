"""Shared fixtures for scan-to-check frontend (Playwright) tests.

Builds a real Uvicorn app from an in-memory registry (no workspace snapshot),
with an injectable mock product-lookup client so no network is hit. Mirrors the
eu-reg-api frontend harness pattern; reuse, not a second testing system.
"""
from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages" / "eu-reg-lists"))
sys.path.insert(0, str(ROOT / "packages" / "eu-ingredient-linter"))
sys.path.insert(0, str(ROOT / "packages" / "scan-to-check"))

import eu_reg_lists as erl  # noqa: E402
from scan_to_check.app import create_app  # noqa: E402
from scan_to_check.obf import ProductResult  # noqa: E402

DATA_UPDATED_AT = "2026-10-08T14:35:34.921319+00:00"


def make_registry():
    """A small in-memory registry with a banned substance + a linked rule."""
    raw = {
        "source_kind": "cosing-annex-ii",
        "source_file": "COSING_Annex_II_v2.txt",
        "source_sha256": "abc",
        "row_reference": "COSING_Annex_II_v2.txt#49",
        "entry_number": 49,
        "reference_number": 47,
        "official_reference": 47,
        "accepted": True,
        "retrieved_at": DATA_UPDATED_AT,
    }
    benzene = erl.Substance(
        id="benzene", name="Benzene", aliases=["benzol"], cas="71-43-2",
        ec="200-753-7", determination="banned",
        rules=[erl.Rule(status="banned", annex="Annex II",
                        regulation="(EC) 2009/1223",
                        source_url="https://ec.europa.eu/growth/tools-databases/cosing/",
                        source_updated_at=None, resolution="cosing-annex-ii", raw=raw)],
        sources=["cosing-annex-ii"],
    )
    water = erl.Substance(id="water", name="Aqua", aliases=["water"],
                          cas="7732-18-5", ec="231-791-2",
                          determination="unknown", rules=[])
    zinc = erl.Substance(
        id="zinc-pyrithione", name="Zinc pyrithione", aliases=["zinc  pyrithione"],
        cas="13463-41-7", ec="236-671-3", determination="banned",
        rules=[erl.Rule(status="banned", annex="Annex II",
                        regulation="(EU) 2021/1902",
                        source_url="https://ec.europa.eu/growth/tools-databases/cosing/",
                        source_updated_at=None, resolution="cosing-annex-ii",
                        raw={"source_kind": "cosing-annex-ii",
                             "source_file": "COSING_Annex_II_v2.txt",
                             "source_sha256": "abc",
                             "row_reference": "COSING_Annex_II_v2.txt#1",
                             "official_reference": 1, "accepted": True,
                             "retrieved_at": DATA_UPDATED_AT})],
        sources=["cosing-annex-ii"],
    )
    # Sodium benzoate: known identity; its group (Annex V) rule is surfaced as
    # linked evidence by the real checker via membership resolution.
    sodium = erl.Substance(
        id="sodium-benzoate", name="Sodium benzoate",
        determination="allowed_with_conditions", rules=[],
        sources=["cosing-annex-v"],
        member_of=[{
            "group_id": "benzoate-group", "group_name": "Benzoic acid and its salts",
            "annex": "Annex V", "official_reference": 1,
        }],
    )
    benzoate_group = erl.Substance(
        id="benzoate-group", name="benzonic-acid", determination="allowed_with_conditions",
        rules=[erl.Rule(status="allowed_with_conditions", annex="Annex V",
                        regulation="(EU) 2023/1492",
                        source_url="https://ec.europa.eu/growth/tools-databases/cosing/",
                        source_updated_at=None, resolution="cosing-annex-v",
                        conditions="Maximum concentration 0.5%",
                        raw={
                            "source_kind": "cosing-annex-v",
                            "source_file": "COSING_Annex_V_v2.csv",
                            "source_sha256": "def",
                            "row_reference": "COSING_Annex_V_v2.csv#1",
                            "official_reference": 1,
                            "accepted": True,
                            "retrieved_at": DATA_UPDATED_AT,
                            "conditions_list": [{"field": "max_concentration",
                                                 "label": "Maximum concentration",
                                                 "value": "0.5%"}],
                        })],
        sources=["cosing-annex-v"],
    )
    # A scoped Annex II prohibition (Hydroquinone-like): source scope/exception
    # qualifier -> shared checker classifies as scope_unresolved, never an
    # unconditional "banned" headline.
    hydroq = erl.Substance(
        id="hydroquinone", name="Hydroquinone", cas="123-31-9", ec="204-617-8",
        determination="banned",
        rules=[erl.Rule(status="banned", annex="Annex II",
                        regulation="(EC) 2009/1223",
                        source_url="https://ec.europa.eu/growth/tools-databases/cosing/",
                        source_updated_at=None, resolution="cosing-annex-ii",
                        conditions=None,
                        raw={"source_kind": "cosing-annex-ii",
                             "source_file": "COSING_Annex_II_v2.txt",
                             "source_sha256": "abc",
                             "row_reference": "COSING_Annex_II_v2.txt#1339",
                             "entry_number": 1339,
                             "reference_number": 1339,
                             "official_reference": 1339,
                             "accepted": True,
                             "retrieved_at": DATA_UPDATED_AT,
                             "conditions_list": None,
                             "source_row": {
                                 "Chemical name / INN":
                                     "Hydroquinone with the exception of entry 14"},
                             })],
        sources=["cosing-annex-ii"],
    )
    verified_sources = {
        "cosing-annex-ii": {"kind": "cosing-annex", "present": True,
                            "accepted": True, "fetched": True,
                            "retrieval_error": None},
        "cosing-annex-v": {"kind": "cosing-annex", "present": True,
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
        "count_substances": 5, "count_verified_rules": 3,
        "count_identity_only": 1, "count_quarantined": 0,
        "verified_sources": verified_sources,
        "snapshot": verified_sources,
    }
    reg = erl.Registry([benzene, zinc, water, sodium, benzoate_group, hydroq],
                       data_updated_at=DATA_UPDATED_AT,
                       verified_sources=verified_sources, metadata=metadata)
    return reg, metadata


class MockClient:
    """Deterministic lookup client returning prebuilt ProductResults."""

    def __init__(self, results):
        self.results = results or {}
        self.calls = []

    def lookup(self, barcode):
        self.calls.append(barcode)
        return self.results.get(barcode, ProductResult(
            status="not_found", barcode=barcode,
            disclaimer="Regulatory information, not legal advice."))


def _beauty(barcode, ingredients, name="Fixture Cosmetic", brands="Fixture Brand"):
    return ProductResult(
        status="found_with_ingredients", barcode=barcode,
        product_type="beauty", name=name, brands=brands,
        ingredients_text=ingredients,
        source_url=f"https://world.openfoodfacts.org/product/{barcode}",
        retrieved_at="2026-01-01T00:00:00+00:00",
        disclaimer="Regulatory information, not legal advice.")


@pytest.fixture(scope="module")
def server():
    reg, metadata = make_registry()
    client = MockClient({})
    app = create_app(reg, client=client)
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(50):
        try:
            import urllib.request
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=0.2)
            break
        except Exception:
            time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser, server):
    p = browser.new_page()
    yield p
    p.close()


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port
