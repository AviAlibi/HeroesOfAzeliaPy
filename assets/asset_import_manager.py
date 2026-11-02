"""Load and validate the interconnected JSON card catalog."""

from dataclasses import dataclass
import json
from pathlib import Path


TERRAIN_TYPES = frozenset({"land", "sea", "air"})
RARITIES = frozenset({"common", "uncommon", "rare", "epic", "mythical"})
KEYWORDS = frozenset({
    "water_breathing", "flying", "guard", "haste", "lifesteal", "ward",
    "grounded", "cant_attack", "no_healing", "disarmed",
})
TARGETS = frozenset({"self", "ally", "enemy", "any", "all_allies", "all_enemies"})


class AssetError(ValueError):
    """An asset has invalid data or a broken cross-reference."""


@dataclass(frozen=True)
class Effect:
    action: str
    target: str
    status: str | None = None
    amount: int = 0


@dataclass(frozen=True)
class StatusDefinition:
    id: str
    kind: str
    name: str
    description: str
    duration: int | None
    requires: tuple[str, ...]
    attack: int
    health: int
    keywords: tuple[str, ...]
    damage_per_turn: int
    healing_per_turn: int
    removes: tuple[str, ...]


@dataclass(frozen=True)
class CardDefinition:
    id: str
    kind: str
    name: str
    description: str
    rarity: str
    cost: int
    requires: tuple[str, ...]
    effects: tuple[Effect, ...]
    attack: int = 0
    health: int = 0
    keywords: tuple[str, ...] = ()
    terrain_types: tuple[str, ...] = ()
    value: int = 0


@dataclass(frozen=True)
class Catalog:
    terrains: dict[str, CardDefinition]
    cards: dict[str, CardDefinition]
    enchants: dict[str, CardDefinition]
    statuses: dict[str, StatusDefinition]

    def card(self, card_id: str) -> CardDefinition:
        for collection in (self.terrains, self.cards, self.enchants):
            if card_id in collection:
                return collection[card_id]
        raise AssetError(f"Unknown card: {card_id}")


def _object(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise AssetError(f"{context}: expected an object")
    return {key: item for key, item in value.items()}


def _text(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AssetError(f"{context}: expected nonempty text")
    return value


def _number(value: object, context: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise AssetError(f"{context}: expected an integer >= {minimum}")
    return value


def _strings(value: object, context: str, allowed: frozenset[str] | None = None) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise AssetError(f"{context}: expected a list")
    result = tuple(_text(item, context) for item in value)
    if len(set(result)) != len(result):
        raise AssetError(f"{context}: duplicate entries")
    if allowed is not None and not set(result) <= allowed:
        raise AssetError(f"{context}: unsupported entries {set(result) - allowed}")
    return result


def _effects(value: object, context: str) -> tuple[Effect, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise AssetError(f"{context}: onPlay must be a list or null")
    result: list[Effect] = []
    for raw in value:
        data = _object(raw, context)
        action = _text(data.get("action"), context)
        target = _text(data.get("target"), context)
        if action not in {"apply", "remove", "damage", "heal", "dispel"} or target not in TARGETS:
            raise AssetError(f"{context}: unsupported effect {action}/{target}")
        allowed = {"action", "target"}
        status = None
        amount = 0
        if action in {"apply", "remove"}:
            status = _text(data.get("status"), context)
            allowed.add("status")
        if action in {"damage", "heal"}:
            amount = _number(data.get("amount"), context, 1)
            allowed.add("amount")
        if set(data) - allowed:
            raise AssetError(f"{context}: unknown effect fields {set(data) - allowed}")
        result.append(Effect(action, target, status, amount))
    return tuple(result)


def _status(card_id: str, kind: str, data: dict[str, object]) -> StatusDefinition:
    allowed = {
        "name", "description", "duration", "requiresTerrain", "attack", "health",
        "keywords", "damagePerTurn", "healingPerTurn", "removes",
    }
    if set(data) - allowed:
        raise AssetError(f"{card_id}: unknown status fields {set(data) - allowed}")
    duration = data.get("duration")
    if duration is not None:
        duration = _number(duration, card_id, 1)
    attack = data.get("attack", 0)
    health = data.get("health", 0)
    if type(attack) is not int or type(health) is not int:
        raise AssetError(f"{card_id}: attack and health modifiers must be integers")
    return StatusDefinition(
        card_id, kind, _text(data.get("name"), card_id),
        _text(data.get("description"), card_id), duration,
        _strings(data.get("requiresTerrain", []), card_id, TERRAIN_TYPES),
        attack, health, _strings(data.get("keywords", []), card_id, KEYWORDS),
        _number(data.get("damagePerTurn", 0), card_id),
        _number(data.get("healingPerTurn", 0), card_id),
        _strings(data.get("removes", []), card_id),
    )


def _card(card_id: str, group: str, data: dict[str, object]) -> CardDefinition:
    kind = "terrain" if group == "terrains" else "enchant" if group == "enchants" else data.get("type")
    if kind not in {"terrain", "creature", "spell", "enchant"}:
        raise AssetError(f"{card_id}: unsupported card type {kind}")
    common = {"name", "description", "rarity", "onPlay", "onDraw"}
    allowed = common | (
        {"value", "terrainType"} if kind == "terrain" else
        {"cost", "requiresTerrain"} if kind == "enchant" else
        {"type", "cost", "requiresTerrain", "attack", "health", "keywords"}
    )
    if set(data) - allowed:
        raise AssetError(f"{card_id}: unknown card fields {set(data) - allowed}")
    if data.get("onDraw") is not None:
        raise AssetError(f"{card_id}: onDraw effects are not supported")
    rarity = _text(data.get("rarity"), card_id)
    if rarity not in RARITIES:
        raise AssetError(f"{card_id}: unsupported rarity {rarity}")
    terrains = _strings(data.get("terrainType", []), card_id, TERRAIN_TYPES)
    if kind == "terrain" and not terrains:
        raise AssetError(f"{card_id}: terrain must have at least one type")
    effects = _effects(data.get("onPlay"), card_id)
    if kind in {"spell", "enchant"} and not effects:
        raise AssetError(f"{card_id}: {kind} must have an effect")
    if kind == "terrain" and any(effect.target not in {"all_allies", "all_enemies"} for effect in effects):
        raise AssetError(f"{card_id}: terrains may only affect all_allies or all_enemies")
    if kind == "creature" and any(effect.target not in {"self", "all_allies", "all_enemies"} for effect in effects):
        raise AssetError(f"{card_id}: creature effects must not require a selected target")
    if kind in {"spell", "enchant"} and any(effect.target == "self" for effect in effects):
        raise AssetError(f"{card_id}: only creatures have a self target")
    if kind == "enchant" and any(effect.action != "apply" or effect.target not in {"ally", "enemy"} for effect in effects):
        raise AssetError(f"{card_id}: enchants must attach statuses to an ally or enemy")
    return CardDefinition(
        card_id, str(kind), _text(data.get("name"), card_id),
        _text(data.get("description"), card_id), rarity,
        _number(data.get("value"), card_id, 1) - 1 if kind == "terrain"
        else _number(data.get("cost"), card_id, 1),
        _strings(data.get("requiresTerrain", []), card_id, TERRAIN_TYPES), effects,
        _number(data.get("attack", 0), card_id),
        _number(data.get("health", 0), card_id, 1 if kind == "creature" else 0),
        _strings(data.get("keywords", []), card_id, KEYWORDS), terrains,
        _number(data.get("value", 0), card_id, 1 if kind == "terrain" else 0),
    )


def load_catalog(root: Path | None = None) -> Catalog:
    """Load assets relative to this module, never the process working directory."""
    root = root if root is not None else Path(__file__).resolve().parent
    groups: dict[str, dict[str, CardDefinition]] = {name: {} for name in ("terrains", "cards", "enchants")}
    statuses: dict[str, StatusDefinition] = {}
    seen: set[str] = set()
    names: set[str] = set()
    for group in ("buffs", "debuffs", "terrains", "cards", "enchants"):
        folder = root / group
        if not folder.is_dir():
            raise AssetError(f"Missing asset directory: {folder}")
        for path in sorted(folder.glob("*.json")):
            if path.stem.startswith("_") or path.stem == "template":
                continue
            try:
                raw: object = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as error:
                raise AssetError(f"{path}: {error}") from error
            data = _object(raw, str(path))
            if path.stem in seen:
                raise AssetError(f"{path}: duplicate asset ID")
            seen.add(path.stem)
            name = _text(data.get("name"), str(path)).casefold()
            if name in names:
                raise AssetError(f"{path}: duplicate asset name")
            names.add(name)
            if group in {"buffs", "debuffs"}:
                statuses[path.stem] = _status(path.stem, group[:-1], data)
            else:
                groups[group][path.stem] = _card(path.stem, group, data)
    for status in statuses.values():
        for removed in status.removes:
            if removed not in statuses:
                raise AssetError(f"{status.id}: unknown removed status {removed}")
    for group in groups.values():
        for card in group.values():
            for effect in card.effects:
                if effect.status is not None and effect.status not in statuses:
                    raise AssetError(f"{card.id}: unknown status {effect.status}")
    return Catalog(groups["terrains"], groups["cards"], groups["enchants"], statuses)