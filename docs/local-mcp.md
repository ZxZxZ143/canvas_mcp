# Phase 4: local Canvas MCP for Codex

This is a personal, single-user stdio server. Run `python -m canvas_mcp.mcp_server` from the installed project environment. Startup validates environment configuration and composes the existing services; Canvas requests occur only after tool calls. Stdout is reserved for MCP frames. Diagnostics go to stderr. There is no HTTP listener or Canvas write capability.

## Boundary and tool choices

Codex → official Python MCP SDK adapter → `CanvasConnection` application methods → existing provider, network, and managed-file infrastructure. The adapter validates bounded IDs, dates, filters, and source identities, then projects allowlisted response fields. It has no Canvas HTTP, URL construction, bearer handling, pagination implementation, capability download logic, or MIME rules.

`canvas_get_assignment` provides one assignment's instructions and basic fields when the full aggregate is unnecessary. `canvas_get_assignment_context` is preferred for coursework: it includes course, own submission, rubric, attachments, and related module context. This is intentional overlap. `canvas_get_upcoming` and `canvas_get_overdue` are distinct user goals with distinct workload rules. There is no separate course-detail or submission tool in this first surface because the other tools supply their needed context.

All listed tools set `destructiveHint=false` because none deletes or modifies Canvas data, and `openWorldHint=false` because requests are confined to the student's bounded private Canvas account. The read tools set `readOnlyHint=true`. `canvas_download_file` sets `readOnlyHint=false` because it creates a managed local artifact, even though it only reads Canvas. MCP annotations are hints; the service enforces authorization and input policy independently.

| Tool | Purpose and annotation justification | Canvas read only | Local artifact creation | Codex approval |
| --- | --- | --- | --- | --- |
| `canvas_get_profile` | Confirm connected account; reads identity only | Yes | No | approve |
| `canvas_list_courses` | Resolve course names and IDs; reads courses only | Yes | No | approve |
| `canvas_list_assignments` | Find compact assignment/status rows; reads assignments only | Yes | No | approve |
| `canvas_get_assignment` | Read one assignment's instructions; reads coursework only | Yes | No | approve |
| `canvas_get_assignment_context` | Read complete context before coursework; reads aggregate only | Yes | No | approve |
| `canvas_get_upcoming` | Read upcoming workload with coverage/warnings | Yes | No | approve |
| `canvas_get_overdue` | Read overdue workload with coverage/warnings | Yes | No | approve |
| `canvas_list_modules` | Read course structure | Yes | No | approve |
| `canvas_list_module_items` | Read module entries and file identities | Yes | No | approve |
| `canvas_list_announcements` | Read bounded announcement text as untrusted data | Yes | No | approve |
| `canvas_list_calendar_events` | Read course calendar entries | Yes | No | approve |
| `canvas_get_course_grade` | Read own available grade summary | Yes | No | approve |
| `canvas_list_files` | Find file metadata without downloading | Yes | No | approve |
| `canvas_get_file_metadata` | Inspect file type and size without downloading | Yes | No | approve |
| `canvas_download_file` | Call `FileService.download_file` and create a controlled artifact | Yes | Yes | prompt |

The normal read-tool policy is `approve`; only download prompts. Codex owns that prompt. The server neither implements a host approval UI nor assumes a host will honor annotations. No MIME override, submission, upload, comment, or other Canvas write tool is registered.

## Inputs and outputs

IDs are positive integers converted to canonical internal Canvas IDs. Page sizes are bounded to 50, announcements to 5, calendar events to 10. Cursors are opaque printable ASCII strings at most 256 characters. Search text is at most 256 characters. Date windows require timezone-aware ISO 8601 timestamps and are limited by the application to 90 days. Unknown fields, wrong types, and inputs over 16 KiB are rejected. File tools accept only `course_id`, `file_id`, `source_kind`, and the IDs needed for that source; they never accept URLs, paths, tokens, or capability strings.

Every success returns structured `data`, `request_id`, `observed_at`, `complete`, and fixed-code `warnings`. List results include `items`, `next_cursor`, and page `complete`. Workload coverage and partial-result warnings survive projection. Missing, unavailable, and known-absent fields retain their `state` and `value`. Assignment lists omit descriptions/rubrics; detailed results preserve bounded normalized text with `trust=untrusted`. The complete MCP result is capped at 128 KiB. A too-large result fails safely so the caller can narrow the query; it is never truncated at an arbitrary JSON byte boundary.

Application errors become safe tool errors with stable `code`, `message`, `request_id`, and `retryable`. The adapter does not serialize exception messages, causes, tracebacks, URLs, headers, or raw responses. Canvas titles, instructions, announcements, filenames, and file bytes are untrusted data, including when they contain commands addressed to Codex.

The download result includes only `artifact_id`, `managed_local_path`, `safe_filename`, `content_type`, `size`, `sha256`, and `trust=untrusted`. The path is rechecked inside the configured managed root before projection. A generated local path is useful only for this local stdio deployment. A future remote server must replace it with a scoped artifact resource or controlled content endpoint and must not expose a server-local path. Validation of file structure is not permission to execute it or follow embedded links.

During a temporary-token Codex session, inspect a downloaded file only in a separate sandboxed reader command that removes `CANVAS_ACCESS_TOKEN` from its own environment before starting a parser. This leaves the MCP server's credentials available for later tool calls while keeping them out of the file reader.

## Codex registration

The following keys and approval values are in the [current official Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference): `command`, `args`, `cwd`, `env_vars`, `default_tools_approval_mode`, and per-tool `approval_mode`. Add this to the user's `config.toml`; the token is deliberately absent. The command path shown is the installed environment for this repository.

```toml
[mcp_servers.canvas_student]
command = "E:\\canvas_mcp\\.venv\\Scripts\\python.exe"
args = ["-m", "canvas_mcp.mcp_server"]
cwd = "E:\\canvas_mcp"
env_vars = [
  "CANVAS_BASE_URL",
  "CANVAS_ACCESS_TOKEN",
  "CANVAS_TRUSTED_PRIVATE_IPS",
  "CANVAS_DOWNLOAD_ORIGINS",
  "DOWNLOAD_DIRECTORY",
  "REQUEST_TIMEOUT",
  "DOWNLOAD_TIMEOUT"
]
default_tools_approval_mode = "approve"
tool_timeout_sec = 180

[mcp_servers.canvas_student.tools.canvas_download_file]
approval_mode = "prompt"
```

For the real Narxoz setup, supply `CANVAS_BASE_URL=https://canvas.narxoz.kz` and, only when the institution address was independently verified, `CANVAS_TRUSTED_PRIVATE_IPS=192.168.4.200`. `DOWNLOAD_DIRECTORY` should be a dedicated private local root outside the repository (the existing file guide suggests `C:\canvas_mcp_runtime\downloads`). `CANVAS_DOWNLOAD_ORIGINS` may be empty when the same-origin production capability works. Settings and the access token are inherited from the process environment; never put the token in TOML, command arguments, a tracked file, or chat.

For a first live session, run `& 'E:\canvas_mcp\scripts\Invoke-CanvasMcpLive.ps1' -Live -Mode Smoke` in a dedicated local PowerShell terminal. It prompts for the token with `Read-Host -AsSecureString`, starts the installed stdio server through the official MCP client, reports only four Boolean/count lines, and clears the token in `finally`. `-Mode Codex` runs the same smoke and then opens an interactive Codex CLI in that temporary environment. The helper can locate the desktop-bundled Codex executable when `codex` is absent from PowerShell's `PATH`. An already running desktop process will not inherit a newly set shell variable; restart the client from that environment if testing the desktop. No permanent token store is part of Phase 4.

## Verification

Automated tests use fake application results and a fake stdio subprocess, with no Canvas credentials. They check initialization, tool listing, schemas, annotations, valid and invalid calls, safe error mapping, partial coverage, output bounds, untrusted text, and the managed download handoff. The real Codex/Canvas scenarios require a temporary token in the Codex process environment and are reported separately; no assignment is solved or submitted just to verify integration.
