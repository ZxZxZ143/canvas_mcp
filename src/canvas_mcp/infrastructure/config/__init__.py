"""Explicit environment loader, typed settings and scoped CredentialSource.

Only the outer composition root consumes validated deployment settings. The
application receives request context and explicit policy values, never env access.
"""
