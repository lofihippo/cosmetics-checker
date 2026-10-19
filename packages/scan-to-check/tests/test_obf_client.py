"""Offline tests for the real OpenBeautyFactsClient parsing/validation.


These exercise the ACTUAL client with an injected opener (no network) so the
parsing, barcode preservation, bounded-read, and typed-outcome logic is verified
deterministically. No live product service is hit.
"""

from __future__ import annotations

import json
import io
import sys
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))

import pytest  # noqa: E402

from scan_to_check.obf import (  # noqa: E402
    OpenBeautyFactsClient,
)


class FakeResponse(io.BytesIO):
    def __init__(self, data, status=200):
        super().__init__(data)
        self.status = status

    def read(self, n=-1):
        return super().read(n)


def _client(payload: dict, status=200):
    body = json.dumps(payload).encode()
    def opener(req, timeout):
        return FakeResponse(body, status)
    return OpenBeautyFactsClient(opener=opener)


def _beauty_payload(**over):
    p = {
        "code": "3560070791460",
        "status": "success",
        "result": {"id": "product_found"},
        "product": {
            "product_type": "beauty",
            "product_name": "Fixture Cosmetic",
            "brands": "Fixture Brand",
            "ingredients_text": "AQUA, ALCOHOL",
        },
    }
    p.update(over)
    return p


def test_leading_zero_barcode_preserved_in_lookup_url():
    """The raw barcode (including leading zeros) is passed verbatim to the URL."""
    seen = {}
    barcode = "0012345678901"
    def opener(req, timeout):
        seen["url"] = req.full_url
        return FakeResponse(json.dumps(_beauty_payload()).encode())
    client = OpenBeautyFactsClient(opener=opener)
    res = client.lookup(barcode)
    assert res.status == "found_with_ingredients"
    assert barcode in seen["url"]  # not stripped of leading zeros
    assert res.barcode == barcode


def test_parse_found_with_ingredients():
    res = _client(_beauty_payload()).lookup("3560070791460")
    assert res.status == "found_with_ingredients"
    assert res.product_type == "beauty"
    assert res.ingredients_text == "AQUA, ALCOHOL"
    assert res.name == "Fixture Cosmetic"
    assert res.source_url


def test_parse_carries_provider_country_evidence():
    """Provider-reported country tags are surfaced as market evidence, kept
    separate from any regulatory determination (a tag is not retail proof)."""
    p = _beauty_payload()
    p["product"]["countries_tags"] = ["en:united-states"]
    p["product"]["countries"] = "United States"
    res = _client(p).lookup("0646670548871")
    assert res.countries_tags == ["en:united-states"]
    assert res.countries == "United States"
    # Non-list/string country values must not crash (they are dropped, not coerced).
    p2 = _beauty_payload()
    p2["product"]["countries_tags"] = {"bad": "shape"}
    p2["product"]["countries"] = ["not", "a", "string"]
    res2 = _client(p2).lookup("0646670548871")
    assert res2.countries_tags is None
    assert res2.countries is None


def test_parse_not_found_via_result_id():
    p = _beauty_payload(status="success")
    p["result"] = {"id": "product_not_found"}
    p["product"] = None
    res = _client(p).lookup("0000000000000")
    assert res.status == "not_found"


def test_parse_found_without_ingredients():
    p = _beauty_payload()
    p["product"]["ingredients_text"] = ""
    res = _client(p).lookup("3560070791460")
    assert res.status == "found_without_ingredients"


def test_parse_unknown_product_type_unsupported():
    p = _beauty_payload()
    p["product"]["product_type"] = "gadget"
    res = _client(p).lookup("3560070791460")
    assert res.status == "unsupported_product_category"
    assert res.product_type == "gadget"


def test_parse_food_is_unsupported_for_cosmetic():
    p = _beauty_payload()
    p["product"]["product_type"] = "food"
    res = _client(p).lookup("3017620422003")
    # food is a known type but NOT cosmetic; cosmetic rules must not apply.
    assert res.status == "unsupported_product_category"
    assert res.product_type == "food"


def test_parse_malformed_json_is_malformed_response():
    client = OpenBeautyFactsClient(opener=lambda req, t: FakeResponse(b"{ not json"))
    res = client.lookup("3560070791460")
    assert res.status == "malformed_response"


def test_upstream_http_error_is_upstream_error():
    client = OpenBeautyFactsClient(opener=lambda req, t: (_ for _ in ()).throw(
        urllib.error.HTTPError("u", 500, "err", {}, None)))
    res = client.lookup("3560070791460")
    assert res.status == "upstream_error"
    assert "500" in (res.error or "")


def test_validate_barcode_rejects_malformed():
    from scan_to_check.obf import validate_barcode
    for bad in ("", "abc", "123", "12345678901234567"):
        with pytest.raises(ValueError):
            validate_barcode(bad)


# --- Upstream shape validation -------------------------------------------

def _lookup(payload, barcode="3560070791460"):
    body = json.dumps(payload).encode()
    client = OpenBeautyFactsClient(
        opener=lambda req, t: FakeResponse(body))
    return client.lookup(barcode)


def test_array_root_is_malformed_not_notfound():
    """A JSON array root must be a typed malformed outcome, not not-found."""
    res = _lookup([])
    assert res.status == "malformed_response"
    assert "object" in (res.error or "")


def test_product_array_is_malformed_not_attribute_error():
    """product:[1] must not raise AttributeError; returns malformed."""
    res = _lookup({"status": "success", "result": {"id": "product_found"},
                   "product": [1]})
    assert res.status == "malformed_response"
    assert "product" in (res.error or "")


def test_empty_object_without_not_found_id_is_malformed():
    """``{}`` is malformed (no documented not-found id), not silently not-found."""
    res = _lookup({})
    assert res.status == "malformed_response"


def test_ingredients_array_is_not_coerced_into_label():
    """An array-valued ingredients_text must not become the fake label ``['Aqua']``."""
    p = _beauty_payload()
    p["product"]["ingredients_text"] = ["Aqua", "ALCOHOL"]
    res = _lookup(p)
    assert res.status == "malformed_response"
    assert res.ingredients_text is None


def test_ingredients_dict_is_not_coerced_into_label():
    p = _beauty_payload()
    p["product"]["ingredients_text"] = {"raw": "Aqua"}
    res = _lookup(p)
    assert res.status == "malformed_response"
    assert res.ingredients_text is None


def test_non_string_display_fields_are_malformed():
    for field in ("product_name", "brands", "product_type"):
        p = _beauty_payload()
        p["product"][field] = 123
        res = _lookup(p)
        assert res.status == "malformed_response", field
        assert field in (res.error or ""), field


def test_field_missing_optional_is_ok():
    p = _beauty_payload()
    del p["product"]["brands"]
    res = _lookup(p)
    assert res.status == "found_with_ingredients"
    assert res.brands is None


def test_bounded_read_uses_configured_limit_and_closes():
    """A response larger than the configured bound -> malformed, and closed."""
    class BigResp(FakeResponse):
        def __init__(self):
            super().__init__(b"x" * 100)
            self._was_closed = False
        def close(self):
            self._was_closed = True
            super().close()

    r = BigResp()
    client = OpenBeautyFactsClient(
        max_response_bytes=50, opener=lambda req, t: r)
    res = client.lookup("3560070791460")
    assert res.status == "malformed_response"
    assert "size limit" in (res.error or "")
    assert r._was_closed


def test_read_time_error_is_malformed_not_crash():
    class FailResp:
        def __init__(self):
            self.closed = False
        def read(self, n=-1):
            raise OSError("io failure")
        def close(self):
            self.closed = True

    r = FailResp()
    client = OpenBeautyFactsClient(opener=lambda req, t: r)
    res = client.lookup("3560070791460")
    assert res.status == "malformed_response"
    assert r.closed


def test_unknown_status_is_malformed_not_notfound():
    res = _lookup({"status": "weird", "result": {"id": "product_found"}})
    assert res.status == "malformed_response"


def test_contact_user_agent_no_fake_contact():
    from scan_to_check.obf import CONTACT_ENV
    # No contact configured -> neutral UA, never a fabricated address.
    client = OpenBeautyFactsClient(opener=lambda req, t: FakeResponse(b"{}"))
    assert "invalid" not in client.user_agent
    assert client.user_agent.startswith("scan-to-check/0.1")
    # Configurable contact via env is honoured.
    import os
    old = os.environ.get(CONTACT_ENV)
    try:
        os.environ[CONTACT_ENV] = "ops@example.org"
        client2 = OpenBeautyFactsClient(opener=lambda req, t: FakeResponse(b"{}"))
        assert "ops@example.org" in client2.user_agent
    finally:
        if old is None:
            os.environ.pop(CONTACT_ENV, None)
        else:
            os.environ[CONTACT_ENV] = old
