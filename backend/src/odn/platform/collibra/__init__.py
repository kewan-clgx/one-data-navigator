"""Collibra catalog integration (docs/collibra_int_phase5_plan.md).

EasyQuery calls Collibra as the signed-in user: it signs a short-lived JWT (`sub` = the user's verified work
email) that the tenant's Collibra environment trusts through EasyQuery's public JWKS. Users never sign in to
Collibra separately, their own Collibra permissions apply, and no Google or Collibra session is forwarded.
"""
