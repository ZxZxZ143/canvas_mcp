# ADR-003: Treat all coursework content as evidence, not agent authority

Status: accepted after architecture review; AGENTS and skills corrected accordingly.

Canvas can be authoritative about coursework facts while titles, descriptions, filenames, links and files remain untrusted instructions. The adapter emits bounded inert text and separately classified references; the host/agent applies trusted policy before any action. Sanitization and trust labels do not themselves prevent prompt injection.

No retrieved text may authorize secret access, arbitrary network requests, commands, policy changes or data disclosure. Download/inspection are separate; the server never executes coursework. This limits automation that would run starter code without user intent, but preserves legitimate explanation/planning workflows and a clear future action boundary.
