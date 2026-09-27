# Phase 6.4 — OCR and downloadable originals

Status: **INCOMPLETE — saved artifact-card conversations fail to reload.**
The reviewed implementation is deployed on Render Free. Real Canvas OCR, original
attachment/download and native-text regression work in fresh ChatGPT sessions.
However, normal saved-conversation recovery fails reproducibly, including before
Attach. This release blocker is unresolved; its cause is not established. Source
security approval and passing automated checks do not establish platform persistence.

Architecture, supported formats, resource caps, extraction metadata, security and
user-facing behavior are documented in [remote-file-ocr.md](remote-file-ocr.md).

## Final implementation and automated validation

Deployed source commit: `c1537687b05492e43882e9b93ef3292eb31a8469` on branch
`codex/phase6-4-ocr-downloads`. Render confirmed it Live on the existing Free service.
Later documentation-only commits do not change the deployed source.

| Check | Observed result |
| --- | --- |
| Full final pytest | 1435 passed, 55 Linux-only skipped, two existing dependency deprecation warnings; 283.01 seconds |
| Focused remote MCP suite | 175 passed, one existing warning; 19.10 seconds |
| Actual Linux Docker verification on deployed source | 105 passed, one existing warning; 13.33 seconds |
| Mypy | 86 source files passed |
| Ruff check / format check | Passed; 216 files formatted |
| Wheel build / isolated install / pip check | Passed |
| Wheel/source/installed comparison | Exact bytes for all 86 Python files |
| Installed-wheel live stdio | Initialize, 15 tools, profile and courses passed using the existing Windows credential provider |

Linux verification includes actual image OCR, JPEG EXIF without JFIF, scanned and
hybrid PDFs, native PDF, denied open/network/process/write operations, credential-free
environment, cancellation and temporary-file cleanup. Windows skips are not claimed
as local Linux execution; the mandatory Render Docker gate supplies that evidence.

## Real Canvas content and artifact evidence

The verified original is a three-page, 519944-byte coursework PDF. Pages 1 and 2
contain native text; page 3 contains rasterized DFA exercises and mathematical notation.
Its original page 3 was visually inspected before assessing OCR accuracy.

The first deployed OCR call (`999a0f6`) returned hybrid mode, OCR pages [3], native
pages [1, 2], three processed pages, content available and no truncation, in 18.429
seconds. Another live hybrid call (`bd4c34e`) passed in 19.957 seconds. A page-3-only
call on `545f64f` returned mode ocr, OCR pages [3], native pages [], one processed page,
content available and truncated because one of three pages was selected, in 17.550
seconds. The exercise prose/explanation matched the original. DFA indices and math
glyphs contained OCR errors; the assistant warned against treating the recovered
table/formulas as reliable. Diagrams were not claimed as structurally interpreted.

The first ChatGPT file-card workflow (`999a0f6`) used the platform file service and
its Download original link. Chrome opened the three-page PDF, and its viewer Download
control saved a non-empty local copy. Read-only filesystem hashing confirmed exactly
519944 bytes and this SHA-256, matching the validated original:

`d617512c7c5a3a1b84a0efb8be5b00b3896c9e979a28ed7d451b8514364da193`

The final `c153768` workflow was separately exercised in a fresh unreloaded ChatGPT
conversation. The model prepared compact metadata through ordinary structured output,
without hidden metadata or original bytes. A user click on Attach invoked the separate
authenticated `canvas_fetch_original_for_card`: server logs confirmed HTTP 200 in
13.469 seconds. The card compared the complete reference and metadata, verified the
original SHA-256 and displayed its successful attachment status. Its actual host
download link opened the correct three-page PDF; page 1's heading and content matched
the inspected original. The viewer Download control was clicked once. No new local
copy was confirmed by the narrow known-filename check, so no second local hash is
claimed. The independently measured saved-file hash above belongs to the earlier
platform test; final-path evidence is client integrity, platform acceptance and
the real PDF download link/preview.

Native page-1 regression passed again on final deployed `c153768`: native mode,
ocr_used false, OCR pages [], native pages [1], one processed page and the correct
"Sets. Operations on Sets." heading. Truncated was true because only one of three
pages was requested, not because the page's text was lost. The original was not
requested or attached again for this regression.

No Canvas signed URL, server path, manual user upload, permission expansion or paid
service was used. ChatGPT's own temporary download links are platform-managed and
are distinct from private Canvas capabilities. Browser URL policy blocked one attempted
Chrome downloads-page inspection; no workaround or security weakening was used.

## Saved-conversation release blocker

A native-only control reloaded successfully. Adding an original-file result made that
conversation fail to reload. A separate original-only control failed before Attach,
without uploadFile, widget state, preview or browser download probes. Retry did not
restore the conversations.

Three bounded mitigations were deployed and tested:

1. `c80d45f`: register the earlier fixed v1 resource alias. Reload still failed.
2. `545f64f`: compact preparation plus a separate app-only byte tool, with no binary
   or base64 anywhere in preparation. Reload still failed before Attach.
3. `c153768`: ordinary McpResult preparation with no _meta, accepting the identity
   through structuredContent.data/toolOutput. The actual v4 script and app-only fetch
   were confirmed loaded; reload still failed before Attach.

These controls exclude binary presence, upload and widget-state changes as necessary
causes of the observed failure. They do not establish a host defect, exact size cap,
authentication fault or protocol root cause. A filtered server log had no 401 matching
one reload; that does not prove whether resources/read was attempted. The final fresh
attachment test demonstrates byte transfer but does not repair saved conversations.
Historical failed conversations have not been claimed restored or deleted.

Remote registration is now 17 tools: 16 model-visible tools plus one app-only byte
transport without a UI template. Stdio remains 15. The larger response cap and decoded
original credential-reflection guard apply together only to that byte transport.
Fixed v1/v2/v3 aliases serve the same reviewed v4 HTML and empty external CSP.

Completion requires a verified compatibility remedy and actual saved-conversation
reload checks both before and after Attach, in addition to the already demonstrated
OCR, original integrity and native regression. No speculative security/protocol
weakening is justified by this unresolved behavior.

## Resource measurements

The packaged English model is 4,113,088 bytes (about 3.92 MiB). A synthetic one-page
Linux OCR sample took 0.380 seconds wall and 0.363 seconds CPU. Aggregate peak child
RSS across those build checks was 107304 KiB. These are build samples, not guarantees
for every document or measured live-service memory. Total runtime container-image
size was not independently measured. No multilingual/GPU stack or runtime model
download is installed.

The service remains Render Free, 512 MB and 0.15 CPU. Live CPU/memory metrics are gated
behind a paid compute plan, which was not enabled. Render warns idle cold starts may
delay requests by 50 seconds or more. Fixed OCR limits, shared concurrency and fail-safe
timeouts remain enforced despite variable document complexity and cold starts.

## Independent review

The requested read-only `ocr-download-security-reviewer` challenged sandbox isolation,
URL/reference authorization, secret/capability reflection, byte transport, resource
limits, cleanup, native regression and actual platform proof. Justified findings were
fixed and re-reviewed: ordinary HTTP cap rejecting originals, blank-page provenance,
encoded capability reflection, missing output schema and hidden-base64 credential
reflection. EXIF bootstrap was repaired without weakening the sealed parser.

Independent verification reproduced safe 4 MiB transport and rejection of raw,
UTF-16BE and nested percent-encoded bearer reflections. It executed the actual UI
JavaScript against synthetic parent spoofing, reference/hash mismatch, retries,
user-click-only retrieval, compact widget state and repeated host globals. All five
current/legacy preparation envelopes passed v4 checks. The installed SDK's annotated
CallToolResult contract was checked. No unresolved Critical/High source finding or
definite protocol-contract flaw remains.

Final disposition: implementation security approved; Phase 6.4 completion blocked by
reproducible saved-card conversation failure with unknown cause. Do not equate source
approval, fresh attachment success or an earlier saved-byte hash with persistence.

No Canvas writes, permission expansion, paid service or Phase 7 is introduced.
