# Phase 6.4.1 — automatic native originals

Chosen approach: **HOST-NATIVE FILE CARD**. Source verification passed; product
verification is partially complete and paused because ChatGPT loading is unstable.
Candidate source: `3e3a604bb1ed69931cb7dba3741fbfb8ec76ad8a`; deployed and source-tested.
A precautionary rollback to the previously manually verified `c153768` completed,
then the existing successful native image was restored. Current source is 3e3a604;
the full deployment history and product evidence are recorded below.
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

- Final full Windows pytest: 1451 passed, 56 Linux-only skipped, two existing warnings,
  263.28 seconds, including the added concurrency/aggregate cases.
- Final focused remote MCP/OAuth/retained UI/native transfer suite: 191 passed,
  one existing warning, 20.38 seconds. Includes new aggregate and slow-send cases.
- Actual Render Linux Docker verification: 106 passed, one existing warning,
  13.43 seconds, including same-download original snapshot and reading above 4 MiB.
- Mypy: 88 source files; Ruff check and format check passed (155 source/test files).
- Wheel build/install/pip check passed; all 88 source/wheel/installed Python files
  matched exactly. Installed-wheel live stdio initialize, 15 tools, profile/courses passed.
- Independent read-only inline-file-ux-reviewer and file-transfer-security-reviewer:
  no unresolved Critical/High findings. Availability wording corrected; actual host
  acceptance/download/persistence must still be verified separately.

## First real native result and rate-limit interruption

The real `Homework-1.pdf` page-1 analysis produced a native host attachment and
the correct three-page PDF preview automatically, without canvas_download_file.
ChatGPT reported 14 seconds of processing; the native resource read was HTTP 200,
4 ms in the safe Render log. The assistant omitted the extraction_mode field;
source and independent SDK review confirm it is present in JSON text and structured
content at `data.content.extraction_mode`. Its omission is not evidence of a missing
server field. Host auto-preview opened; it was closed before further tests.

Reopening that chat failed, then ordinary non-Canvas chats also failed. The user
reproduced the sequence in another window and later on mobile/web observed
`Too many requests` in the server response. A newly created plain no-plugin chat
also failed after reload, while the official desktop read_thread returned its saved
one-word conversation successfully. Reading the native test chat returned
`Too many requests`. This is positive rate-limit evidence, not an established
native/schema/size/URI root cause. Do not describe these failures as proof that
native files corrupt conversations, or reclassify the earlier manually completed
Phase 6.4 based solely on this rate-limited run.

ChatGPT browser/API requests were stopped and the four new test tabs closed to
reduce background traffic. The precautionary Free Render rollback was still building
at the last check; it must not be claimed Live prematurely. Candidate source remains committed for controlled verification after
the account recovers. No undocumented host API, cookie/session extraction, security
weakening or deletion of user conversations was used.

The native synthetic card at 390px width remained compact with an accessible
Download button in the tree. Native host visual treatment/hover behavior is host
controlled; no internal CSS was copied. The viewport override was reset.

At the point of the interruption, the additional product checks remained pending.
Subsequent recovery evidence is recorded below. Active
course file listings inspected for a real standalone image contained no image
files; this does not imply that all possible assignment/module sources are empty.
No Canvas write or fabricated original is used to fill that test gap. Phase 7 is not started.

### Recovery and real-original integrity

After ten minutes without ChatGPT browser/API traffic, one fresh load of the plain
no-plugin control conversation succeeded. One load of the existing native PDF
conversation also succeeded: its explanation and native inline card remained.
The earlier replay failures therefore are not an established native-file defect.
No retry loop or private ChatGPT endpoint was used.

The inline Download action was clicked with no preview panel open. The user saved
the Windows Save As dialog to Downloads. The newly saved `Homework-1.pdf` timestamp
was `2026-09-27T19:15:30.4050583Z`; it contains 519944 bytes, three PDF pages and
SHA-256 `d617512c7c5a3a1b84a0efb8be5b00b3896c9e979a28ed7d451b8514364da193`,
all matching the previously validated Canvas original. This is a new native download,
not the earlier Phase 6.4 copy. The native registry's 300-second lifetime had elapsed
long before this replay/download, demonstrating independence from that memory entry.
Browser-download-start timing cannot be measured reliably across the user-operated
OS save dialog; one inline browser click plus OS Save confirmation was observed.

The precautionary rollback did reach Live at 19:13:48 UTC despite the attempted
cancel, replacing candidate 3e3a604 with c153768. Restoration of the previously
successful candidate image completed at 19:17:49 UTC in deployment
`dep-dasmnhh7lnhs739sfj50`. Render reported no configuration changes; the source
is again 3e3a604. This used the existing Free service without a plan change.
Do not claim an after-restart download request from the saved file's completion
timestamp alone; the actual click preceded the final rollback status observation.

One follow-up in the existing PDF conversation produced its requested short
explanation without a tool activity or additional file card. This verifies normal
reuse for that controlled case, not enforced conversation-wide deduplication.

A subsequent replay after the container restoration again displayed ChatGPT's
generic conversation-load error. No immediate retry, native probe or additional
ChatGPT API read was attempted. The test tab was closed. The earlier directly
observed Too many requests response remains rate-limit evidence; the generic
later failure does not independently establish its HTTP status or a file defect.

### Remaining product checks

- ChatGPT OCR result plus automatic original on the current native build.
- ChatGPT standalone image OCR plus automatic original when a real Canvas image
  is available (the bounded active-course file listings found none).
- A new download request made after a confirmed container restart; saving an
  already-open OS download dialog afterward is insufficient evidence.
- Stable repeated manual replay after the account's request restriction clears.
- An actually stale host download URL/fresh URL test, if its expiry is observable
  through supported host behavior. Server handoff expiry already passed, but it is
  not the same as host download URL expiry.
- Precise browser-download-start timing beyond the Windows Save As handoff.

Phase 6.4 remains complete on the user's prior manual evidence. Phase 6.4.1 is
not marked complete while these product checks are unresolved; no Phase 7 work starts.
