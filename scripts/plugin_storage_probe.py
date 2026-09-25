"""Check dedicated managed storage roots without Canvas or credential access."""

import os
from pathlib import Path

from canvas_mcp.domain.models import AccessScope, ConnectionId, PrincipalId
from canvas_mcp.infrastructure.config.schema import DeploymentSettings
from canvas_mcp.infrastructure.files.storage import ManagedStore


def main() -> None:
    roots = {
        "user_local": Path(os.environ["LOCALAPPDATA"]) / "CanvasStudent" / "downloads",
        "machine_local": Path("C:/canvas_mcp_runtime/plugin_downloads"),
    }
    for label, root in roots.items():
        scope = AccessScope(PrincipalId("storage-probe"), ConnectionId(label))
        store = ManagedStore(
            DeploymentSettings("https://canvas.narxoz.kz", download_directory=root), scope
        )
        try:
            store._start()
            print(f"{label}=ready")
        except Exception as error:
            print(f"{label}={type(error).__name__}")
        finally:
            try:
                store.close()
            except Exception as error:
                print(f"{label}_close={type(error).__name__}")


if __name__ == "__main__":
    main()
