from pathlib import Path

from assets.asset_import_manager import CardDefinition, load_catalog


def load_cards(root: Path | None = None) -> dict[str, CardDefinition]:
    return load_catalog(root).cards