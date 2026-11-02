from pathlib import Path

from assets.asset_import_manager import StatusDefinition, load_catalog


def load_buffs(root: Path | None = None) -> dict[str, StatusDefinition]:
    return {key: status for key, status in load_catalog(root).statuses.items() if status.kind == "buff"}