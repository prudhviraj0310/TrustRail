"""Pydantic schemas — the validated, typed boundary for untrusted input.

Anything an AI buyer sends is parsed into these models before a service ever
sees it. Extra/unknown fields are ignored (LLMs are chatty); only the
financially-relevant fields are accepted and later canonicalised.
"""
