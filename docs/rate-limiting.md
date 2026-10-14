# Rate limiting reference (bounded, no-accounts)

Regulatory information, not legal advice.

This is a **reference planning document**, not a wired-in implementation. The V1
scope has no accounts or payments, so the bounded, no-accounts approach is a
simple per-origin/per-IP limiter over the read endpoints. It is documented here
so the proxy runbook and the services stay agnostic to the hosting choice. It is
**not** yet enabled in the code — wiring it in is a separate, scoped change that
must preserve the accepted test/coverage baseline and the exact error semantics.

## Rationale

- The shared registry is read-only and in-memory, so the services are cheap to
  serve; the expensive operations are the multi-token formula check and the
  barcode -> product lookup (which can hit the external Open Beauty Facts
  provider).
- No accounts means there is no per-user key; a per-origin/IP limiter is the
  appropriate no-credentials boundary.
- A single, documented limiter is preferred over several competing ones.

## Endpoints to limit

Apply the limiter to the read/check endpoints; serve cheap assets freely:

| Origin | Endpoint | Notes |
|---|---|---|
| `eu-reg-api` | `POST /v1/check-formula` | multi-token evaluation |
| `eu-reg-api` | `GET /v1/check`, `/v1/substances`, `/v1/lists/{l}` | registry reads |
| `scan-to-check` | `POST /api/check` | ingredient check |
| `scan-to-check` | `GET /api/product/{barcode}` | external provider lookup |
| both | `/health`, `/api/health`, `/static/...` | cheap; exclude or generous limit |

## Reference behavior

On exceed, return **`429`** with a `Retry-After` header and a structured body
that carries the same disclaimer and coverage semantics as the rest of the API
(e.g. a `rate_limited` error code, `message`, and the regulatory disclaimer).
Never return a cached or "clean" result on a rate-limit or backend-unavailable
condition.

## Two implementation options (choose ONE)

1. **App-side in-memory limiter** (matches the hosting brief): a single-process,
   per-IP/per-origin limiter over the read endpoints. Simple, no new dependency,
   but per-process — so it is only correct with one worker per service.
2. **Proxy-level limiter**: nginx's built-in `limit_req` on the same two
   locations (Caddy's standard build has no `rate_limit` directive; that is a
   third-party plugin). The proxy must set a trusted `X-Forwarded-For`/`X-Real-IP`
   so the limiter keys on the real client IP, not the loopback proxy.

Keep the limit generous enough not to break a normal user session but bounded
enough to stop abuse. The registry is small and read-only, so the numbers are a
capacity/abuse trade-off, not a correctness gate.

## Not done here

No limiter is wired into the running services or the proxy in this preparation
step. This is a documented reference; implementing it (and its tests) is a
separate change that must preserve the >=90% named-core coverage and the exact
"not in dataset is NOT clean" / ECHA not-included semantics.

Regulatory information, not legal advice.
