"""The merchant subsystem.

A minimal, self-contained mock of the *external* merchant backend that TrustRail
coordinates with: a synthetic catalogue, a service layer, a client seam, and a
FastAPI router exposing the merchant endpoints. TrustRail talks to it only
through :class:`app.merchant.client.MerchantClient`, so in Phase 2 it can be
swapped for a real HTTP merchant without touching the orchestration code.
"""
