"""eu-reg-api: local read-only REST API over the Annex-II registry (Phase 3).

Exposes a FastAPI app that reuses ``eu_reg_lists`` (search/lookup) and
``eu_ingredient_linter`` (label checking) without duplicating matching,
tokenization, classification, or reconciliation logic.

Endpoints: ``/v1/substances``, ``/v1/substances/{cas}``, ``/v1/lists/{name}``,
``/v1/check``, ``/health``. Every response carries the honesty disclaimer;
``data_updated_at`` is the snapshot build/update time (never request time).
"""

from __future__ import annotations

from .main import build_app_from_snapshot, create_app, run

__version__ = "0.1.0"
__all__ = ["build_app_from_snapshot", "create_app", "run"]
