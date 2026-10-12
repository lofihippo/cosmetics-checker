"""scan-to-check: local barcode -> product ingredients -> existing checker.

Regulatory information, not legal advice.
"""

from __future__ import annotations

from .app import create_app, run
from .obf import (
    DISCLAIMER,
    OpenBeautyFactsClient,
    ProductResult,
    validate_barcode,
)

__all__ = [
    "DISCLAIMER",
    "OpenBeautyFactsClient",
    "ProductResult",
    "create_app",
    "run",
    "validate_barcode",
]
