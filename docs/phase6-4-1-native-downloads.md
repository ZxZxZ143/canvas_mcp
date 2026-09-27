# Phase 6.4.1 — automatic native originals

Chosen approach: **HOST-NATIVE FILE CARD**. Product verification is in progress.
Phase 6.4 itself is complete on the user's later manual replay/download evidence.

## Supported representation and synthetic experiment

The server returns standard MCP `ResourceLink` alongside bounded extracted text and
structured metadata. ChatGPT reads a separate binary MCP resource, whose response
contains `BlobResourceContents`. No original bytes/base64 enter model tool content,
structuredContent, widget state or a plain download URL. No private OpenAI file
registration API, invented fileId or undocumented `_meta` file representation is used.
The host handles its own attachment identity and temporary download links.

Official references: [Plugins reference](https://developers.openai.com/plugins/reference),
[ChatGPT UI](https://developers.openai.com/plugins/build/chatgpt-ui), and the installed
MCP SDK ResourceLink/ReadResourceResult definitions. Resource links need not appear in
resources/list. The server overrides exact private resource reads instead of adding
per-document resource objects to the discoverable SDK registry.

Probe `8a88683c5f60a459d666e5f13d3e695447820246` returned a fixed synthetic PDF with
no Canvas access: 607 bytes, one page, SHA-256
`551370601cd3a83b304d4053d17f5560b52ed2fb2fa4217a8ab5f216647344b7`.
A fresh ChatGPT conversation displayed the host's native inline card and correct
preview. Reload restored the card. The user completed Windows Save As; the saved
PDF matched size/hash and independently read as one page. Browser automation did
not emit a download event because the OS save dialog required user action.
The temporary synthetic tool/resource are removed from the production surface.

ChatGPT automatically opened its native preview during ingestion. No custom panel
or modal is requested by this implementation; there is no documented suppression
flag. Do not report zero observed host preview opens or mistake the native preview
for a required custom-widget download step. Direct inline download is retested with
the preview closed. Windows Save As can add a separate OS confirmation.

## Same download, private handoff and cleanup

The authorized anonymous Canvas download is validated once. Parsing/OCR and the
original snapshot use the same validated read-only descriptor, rewound after parsing.
Snapshot size/hash and reflected capability checks are repeated before publication.
The server then closes/unlinks temporary staging on success/failure/cancel/timeout.
Only a bounded memory snapshot survives for host ingestion, not a Render temp path.
HTTP delivery does not claim host acceptance acknowledgment.

Opaque exact native resource URIs are bound to the authenticated principal and
Canvas connection. The URI alone is insufficient authorization. No original is
listed. Every original is checked against server secrets and the issuance request's
presented bearer before its link leaves; resource bytes are independently checked
against the later read request's bearer, which may differ. No credential is retained
in the registry or supplied to a document parser/UI.

The registry permits at most two entries and **4 MiB aggregate**, expires abandoned
entries with a real 300-second timer, and clears on shutdown. It never evicts an
unexpired promised original to admit another. Successful resource delivery allows
a nonextending 10-second ingestion retry grace; it is not indefinite server storage.
Request receipt ledgers span SDK child tasks, roll back failed publication/read/send
and cancellation, and keep original leases through framing/validation/send. A shared
HTTP transfer gate prevents another file analysis/byte result overlapping that send.

Analysis still accepts up to 8 MiB; original transfer remains 4 MiB. Larger readable
files report `original_download_available=false` and
`original_download_reason=original_too_large_for_chat_transfer`. Capacity exhaustion
reports `original_handoff_capacity`. Availability means a prepared validated reference,
not proven host acceptance. Ordinary HTTP capture remains 256 KiB and text tool
results remain 128 KiB; only exact native binary reads and the retained private
legacy byte tool receive the existing 5,700,000-byte transport cap.

OCR/native/hybrid extraction, sandbox and limits are unchanged. Originals and their
names are untrusted data. No arbitrary URLs, paths or embedded-code execution are
authorized. No new route, scope, identity, paid plan or file-library side effect exists.

## Surface and conversation behavior

Successful `canvas_get_file_content` automatically supplies the native original
reference. List/metadata tools remain data-only. No new custom UI is used. The
existing 17 remote tools (16 model-visible and one private app-only legacy byte tool)
are retained until preferred-path real integrity/replay/restart tests pass; the old
fixed resource aliases remain necessary for earlier saved custom-card conversations.
Local stdio remains 15 tools.

Ordinary follow-ups reuse already-read context by server instruction. There is no
invented conversation identifier or cross-conversation cache; a fresh analysis can
create another attachment. Do not claim technical conversation-wide deduplication.
No `library:true` is requested. Historical native downloads must be host-owned and
must work after handoff expiry/container restart, without Canvas refetch.

## Validation so far

- Full Windows pytest: 1449 passed, 56 Linux-only skipped, two existing warnings,
  256.33 seconds. Added concurrency/aggregate cases are checked in the final focused run.
- Mypy: 88 source files; Ruff check and format check passed.
- Wheel build/install/pip check passed; all 88 source/wheel/installed Python files
  matched exactly. Installed-wheel live stdio initialize, 15 tools, profile/courses passed.
- Independent read-only inline-file-ux-reviewer and file-transfer-security-reviewer:
  no unresolved Critical/High findings. Availability wording corrected; actual host
  acceptance/download/persistence must still be verified separately.

Real text/OCR/image, normal follow-up, replay, restart, downloaded-original integrity,
and timing evidence will be recorded after the deployment checks. Phase 7 is not started.
