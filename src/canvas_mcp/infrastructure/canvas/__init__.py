"""Canvas HTTP, profile/course mappings and scoped query adapter.

Owns fixed GET routes, authorized self-user queries, credential attachment,
outbound policy, bounded pagination/timeouts/retries, and safe error translation.
Raw payloads never escape this infrastructure boundary. File source resolution
and all other coursework operations remain future work.
"""
