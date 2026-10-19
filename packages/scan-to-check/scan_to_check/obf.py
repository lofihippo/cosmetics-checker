"""Open Beauty Facts (and cross-database) barcode lookup adapter.


This is a small, bounded adapter for the Open Food Facts / Open Beauty Facts read
API (v3). It performs NO regulatory matching and NO ingredient evaluation — it
only fetches a product's declared ingredients and provenance from the public
product database. Product identity/provenance is kept separate from the
regulatory registry provenance.

Data license / attribution: Open Food Facts / Open Beauty Facts data is provided
under the Open Database License (ODbL) 1.0; the UI must attribute the
Open Food Facts / Open Beauty Facts project and link back to the source product
page and data license. See ``LICENSES.md``. This adapter makes no write to the
product services.

Bounded / honest contract:
  * A fixed, trusted endpoint (``world.openfoodfacts.org`` v3 with
    ``product_type=all``, which canonicalises a barcode to the correct instance
    -- beauty/food/petfood/product) is used; no arbitrary URL fetching.
  * Requests have a timeout and a bounded read size; the client is injectable so
    tests never hit the network.
  * Raw barcodes are PRESERVED as strings (leading zeros included) and passed to
    the upstream verbatim. The upstream normalises them; the adapter never strips
    leading zeros itself.
  * Malformed input (empty / non-ASCII / wrong length) is rejected up front.
  * Outcomes are typed: ``found_with_ingredients``, ``found_without_ingredients``,
    ``not_found``, ``unsupported_product_category``, ``malformed_response``,
    ``upstream_error``. A transient upstream failure NEVER yields a product.
  * Cosmetic conclusions are only emitted for ``product_type == beauty``; food /
    petfood / other are explicitly excluded from cosmetic checking (deferred).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

DISCLAIMER = "Regulatory information, not legal advice."
DEFAULT_BASE = "https://world.openfoodfacts.org"
PRODUCT_TYPE = "all"
# Honest, configurable application identification. No fake contact is invented:
# the operator may set SCAN_TO_CHECK_CONTACT (or pass ``contact=``) to disclose a
# real contact, otherwise a neutral project User-Agent is used.
CONTACT_ENV = "SCAN_TO_CHECK_CONTACT"
DEFAULT_USER_AGENT = ("scan-to-check/0.1 "
                      "(cosmetics-checker local product-data lookup)")
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
TIMEOUT_SECONDS = 12
# Cosmetic = Open Beauty Facts product_type == "beauty". Food/petfood/product are
# not cosmetics and are not checked with cosmetic rules.
COSMETIC_PRODUCT_TYPE = "beauty"
# A product is "cosmetic" only when the upstream product_type is beauty. We do NOT
# infer cosmetic status from ingredient text or category names.
KNOWN_PRODUCT_TYPES = {"beauty", "food", "petfood", "product"}
# Barcode length per the upstream Barcode Normalization reference (EAN-8/13,
# UPC-A/12, UPC-E/8, GTIN-14). We keep it as a string; a malformed length is
# rejected but trailing/leading zeros are preserved.
BARCODE_LENGTHS = (8, 12, 13, 14)


class LookupError(Exception):
    """A bounded, typed lookup failure."""


@dataclass
class ProductResult:
    status: str  # outcome enum value
    barcode: str
    product_type: Optional[str] = None
    name: Optional[str] = None
    brands: Optional[str] = None
    ingredients_text: Optional[str] = None
    source_url: Optional[str] = None
    data_updated_at: Optional[str] = None
    retrieved_at: Optional[str] = None
    # Provider-reported market evidence (crowd-sourced tags), kept separate from
    # any regulatory determination. A country tag is NOT independently confirmed
    # retail availability; it is surfaced for attribution/market context only.
    countries_tags: Optional[list] = None
    countries: Optional[str] = None
    raw: Optional[dict] = field(default=None, repr=False)
    error: Optional[str] = None
    disclaimer: str = DISCLAIMER

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "barcode": self.barcode,
            "product_type": self.product_type,
            "name": self.name,
            "brands": self.brands,
            "ingredients_text": self.ingredients_text,
            "source_url": self.source_url,
            "data_updated_at": self.data_updated_at,
            "retrieved_at": self.retrieved_at,
            "countries_tags": self.countries_tags,
            "countries": self.countries,
            "error": self.error,
            "disclaimer": DISCLAIMER,
        }


def _now_iso() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _bounded_read(resp: Any, max_bytes: int = MAX_RESPONSE_BYTES) -> bytes:
    """Read at most ``max_bytes`` from the response stream.

    Always closes the response in a ``finally`` so a partial read never leaks a
    file/socket handle. A read-time error (e.g. ``OSError`` during ``read``) is
    surfaced as a bounded failure.
    """
    try:
        data = resp.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise LookupError("upstream response exceeded size limit")
        return data
    finally:
        close = getattr(resp, "close", None)
        if callable(close):
            close()


def validate_barcode(raw: str) -> str:
    """Validate a barcode as a string, preserving leading zeros.

    Rejects empty, non-digit, and implausible-length input. The raw string is
    returned unchanged (leading zeros preserved). Length is checked AFTER leading
    zeros are preserved, so ``00123`` (5 digits) is rejected, but a normal
    EAN/UPC with leading zeros is accepted.
    """
    if raw is None:
        raise ValueError("barcode is required")
    value = str(raw)
    if not value:
        raise ValueError("barcode is empty")
    if not value.isdigit():
        raise ValueError("barcode must contain only digits")
    if len(value) not in BARCODE_LENGTHS:
        raise ValueError(
            f"barcode must be {BARCODE_LENGTHS} digits, got {len(value)}")
    return value


class OpenBeautyFactsClient:
    """Bounded read-only client for the public product database (v3)."""

    def __init__(self, base_url: str = DEFAULT_BASE,
                 timeout: int = TIMEOUT_SECONDS,
                 max_response_bytes: int = MAX_RESPONSE_BYTES,
                 user_agent: Optional[str] = None,
                 contact: Optional[str] = None,
                 opener: Optional[Callable[[urllib.request.Request, float], Any]] = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.contact = contact
        if user_agent is None:
            user_agent = self._default_user_agent()
        elif contact:
            user_agent = f"{user_agent} (contact: {contact})"
        self.user_agent = user_agent
        self._opener = opener

    def _default_user_agent(self) -> str:
        import os
        contact = self.contact
        if contact is None:
            contact = os.environ.get(CONTACT_ENV)
        if contact:
            return f"{DEFAULT_USER_AGENT} (contact: {contact})"
        return DEFAULT_USER_AGENT

    def _request(self, url: str) -> Any:
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        if self._opener is not None:
            return self._opener(req, self.timeout)
        return urllib.request.urlopen(req, timeout=self.timeout)

    def lookup(self, barcode: str) -> ProductResult:
        """Look up a product by barcode.

        Returns a typed ProductResult. A malformed barcode raises ValueError
        (caller rejects it); a fetch/parse failure returns an ``upstream_error``
        or ``malformed_response`` outcome, never a product.
        """
        raw = validate_barcode(barcode)
        url = (f"{self.base_url}/api/v3/product/{urllib.parse.quote(raw)}"
               f"?product_type={PRODUCT_TYPE}")
        retrieved_at = _now_iso()
        try:
            resp = self._request(url)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return self._not_found(raw, retrieved_at)
            return ProductResult(
                status="upstream_error", barcode=raw, retrieved_at=retrieved_at,
                error=f"HTTP {exc.code}", disclaimer=DISCLAIMER)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return ProductResult(
                status="upstream_error", barcode=raw, retrieved_at=retrieved_at,
                error=f"{type(exc).__name__}: {exc}", disclaimer=DISCLAIMER)
        except Exception as exc:  # lookups must never raise a raw traceback
            return ProductResult(
                status="upstream_error", barcode=raw, retrieved_at=retrieved_at,
                error=f"{type(exc).__name__}: {exc}", disclaimer=DISCLAIMER)

        try:
            if isinstance(resp, bytes):
                body = resp
            else:
                body = _bounded_read(resp, self.max_response_bytes)
            data = json.loads(body.decode("utf-8"))
        except (LookupError, json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            return ProductResult(
                status="malformed_response", barcode=raw, retrieved_at=retrieved_at,
                error=f"{type(exc).__name__}: {exc}", disclaimer=DISCLAIMER)

        return self._parse(data, raw, retrieved_at)

    def _not_found(self, barcode: str, retrieved_at: str) -> ProductResult:
        return ProductResult(status="not_found", barcode=barcode,
                             retrieved_at=retrieved_at, disclaimer=DISCLAIMER)

    @staticmethod
    def _str_field(*, raw: Any, field: str, barcode: str, retrieved_at: str,
                   required: bool = False) -> "tuple[Optional[str], Optional[ProductResult]]":
        """Validate an upstream display field is a string (or None/absent).

        v3 returns these as strings. A non-None container (list/dict) is NOT
        coerced into a fake string label: it is a malformed response, so the
        caller never shows ``['Aqua']`` as an ingredient name. Returns
        ``(value, None)`` on success or ``(None, malformed_result)`` on failure.
        """
        if raw is None:
            if required:
                return None, ProductResult(
                    status="malformed_response", barcode=barcode,
                    retrieved_at=retrieved_at,
                    error=f"{field} missing", disclaimer=DISCLAIMER)
            return None, None
        if not isinstance(raw, str):
            return None, ProductResult(
                status="malformed_response", barcode=barcode,
                retrieved_at=retrieved_at,
                error=f"{field} must be a string, got {type(raw).__name__}",
                disclaimer=DISCLAIMER)
        return raw, None

    def _parse(self, data: dict, barcode: str, retrieved_at: str) -> ProductResult:
        if not isinstance(data, dict):
            # ``[]`` (a JSON array root) is malformed, never a product/not-found.
            return ProductResult(
                status="malformed_response", barcode=barcode,
                retrieved_at=retrieved_at,
                error=f"root must be a JSON object, got {type(data).__name__}",
                disclaimer=DISCLAIMER)
        status = data.get("status")
        product = data.get("product")
        result = data.get("result")
        # Documented not-found: explicit result id. ``{}`` and a bare
        # status!=success without that id are MALFORMED, not silently not-found.
        if isinstance(result, dict) and result.get("id") == "product_not_found":
            return self._not_found(barcode, retrieved_at)
        if status != "success":
            return ProductResult(
                status="malformed_response", barcode=barcode,
                retrieved_at=retrieved_at,
                error=f"unexpected upstream status: {status!r}",
                disclaimer=DISCLAIMER)
        if not isinstance(product, dict):
            # ``{"status":"success","product":[1]}`` must not raise AttributeError.
            return ProductResult(
                status="malformed_response", barcode=barcode,
                retrieved_at=retrieved_at,
                error=f"product must be a JSON object, got {type(product).__name__}",
                disclaimer=DISCLAIMER)
        if not product:
            return ProductResult(
                status="malformed_response", barcode=barcode,
                retrieved_at=retrieved_at, error="product object missing",
                disclaimer=DISCLAIMER)
        product_type = product.get("product_type")
        if not isinstance(product_type, str):
            return ProductResult(
                status="malformed_response", barcode=barcode,
                retrieved_at=retrieved_at,
                error=f"product_type must be a string, got {type(product_type).__name__}",
                disclaimer=DISCLAIMER)
        name, err = self._str_field(raw=product.get("product_name"), field="product_name",
                                    barcode=barcode, retrieved_at=retrieved_at)
        if err:
            return err
        if name is None:
            name, err = self._str_field(raw=product.get("generic_name"),
                                        field="generic_name", barcode=barcode,
                                        retrieved_at=retrieved_at)
            if err:
                return err
        brands, err = self._str_field(raw=product.get("brands"), field="brands",
                                      barcode=barcode, retrieved_at=retrieved_at)
        if err:
            return err
        ingredients_text, err = self._str_field(
            raw=product.get("ingredients_text"), field="ingredients_text",
            barcode=barcode, retrieved_at=retrieved_at)
        if err:
            return err
        # Provider-reported market evidence (list/string, crowd-sourced). We do
        # not infer or assert market availability beyond this upstream signal.
        countries_tags = product.get("countries_tags")
        if countries_tags is not None and not isinstance(countries_tags, list):
            countries_tags = None
        countries = product.get("countries")
        if countries is not None and not isinstance(countries, str):
            countries = None
        source_url = (f"https://world.openfoodfacts.org/product/{barcode}"
                      if barcode else None)
        if product_type != COSMETIC_PRODUCT_TYPE:
            # Only `beauty` (Open Beauty Facts) is a cosmetic we can check with
            # cosmetic rules. food / petfood / product / unknown are NOT cosmetics
            # and are never checked here (deferred). This is an explicit
            # "unsupported product category" outcome, not a silent pass.
            return ProductResult(
                status="unsupported_product_category", barcode=barcode,
                product_type=product_type, name=name, brands=brands,
                ingredients_text=ingredients_text, source_url=source_url,
                retrieved_at=retrieved_at, disclaimer=DISCLAIMER,
                countries_tags=countries_tags, countries=countries,
                error=(f"unsupported product type: {product_type!r} "
                       f"(only 'beauty' is a cosmetic)"))
        if not ingredients_text or not ingredients_text.strip():
            return ProductResult(
                status="found_without_ingredients", barcode=barcode,
                product_type=product_type, name=name, brands=brands,
                source_url=source_url, retrieved_at=retrieved_at,
                disclaimer=DISCLAIMER, countries_tags=countries_tags,
                countries=countries)
        return ProductResult(
            status="found_with_ingredients", barcode=barcode,
            product_type=product_type, name=name, brands=brands,
            ingredients_text=ingredients_text, source_url=source_url,
            retrieved_at=retrieved_at, disclaimer=DISCLAIMER,
            countries_tags=countries_tags, countries=countries)
