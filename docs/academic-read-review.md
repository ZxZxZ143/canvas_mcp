# Independent academic read implementation review

This is the original Phase 2 implementation review. The subsequent live-failure hardening and its newer verification results are recorded in the [resilience review](phase2-resilience-review.md).

The actual read-only `academic_read_reviewer` subagent reviewed the source, architecture and synthetic tests after implementation. It did not edit files, use real credentials or make live Canvas calls. No CRITICAL or HIGH finding was reported.

## Findings and dispositions

| Severity | Finding | Main-agent disposition |
| --- | --- | --- |
| MEDIUM | Protocol-relative/root-relative signed URLs could survive visible content projection | ACCEPTED and fixed. Omit identifiable URL/signed-reference forms; retain arithmetic and comment syntax. Regression fixtures cover absolute, protocol-relative, Canvas-relative and signed relative references. |
| MEDIUM | HTML-to-text conversion collapsed paragraphs/preformatted boundaries, allowing URL omission to consume adjacent instructions | ACCEPTED and fixed. Shared inert projection preserves block/list/pre line boundaries; names/titles remain single-line. Secret screening also checks boundary-removed text. |
| MEDIUM | Invalid RFC3339 offset minutes such as `+00:60` were silently normalized | ACCEPTED and fixed. Validate offset hour/minute ranges before constructing UTC instants. |
| MEDIUM | Upcoming excluded graded assignments even when submission was unknown | ACCEPTED and fixed. Exclude confirmed submitted work, not grading as a substitute for submission evidence; preserve graded+unknown with a warning. |
| LOW | Concurrent resumes could consume a one-use cursor twice | ACCEPTED and fixed. Reserve/consume the cursor before the network await; a barrier-controlled concurrent replay regression permits one winner. Failed resume requires a fresh scan. |
| LOW | Application ingress currently uses the Canvas numeric ID profile | ACCEPTED as a documented personal-MVP limitation. Core records/ports remain normalized and HTTP-free; a later non-Canvas ingress needs its own profile. No speculative provider factory added. |
| LOW | Quiz/discussion-backed assignment sequence compatibility lacks verified fixtures | PARTIALLY ACCEPTED. Added all module-item type fixtures and cross-module neighbor coverage. Exact assignment membership still fails closed on mismatched asset type/identity; quiz/discussion-backed sequence behavior needs a targeted live compatibility check, not invented fixture assumptions. |

During fix review, the reviewer caught overly broad URL omission affecting fractions/floor division/comments, and multiline display-name spoofing risk. Those follow-up regressions were fixed and tested before completion. Course `hide_final_grades` handling was additionally hardened using the documented course flag: return unknown totals without querying enrollment scores. This is conservative enforcement, not a claim that a live hidden-grade leak was observed.

The reviewer independently rechecked the corrected findings and concluded: no open CRITICAL, HIGH or MEDIUM findings remain. The two documented LOW compatibility/evolution notes above remain nonblocking; no source changes were made by the reviewer.

## Required review questions

1. **Other students' data:** normal application APIs accept no student selector; submission and enrollment identities must match the authenticated subject. Parent/course and learner-enrollment checks apply.
2. **Content-triggered actions:** no content-to-network/file/process/configuration execution path was found. Hostile-content tests guard those effects.
3. **Origin escapes:** fixed GET routes and the preserved origin/DNS/TLS/redirect/pagination controls prevent content URLs becoming request targets.
4. **HTML/instructions:** only bounded untrusted plain data is returned. Future host instruction handling still requires trusted policy; sanitization is not itself a prompt-injection defense.
5. **Context duplication:** three academic GETs normally, one own-submission fallback only when needed; no separate rubric or module-enumeration calls.
6. **Upcoming N+1:** requests scale by course/page, not assignment; one shared deadline/request/page ledger caps scans.
7. **Submission facts:** independent submitted/graded/late/missing/excused/required evidence is preserved; graded does not imply submitted.
8. **Timezone safety:** explicit offsets, strict parsing, UTC instants, deterministic supplied observation time and half-open windows are tested.
9. **Hidden grades:** missing/null/conflicting data stays absent/unknown; unposted fields are omitted and hidden course totals are not fetched.
10. **Raw payload leakage:** raw Canvas JSON/headers remain in infrastructure; application/MCP-facing records are allowlisted normalized dataclasses. MCP remains unimplemented.
11. **Phase 1 regressions:** existing security tests remain in the full suite; single-line display output and concurrent cursor semantics were specifically rechecked after shared changes.
12. **MVP simplicity:** still one local package, one existing HTTP client/provider, narrow ports and two small application areas. No database, scheduler, plugin, MCP server, write stack or file subsystem was added.

## Verification

Main final verification: **274 tests passed**, configured mypy passed on **36 source files**, and Ruff passed. The final wheel built and installed successfully; all **36 installed modules** imported in isolated mode, with **zero source/install file mismatches**. Both installed smoke-command help screens and `pip check` passed. Independent reviewer verification: initial full suite **248 passed**, post-fix focused suite **80 passed**, final revised regression suite **17 passed**. These are different runs/subsets, not additive counts. No Phase 2 live university smoke run was performed by the agent.
