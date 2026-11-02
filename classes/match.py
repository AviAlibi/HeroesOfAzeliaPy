"""Starter decks, legal move discovery, and a public-information AI."""

from collections import Counter
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, dataclass
from itertools import combinations
import random

from assets.asset_import_manager import CardDefinition, Catalog
from classes.game import Game, RuleError


@dataclass(frozen=True)
class Starter:
    id: str
    name: str
    description: str
    domains: tuple[str, ...]
    cards: tuple[str, ...]


def _copies(**counts: int) -> tuple[str, ...]:
    return tuple(card_id for card_id, count in counts.items() for _ in range(count))


STARTERS = (
    Starter("wildwood", "Wildwood Covenant", "Resilient guardians, living armor, and an overwhelming woodland rally.", ("land",),
            _copies(forest=10, mountain=2, lanternwood=2, emberfall_ridge=2,
                    briar_cub=3, mossback_turtle=3, thornstalker=3, briarheart_dryad=3,
                    sunstone_lion=3, elderbloom_titan=1, stone_shelter=2, rally_the_wilds=2,
                    purifying_light=2, granite_oath=2)),
    Starter("depths", "Court of the Deep", "Submerge your hunters, weather the assault, and awaken a leviathan.", ("sea",),
            _copies(beach=3, duskmire=3, whispering_marsh=3, brineveil_lagoon=3,
                    coral_reef=2, underwater_city=2,
                    reef_dartfish=3, shellback_crab=3, kelp_grazer=3, tidal_serpent=3,
                    pearl_healer=2, pearlscale_leviathan=1, abyss_monarch=1,
                    tidal_gift=2, healing_rain=2, frost_lance=2, gills_of_the_deep=2)),
    Starter("skies", "Skybound Accord", "Fast aerial attackers, protective wards, and lightning from above.", ("air",),
            _copies(floating_island=12, skyglass_spire=2, cloudbreak_citadel=2,
                    cloudwing_sparrow=3, zephyr_courier=3, stormcrest_hawk=3, spire_griffin=3,
                    windweaver=2, lightning_roc=1, horizon_dragon=1,
                    grounding_bolt=2, windcall=2, prismatic_aegis=2, lead_feathers=2)),
    Starter("marsh", "Marshlight Circle", "Coastal creatures, cleansing tides, and venom that wears enemies down.", ("land", "sea"),
            _copies(forest=5, beach=3, duskmire=3, whispering_marsh=3, coral_reef=2,
                    shoreline_ranger=3, marsh_lurker=3, delta_herbalist=2, tidewood_stag=2,
                    mangrove_guardian=2, briar_cub=2, current_dancer=2,
                    venom_dart=2, quenching_tide=2, venom_vines=2, evergreen_pact=2)),
    Starter("highlands", "Thunderpeak Legion", "Charging highland beasts, mighty guards, and wind-carried lifesteal.", ("land", "air"),
            _copies(forest=5, floating_island=5, stormpeak_cliffs=2, farwatch_summit=2, wind_carved_reach=2,
                    briar_cub=3, cliffside_ranger=3, thunderhoof_charger=3, mistfall_drake=3,
                    windroot_treant=2, sunwing_lion=2,
                    root_snare=2, stone_shelter=2,
                    banner_of_embers=2, wings_of_the_zephyr=2)),
    Starter("tempest", "Tempest Voyage", "Sea and sky join forces: evasive creatures, soaking curses, and storm magic.", ("sea", "air"),
            _copies(brineveil_lagoon=3, floating_island=5, beach=2, duskmire=2, tempest_strait=2, coral_reef=2,
                    reef_dartfish=3, tempest_gull=3, sprayfin_glider=3, squall_serpent=3,
                    stormtide_oracle=2, aurora_whale=2,
                    grounding_bolt=2, frost_lance=2, undertow_shroud=2, unravel=2)),
)


def starter_deck(catalog: Catalog, starter_id: str, rng: random.Random) -> list[str]:
    starter = next((deck for deck in STARTERS if deck.id == starter_id), None)
    if starter is None:
        raise RuleError("Choose one of the listed starter decks")
    cards = list(starter.cards)
    if len(cards) != 40:
        raise ValueError(f"{starter.id}: starter must contain 40 cards")
    for card_id in cards:
        catalog.card(card_id)
    terrains = [card_id for card_id in cards if catalog.card(card_id).kind == "terrain"]
    if len(terrains) != 16:
        raise ValueError(f"{starter.id}: starter must contain 16 terrains")
    # A shared opening rule avoids unplayable zero-resource hands for either side.
    basics = [card_id for card_id in terrains if catalog.card(card_id).cost == 0]
    rng.shuffle(cards)
    rng.shuffle(basics)
    if len(basics) < 2:
        raise ValueError(f"{starter.id}: starter needs at least two free terrains")
    required = set(starter.domains)
    opening = list(max(
        combinations(basics, 2),
        key=lambda pair: len(required & {
            domain for card_id in pair for domain in catalog.card(card_id).terrain_types
        }),
    ))
    for card_id in opening:
        cards.remove(card_id)
    supported = {domain for card_id in opening for domain in catalog.card(card_id).terrain_types}
    early = [
        card_id for card_id in cards
        if catalog.card(card_id).kind == "creature" and catalog.card(card_id).cost <= 3
        and set(catalog.card(card_id).requires) <= supported
    ]
    if len(early) < 3:
        raise ValueError(f"{starter.id}: starter needs at least three early creatures")
    inexpensive = next((card_id for card_id in early if catalog.card(card_id).cost <= 2), None)
    if inexpensive is None:
        raise ValueError(f"{starter.id}: starter needs a supported inexpensive creature")
    early.remove(inexpensive)
    for card_id in [inexpensive, *early[:2]]:
        cards.remove(card_id)
        opening.append(card_id)
    rng.shuffle(opening)
    return opening + cards


@dataclass(frozen=True)
class Action:
    kind: str
    card: str | None = None
    source: int | None = None
    target: int | None = None

    def public(self) -> dict[str, object]:
        return {key: value for key, value in asdict(self).items() if value is not None}


def execute(game: Game, action: Action) -> None:
    if action.kind == "play" and action.card is not None:
        game.play(game.active, action.card, action.target)
    elif action.kind == "attack" and action.source is not None:
        game.attack(game.active, action.source, action.target)
    elif action.kind == "sacrifice" and action.source is not None:
        game.remove_terrain(game.active, action.source)
    elif action.kind == "dismiss" and action.source is not None:
        game.dispel(game.active, action.source)
    elif action.kind == "end_turn":
        game.end_turn(game.active)
    else:
        raise RuleError("Unknown or incomplete action")


def finished(game: Game) -> bool:
    return any(player.health <= 0 for player in game.players)


def preview(game: Game, action: Action) -> Game:
    trial = deepcopy(game, {id(game.catalog): game.catalog})
    execute(trial, action)
    return trial


def legal_moves(game: Game) -> tuple[list[Action], dict[str, str]]:
    """Use actual rule execution on copies; never duplicate legality in the UI or AI."""
    if finished(game):
        return [], {}
    player = game.players[game.active]
    units = [unit.id for board in game.players for unit in board.creatures.values()]
    candidates: list[tuple[Action, str]] = []
    for card_id in dict.fromkeys(player.hand):
        card = game.catalog.card(card_id)
        targeted = any(effect.target in {"ally", "enemy", "any"} for effect in card.effects)
        targets = units if targeted and units else [None]
        candidates.extend((Action("play", card=card_id, target=target), card_id) for target in targets)
    for unit in player.creatures.values():
        candidates.append((Action("attack", source=unit.id), f"unit:{unit.id}"))
        candidates.extend(
            (Action("attack", source=unit.id, target=enemy.id), f"unit:{unit.id}")
            for enemy in game.players[1 - game.active].creatures.values()
        )
    legal: list[Action] = []
    errors: dict[str, str] = {}
    successful: set[str] = set()
    for action, key in candidates:
        try:
            preview(game, action)
        except RuleError as error:
            errors.setdefault(key, str(error))
        else:
            legal.append(action)
            successful.add(key)
    return legal, {key: reason for key, reason in errors.items() if key not in successful}


def _position_value(game: Game, owner: int) -> float:
    def value(index: int) -> float:
        player = game.players[index]
        result = player.health * 1.5
        for unit in player.creatures.values():
            result += unit.attack * 1.8 + unit.health + 1.5
            result += sum(
                {"flying": 1.8, "water_breathing": 1.5, "guard": 1, "ward": 1,
                 "lifesteal": 1.5, "haste": 0.3, "cant_attack": -3,
                 "no_healing": -0.5}.get(keyword, 0)
                for keyword in unit.keywords
            )
            result += sum(
                status.definition.healing_per_turn - 1.5 * status.definition.damage_per_turn
                for status in unit.statuses.values()
            )
        return result
    return value(owner) - value(1 - owner)


def ai_turn(game: Game, on_action: Callable[[Action, int], None] | None = None) -> None:
    """Greedy one-ply AI uses board information, not the opponent's hidden hand."""
    owner = game.active
    for _ in range(64):
        if finished(game):
            return
        actions, _ = legal_moves(game)
        baseline = _position_value(game, owner)
        best: Action | None = None
        best_score = 0.05
        for action in actions:
            trial = preview(game, action)
            score = _position_value(trial, owner) - baseline
            if trial.winner == owner:
                score += 10000
            if action.kind == "play" and action.card is not None:
                card = game.catalog.card(action.card)
                score -= card.cost * 0.08
                if card.kind == "terrain":
                    missing = set(card.terrain_types) - game.terrain_types(owner)
                    score += 3 + len(missing)
            if score > best_score:
                best, best_score = action, score
        if best is None:
            game.end_turn(owner)
            if on_action is not None:
                on_action(Action("end_turn"), owner)
            return
        execute(game, best)
        if on_action is not None:
            on_action(best, owner)
    raise RuntimeError("AI exceeded its finite turn action budget")


def card_data(card: CardDefinition) -> dict[str, object]:
    return {
        "id": card.id, "name": card.name, "kind": card.kind,
        "description": card.description, "rarity": card.rarity, "cost": card.cost,
        "requires": list(card.requires), "domains": list(card.terrain_types or card.requires),
        "attack": card.attack, "health": card.health, "keywords": list(card.keywords),
        "effects": [asdict(effect) for effect in card.effects],
    }


class Match:
    """One local table, with a privacy handoff between hotseat turns."""

    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog
        self.game: Game | None = None
        self.mode = "ai"
        self.decks: tuple[str, str] = ("wildwood", "depths")
        self.revision = 0
        self.handoff = False

    def start(self, mode: str, first: str, second: str, seed: int | None = None) -> None:
        if mode not in {"ai", "hotseat"}:
            raise RuleError("Mode must be AI or hotseat")
        rng = random.Random(seed)
        game = Game(self.catalog, (
            starter_deck(self.catalog, first, rng), starter_deck(self.catalog, second, rng),
        ))
        self.game, self.mode, self.decks = game, mode, (first, second)
        self.handoff = mode == "hotseat"
        self.revision += 1
        game.events.append("The duel begins with Player 0.")

    def act(self, action: Action, revision: int) -> list[dict[str, object]]:
        if revision != self.revision:
            raise RuleError("This table changed. The board has been refreshed; choose your move again.")
        if self.game is None:
            raise RuleError("Start a duel first")
        if finished(self.game):
            raise RuleError("The duel has ended. Start a new duel to play again.")
        frames: list[dict[str, object]] = []

        def capture(move: Action, owner: int) -> None:
            snapshot = self.state()["match"]
            frames.append({
                "action": move.public(), "owner": owner, "match": snapshot,
                "card": card_data(self.catalog.card(move.card)) if move.card is not None else None,
            })

        if action.kind == "ready":
            if not self.handoff:
                raise RuleError("There is no handoff to reveal")
            self.handoff = False
        else:
            if self.handoff:
                raise RuleError("Pass the screen and reveal the next player's hand first")
            if self.mode == "ai" and self.game.active != 0:
                raise RuleError("The opponent is taking its turn")
            owner = self.game.active
            execute(self.game, action)
            if self.mode != "hotseat" or action.kind != "end_turn":
                capture(action, owner)
            if action.kind == "end_turn" and not finished(self.game):
                if self.mode == "ai":
                    ai_turn(self.game, capture)
                else:
                    self.handoff = True
        self.revision += 1
        return frames

    def state(self) -> dict[str, object]:
        result: dict[str, object] = {
            "revision": self.revision,
            "starters": [
                {"id": deck.id, "name": deck.name, "description": deck.description,
                 "domains": list(deck.domains), "count": len(deck.cards),
                 "cards": [{"card": card_data(self.catalog.card(key)), "count": count}
                           for key, count in Counter(deck.cards).items()]}
                for deck in STARTERS
            ],
            "match": None,
        }
        game = self.game
        if game is None:
            return result
        viewer = game.active if self.mode == "hotseat" else 0
        actions, blocked = (
            ([], {}) if self.handoff or (self.mode == "ai" and game.active != 0)
            else legal_moves(game)
        )
        players: list[dict[str, object]] = []
        for owner, player in enumerate(game.players):
            creatures = []
            for unit in player.creatures.values():
                creatures.append({
                    "id": unit.id, "owner": owner, "card": card_data(unit.card),
                    "attack": unit.attack, "health": unit.health, "maxHealth": unit.max_health,
                    "ready": unit.ready or "haste" in unit.keywords, "attacked": unit.attacked,
                    "keywords": sorted(unit.keywords),
                    "statuses": [
                        {"id": status.definition.id, "name": status.definition.name,
                         "kind": status.definition.kind, "description": status.definition.description,
                         "remaining": status.remaining, "source": status.source}
                        for status in unit.statuses.values()
                    ],
                })
            players.append({
                "owner": owner, "health": player.health, "mana": player.mana,
                "deckName": next(deck.name for deck in STARTERS if deck.id == self.decks[owner]),
                "deckCount": len(player.deck), "handCount": len(player.hand),
                "hand": [card_data(self.catalog.card(key)) for key in player.hand]
                if owner == viewer and not self.handoff else None,
                "terrains": [{"id": key, "card": card_data(card)} for key, card in player.terrains.items()],
                "creatures": creatures, "terrainPlayed": player.terrain_played,
                "graveyard": [card_data(self.catalog.card(key)) for key in player.graveyard],
            })
        result["match"] = {
            "mode": self.mode, "viewer": viewer, "active": game.active, "turn": game.turn,
            "handoff": self.handoff, "over": finished(game), "winner": game.winner,
            "players": players, "actions": [action.public() for action in actions],
            "blocked": blocked, "events": game.events[-100:],
            "enchants": [
                {"id": enchant.id, "owner": enchant.owner, "target": enchant.target,
                 "card": card_data(self.catalog.card(enchant.card_id))}
                for enchant in game.enchants.values()
            ],
        }
        return result
