"""Validate the catalog or run a small, legal terrain/status demonstration."""

import argparse

from assets.asset_import_manager import AssetError, Catalog, load_catalog
from classes.game import Game


def demo(catalog: Catalog) -> Game:
    opening = ["forest", "briar_cub", "coral_reef", "beach", "tidal_gift"]
    game = Game(catalog, (opening + ["forest"] * 10, ["forest"] * 15))
    game.play(0, "forest")
    creature_id = game.play(0, "briar_cub")
    if creature_id is None:
        raise RuntimeError("The demo creature did not enter play")
    game.end_turn(0)
    game.play(1, "forest")
    game.end_turn(1)
    sea_id = game.play(0, "coral_reef")
    if sea_id is None:
        raise RuntimeError("The demo terrain did not enter play")
    unit = game.creature(creature_id)
    print(f"Coral Reef played: Water Breathing = {'water_breathing' in unit.statuses}")
    game.remove_terrain(0, sea_id)
    print(f"Last sea terrain lost: Water Breathing = {'water_breathing' in unit.statuses}")
    game.end_turn(0)
    game.end_turn(1)
    game.play(0, "beach")
    print(f"Sea terrain restored: Water Breathing = {'water_breathing' in unit.statuses}")
    game.play(0, "tidal_gift", creature_id)
    print(f"Tidal Gift applied again: Water Breathing = {'water_breathing' in unit.statuses}")
    return game


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="demonstrate terrain-dependent buffs")
    args = parser.parse_args()
    try:
        catalog = load_catalog()
    except AssetError as error:
        parser.exit(2, f"Invalid catalog: {error}\n")
    creatures = sum(card.kind == "creature" for card in catalog.cards.values())
    print(
        f"Validated: {len(catalog.terrains)} terrains, {creatures} creatures, "
        f"{len(catalog.cards) - creatures} spells, {len(catalog.enchants)} enchants, "
        f"{sum(status.kind == 'buff' for status in catalog.statuses.values())} buffs, "
        f"{sum(status.kind == 'debuff' for status in catalog.statuses.values())} debuffs"
    )
    if args.demo:
        demo(catalog)


if __name__ == "__main__":
    main()