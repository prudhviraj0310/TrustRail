"""Service layer.

Deterministic business logic. Nothing here trusts LLM output; the API layer
converts untrusted input into validated Pydantic models before it ever reaches
these services.
"""
