"""Secret, typed state settings; independent of Canvas credentials/settings."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
import re
from urllib.parse import urlsplit, parse_qs, unquote

from canvas_mcp.domain.errors import StateStoreUnavailableError


@dataclass(frozen=True, repr=False)
class StateSettings:
    url: str = field(repr=False)
    backend: str
    sqlite_path: Path | None = field(default=None, repr=False)

    @classmethod
    def load(cls, environ: Mapping[str, str], *, remote: bool = False) -> "StateSettings":
        value = environ.get("STATE_DATABASE_URL", "")
        try:
            parsed = urlsplit(value)
            if parsed.scheme == "sqlite" and not remote:
                decoded = unquote(parsed.path)
                path = Path(decoded[1:] if re.match(r"^/[A-Za-z]:[\\/]", decoded) else decoded)
                if parsed.netloc or parsed.query or parsed.fragment or not path.is_absolute():
                    raise ValueError()
                return cls(value, "sqlite", path)
            if parsed.scheme not in ("postgresql", "postgres"):
                raise ValueError()
            if not parsed.hostname or not parsed.username or not parsed.path.strip("/"):
                raise ValueError()
            if parsed.fragment or len(value) > 4096:
                raise ValueError()
            query = parse_qs(parsed.query, strict_parsing=True)
            if set(query) - {"sslmode", "sslrootcert"} or any(len(x) != 1 for x in query.values()):
                raise ValueError()
            mode = query.get("sslmode", [""])[0]
            local = parsed.hostname in ("localhost", "127.0.0.1", "::1")
            if mode != "verify-full" and not (local and not remote and mode == "disable"):
                raise ValueError()
            return cls(value, "postgresql")
        except (ValueError, TypeError):
            pass
        raise StateStoreUnavailableError() from None
