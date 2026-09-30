"""Compare built and installed files to reviewed sources; inspect migration packaging."""

from pathlib import Path
import zipfile
import canvas_mcp

root = Path(__file__).resolve().parents[1]
installed = Path(canvas_mcp.__file__).parent
wheel = root / "dist/phase72/canvas_mcp-0.1.0-py3-none-any.whl"
paths = sorted((root / "src/canvas_mcp").rglob("*.py"))
with zipfile.ZipFile(wheel) as archive:
    for source in paths:
        relative = source.relative_to(root / "src")
        assert source.read_bytes() == archive.read(relative.as_posix())
        assert (
            source.read_bytes()
            == (installed / source.relative_to(root / "src/canvas_mcp")).read_bytes()
        )
    relative = "canvas_mcp/infrastructure/state/001.sql"
    assert archive.read(relative) == (root / "src" / relative).read_bytes()
    assert archive.read(relative) == (installed / "infrastructure/state/001.sql").read_bytes()
print(f"Verified {len(paths)} Python sources plus migration in wheel and installed package.")
print(f"Installed package: {installed}")
