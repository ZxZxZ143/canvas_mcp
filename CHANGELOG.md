# Changelog

## Phase 7.1 implementation

- Added read-only `canvas_get_workload` with bounded calendar-day horizons,
  cross-course partial results, availability and deterministic metadata features.
- Added a study-planner skill, synthetic acceptance cases and timezone/aggregate regressions.
- Planning and uncertain effort estimates stay in ChatGPT; no server LLM, file crawl,
  schedule database, background polling, new credentials or Canvas write scopes.

## 0.1.0 — Personal Canvas Student plugin

- Built the Canvas connection foundation and normalized academic read services.
- Added assignment context, workload, modules, announcements, calendar, grades, and own submission status.
- Added bounded secure file metadata and managed downloads through anonymous Canvas file capabilities.
- Exposed 15 read-only Canvas tools through a local stdio MCP server.
- Packaged four Codex coursework skills and a Windows personal plugin launcher.
- Added Windows Credential Manager setup and Narxoz split-horizon network configuration.
- Validated the personal plugin against a live Canvas installation and fresh Codex session.
