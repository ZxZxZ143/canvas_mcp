# Canvas Student for Codex

Canvas Student is a personal, read-only Canvas LMS integration for Codex. A local stdio MCP server exposes bounded coursework queries, while a Codex plugin supplies four workflows for finding and understanding courses, assignments, deadlines, and materials.

## Current status

| Item | State |
| --- | --- |
| Version | 0.1.0 |
| Deployment | Personal plugin and local stdio MCP |
| Canvas access | Read-only |
| Tested platform | Windows |
| Live-tested installation | Narxoz University Canvas |

The stable plugin is a local, single-user installation. Phase 6.1 adds a Streamable HTTP adapter with default-deny authentication. Phase 6.2 has a personal Auth0-protected Render Free deployment connected to ChatGPT Web, with real Canvas queries and post-idle recovery verified. See [actual remote deployment status](docs/phase6-2-render-deployment.md) and the [HTTP architecture guide](docs/phase6-remote-mcp.md).

## Architecture

```text
Codex
├── Canvas workflow skills
└── Canvas Student plugin → stdio MCP
                              ↓
                         Application services
                              ↓
                         Domain and ports
                              ↓
                         Canvas infrastructure
                         ├── Canvas REST API
                         └── Secure file layer
```

The MCP adapter validates inputs and projects safe results; Canvas HTTP and file policy live in infrastructure. The [local MCP guide](docs/local-mcp.md) and [personal plugin guide](docs/personal-plugin.md) describe the implemented surface. See the [architecture decisions](docs/architecture/decisions/) for deeper boundaries; the [earlier architecture blueprint](docs/architecture/overview.md) preserves historical proposals.

## What it does

The server reads the signed-in student's profile, courses, assignments and full assignment context, upcoming and overdue work, modules, submission status, announcements, course calendar events, own course grade, and course files. Assignment context can include instructions, rubric, own submission, attachments, and related module items when Canvas makes them available. Missing or partial data is reported rather than filled in.

For a course file, the production path is:

```text
Canvas Files API → infrastructure-private public_url capability
→ anonymous bounded download → MIME and byte-format validation
→ SHA-256 → managed local artifact
```

The capability URL is not returned by MCP. The downloaded artifact remains `trust=untrusted`; inspecting it does not authorize executing code, macros, or embedded links. See the [secure file guide](docs/secure-file-layer.md) and [production download decision](docs/architecture/decisions/ADR-008-public-url-primary-download.md).

## MCP tools

All 15 tools read Canvas. Only `canvas_download_file` creates a managed local file, and the personal Codex configuration prompts for approval before that call.

| Tool | Purpose | Canvas mutation? | Local side effect? |
| --- | --- | --- | --- |
| `canvas_get_profile` | Connected student profile | No | No |
| `canvas_list_courses` | Enrolled course discovery | No | No |
| `canvas_list_assignments` | Assignment and status rows in a course | No | No |
| `canvas_get_assignment` | One assignment's details | No | No |
| `canvas_get_assignment_context` | Course, assignment, submission, rubric and related materials | No | No |
| `canvas_get_upcoming` | Upcoming cross-course workload | No | No |
| `canvas_get_overdue` | Overdue cross-course workload | No | No |
| `canvas_list_modules` | Course modules | No | No |
| `canvas_list_module_items` | Module entries and file references | No | No |
| `canvas_list_announcements` | Course announcements | No | No |
| `canvas_list_calendar_events` | Course calendar events | No | No |
| `canvas_get_course_grade` | Own available grade summary | No | No |
| `canvas_list_files` | Course file discovery | No | No |
| `canvas_get_file_metadata` | Authorized file metadata | No | No |
| `canvas_download_file` | Secure download to managed storage | No | Yes |

The [MCP guide](docs/local-mcp.md) describes inputs, result coverage, and approval behavior.

## Codex skills

| Skill | Use |
| --- | --- |
| `canvas-navigation` | Resolve course, assignment, module and file names to Canvas entities. |
| `assignment-workflow` | Obtain complete verified assignment context before coursework help. |
| `course-materials` | Locate relevant files, inspect metadata and retrieve approved materials. |
| `study-overview` | Summarize deadlines, submissions and workload across courses. |

Codex resolves IDs from names and context; students do not need to provide Canvas IDs.

## Security model

- The installed personal plugin reads its Canvas token from a dedicated Windows Credential Manager generic credential. The parent Codex process does not need a token environment variable. The environment-token provider remains available for development and manual smoke tests. Tokens are never part of MCP results or repository files.
- Canvas assignment text, rubrics, announcements, HTML, filenames and downloaded bytes are external untrusted data, even when they contain instructions addressed to an assistant.
- File tools accept validated course/file references, not arbitrary URLs, destinations or credentials. The secure downloader confines origins and redirects, enforces size and time limits, validates MIME and bytes, and stores files under a controlled local root with a SHA-256 digest. Anonymous capability requests receive no Canvas Bearer token.
- Windows Credential Manager protects the credential for the signed-in Windows user. Another process running as that same user is **not** a separate trust boundary and may read the generic credential.
- Narxoz uses split-horizon DNS. The personal launcher uses `CANVAS_BASE_URL=https://canvas.narxoz.kz` and the independently verified `CANVAS_TRUSTED_PRIVATE_IPS=192.168.4.200` for its internal network. Outside that network, valid global DNS results work without a static public-IP setting. The trusted private IP is Narxoz-specific and is not required for other Canvas installations.

See the [personal plugin guide](docs/personal-plugin.md), [security architecture](docs/architecture/security.md), and [split-horizon policy](docs/split-horizon-canvas.md).

## Requirements and installation

- Python 3.11 or newer (see [`pyproject.toml`](pyproject.toml)).
- Windows for the tested personal credential store, launcher and managed file storage.
- Codex with local plugin and MCP support.
- A Canvas personal access token with access to the student's account.

Clone the repository and install its development dependencies in PowerShell:

```powershell
git clone git@github.com:ZxZxZ143/canvas_mcp.git
Set-Location canvas_mcp
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -e '.[dev]'
```

The tested development checkout is `E:\canvas_mcp`. The commands below use the current checkout path and bind the plugin to its installed Python executable.

## Configuration and credentials

The environment provider takes nonsecret settings from process environment; [`.env.example`](.env.example) is a reference and is not loaded automatically. The installed personal launcher supplies its tested Narxoz settings.

| Setting | Meaning |
| --- | --- |
| `CANVAS_BASE_URL` | Required exact HTTPS Canvas origin. |
| `CANVAS_TRUSTED_PRIVATE_IPS` | Optional operator-vetted exact private IPs for split-horizon DNS. |
| `CANVAS_DOWNLOAD_ORIGINS` | Optional approved external HTTPS file origins; empty means no off-origin downloads. |
| `DOWNLOAD_DIRECTORY` | Dedicated managed local download root outside a repository; required for downloads. |
| `REQUEST_TIMEOUT` | Optional request timeout in seconds; default 20. |
| `DOWNLOAD_TIMEOUT` | Optional whole-download timeout in seconds; default 120. |

On this Narxoz installation, the launcher sets `CANVAS_BASE_URL=https://canvas.narxoz.kz`, `CANVAS_TRUSTED_PRIVATE_IPS=192.168.4.200`, and `DOWNLOAD_DIRECTORY=C:\canvas_mcp_runtime\plugin_downloads`. These are local operator settings, not MCP tool arguments. Other installations must review their own origin and network policy.

Configure the personal credential from a PowerShell terminal. Wait for the hidden prompt; do not pass a token as an argument or put it in a file:

```powershell
& .\.venv\Scripts\python.exe -m canvas_mcp.credentials_cli configure
& .\.venv\Scripts\python.exe -m canvas_mcp.credentials_cli status
```

`configure` validates the token against Canvas before storing it in Windows Credential Manager. `status` reports availability only. To remove this project's credential:

```powershell
& .\.venv\Scripts\python.exe -m canvas_mcp.credentials_cli logout
```

## Running the MCP locally

The stdio server entry point is `python -m canvas_mcp.mcp_server`. The installed plugin starts it through [`Start-CanvasMcp.ps1`](plugins/canvas-student/scripts/Start-CanvasMcp.ps1), which selects Windows credentials and clears any inherited `CANVAS_ACCESS_TOKEN` in the child environment. For a temporary, manual live smoke with a hidden token prompt, use:

```powershell
& .\scripts\Invoke-CanvasMcpLive.ps1 -Live -Mode Smoke
```

The manual helper uses a process-scoped environment token and clears it afterward. It is separate from the persistent plugin credential workflow. See the [local MCP guide](docs/local-mcp.md).

## Installing the personal Codex plugin

The package source is [`plugins/canvas-student/`](plugins/canvas-student/), identity `canvas-student`, version `0.1.0`. The repository includes a [local marketplace manifest](.agents/plugins/marketplace.json) whose plugin source is relative to the checkout root. From the checkout root in PowerShell, write the nonsecret machine-local Python binding, register this checkout as a marketplace, and install:

```powershell
$repo = (Get-Location).Path
$bindingDir = Join-Path $env:LOCALAPPDATA 'CanvasStudent'
New-Item -ItemType Directory -Force -Path $bindingDir | Out-Null
@{ python = (Resolve-Path .\.venv\Scripts\python.exe).Path } |
    ConvertTo-Json |
    Set-Content -LiteralPath (Join-Path $bindingDir 'binding.json') -Encoding utf8
codex plugin marketplace add $repo
codex plugin marketplace list
codex plugin add canvas-student@canvas-student-local
```

The already-tested installation on this machine uses an existing marketplace named `personal`; its installation selector is `canvas-student@personal`. For a fresh checkout using the repository-local marketplace above, add the following plugin-scoped settings to `%USERPROFILE%\.codex\config.toml` to approve ordinary reads and prompt for downloads:

```toml
[plugins."canvas-student@canvas-student-local".mcp_servers.canvas_student]
default_tools_approval_mode = "approve"
tool_timeout_sec = 180

[plugins."canvas-student@canvas-student-local".mcp_servers.canvas_student.tools.canvas_download_file]
approval_mode = "prompt"
```

Start a fresh Codex session after installation. Recheck the download prompt setting after reinstalling the plugin: reinstall may remove its nested configuration. See the [personal plugin guide](docs/personal-plugin.md) for the credential, binding, and approval boundaries. This is a personal local package, not a public marketplace submission.

## Example requests

- “What Canvas courses do I have?”
- “What coursework is due this week?”
- “Explain my next assignment.”
- “Find the course material related to this assignment.”
- “Download and explain that PDF.” (Codex prompts before download.)

## Development and verification

```powershell
& .\.venv\Scripts\python.exe -m pytest
& .\.venv\Scripts\python.exe -m mypy src
& .\.venv\Scripts\python.exe -m ruff check .
& .\.venv\Scripts\python.exe -m ruff format --check .
& .\.venv\Scripts\python.exe -m build
& .\.venv\Scripts\python.exe -m pip check
```

The tests use synthetic fixtures and need no live Canvas token. Live checks are opt-in and credential-sensitive. See the [test guides](tests/), [personal plugin validation](docs/personal-plugin-validation.md), and [architecture testing notes](docs/architecture/testing.md).

## Project structure

```text
src/canvas_mcp/
├── domain/          normalized records, IDs and errors
├── application/     coursework queries and file workflows
├── ports/           external capability contracts
├── infrastructure/  Canvas, credentials, network and files
├── mcp/             bounded result projection
└── mcp_server.py    15-tool stdio adapter
plugins/canvas-student/  personal Codex package and four skills
skills/                  source workflow guidance
docs/                    design, decisions and validation
scripts/                 opt-in local smoke and diagnostics
tests/                   unit, contract, integration and security tests
```

## Known limitations

Canvas access is read-only: there is no assignment submission, upload, comment, grade change or enrollment change. The HTTP adapter supports a personal Auth0 resource server. Render Free Web Service is deployed and linked to ChatGPT Web; real Canvas smoke, profile, courses, upcoming and assignment context passed. After more than 16 idle minutes, a new container and fresh Canvas queries were verified following manual OAuth reconnect. This is a personal beta with sleep delays and expiring login, not an always-on service. See [Render setup and actual status](docs/phase6-2-render-deployment.md) and [resource-server architecture and historical Railway evaluation](docs/phase6-2-remote-personal.md). Remote downloads, multi-user deployment, and public marketplace publication are unavailable. Secure local credential and file storage are Windows-first. The local plugin configuration is Narxoz-specific, and another same-user process can access its Windows generic credential.

## Roadmap

The [roadmap](docs/roadmap.md) reserves Phase 6.3 for remote files, Phase 6.4 for an optional mobile companion, Phase 7 for safe Canvas writes and Phase 8 for a public multi-user plugin. Those capabilities are unavailable; Phase 6.3 does not begin automatically.
