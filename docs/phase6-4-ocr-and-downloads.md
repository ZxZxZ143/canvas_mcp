# Phase 6.4 — OCR and downloadable originals

Status: implementation and validation in progress. Do not mark COMPLETE until the
real Canvas OCR, actual ChatGPT-managed original download/integrity, native-text
regression and independent security review all pass.

Architecture, format scope, resource caps, extraction metadata, download workflow,
sandbox and user-facing behavior are in [remote-file-ocr.md](remote-file-ocr.md).

## Validation record

Local checks: mypy passed (85 sources); Ruff/check and format passed (214 files);
wheel build, isolated install/import and byte-for-byte wheel/source comparison
passed (85 Python files). Focused MCP/HTTP/OAuth tests passed. Final full pytest,
real Linux OCR/seccomp execution and real Canvas/ChatGPT OCR/file-card verification
remain in progress.

Independent read-only ocr-download-security-reviewer requested. Initial architectural
review required fail-closed single-thread/FD bootstrap, shared concurrency, source
pixel limits, exact artifact wire cap, original-byte reflection rejection and real
platform artifact proof. Findings fixed: ordinary HTTP cap rejecting originals,
blank-page provenance, encoded capability reflection, missing output schema and
hidden-base64 credential reflection. Independent re-review found no remaining
Critical/High source findings; safe 4 MiB transport and rejection of raw/UTF-16BE/
nested percent-encoded bearer reflections were independently reproduced. Final
release approval remains conditional on Linux and real ChatGPT proof.

No Canvas writes, permission expansion, paid service or Phase 7 is introduced.
