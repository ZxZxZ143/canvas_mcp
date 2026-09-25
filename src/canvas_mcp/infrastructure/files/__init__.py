"""Future CourseArtifacts implementation and scoped artifact resolver.

Owns credential-free downloads, source/redirect/DNS checks, owner-only roots,
safe generated filenames, exclusive no-follow creation, quotas and lifecycle.
Never parse, execute, unpack, or overwrite. Inspection is a separate operation.
"""
