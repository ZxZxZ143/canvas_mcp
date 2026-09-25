"""Manual opt-in profile/course smoke test: python -m canvas_mcp.smoke --live."""

import argparse
import asyncio

from canvas_mcp.composition import open_canvas_connection
from canvas_mcp.domain.errors import ApplicationError
from canvas_mcp.domain.models import PageRequest


async def run(*, debug: bool = False) -> int:
    try:
        async with open_canvas_connection() as connection:
            profile = await connection.get_profile()
            seen: set[str] = set()
            page = PageRequest()
            warnings = len(profile.warnings)
            warning_codes = {(item.component, item.code) for item in profile.warnings}
            while True:
                result = await connection.list_courses(page)
                seen.update(item.id for item in result.data.items)
                warnings += len(result.warnings)
                warning_codes.update((item.component, item.code) for item in result.warnings)
                if result.data.next_cursor is None:
                    break
                page = PageRequest(page.limit, result.data.next_cursor)
            print(f"Connected as: {profile.data.display_name.text}")
            print(f"Courses found: {len(seen)}")
            if warnings:
                print(f"Optional metadata warnings: {warnings}")
            if debug:
                for component, code in sorted(warning_codes):
                    print(f"Metadata warning: {component}/{code}")
        return 0
    except ApplicationError as error:
        print(f"Canvas connection failed: {error.code}")
        return 1
    except Exception:  # noqa: BLE001 - CLI must never render an unsafe traceback
        print("Canvas connection failed: internal_error")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="explicitly permit real Canvas reads")
    parser.add_argument("--debug", action="store_true", help="fixed warning codes only")
    args = parser.parse_args()
    if not args.live:
        parser.error("the optional smoke test requires --live")
    try:
        return asyncio.run(run(debug=args.debug))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
