"""Deterministic two-player rules for the asset catalog."""

from dataclasses import dataclass, field

from assets.asset_import_manager import CardDefinition, Catalog, Effect, StatusDefinition


class RuleError(ValueError):
    """An attempted action is illegal; no resources should be spent."""


@dataclass
class AppliedStatus:
    definition: StatusDefinition
    remaining: int | None
    source: int | None = None


@dataclass
class Creature:
    id: int
    owner: int
    card: CardDefinition
    damage: int = 0
    ready: bool = False
    attacked: bool = False
    statuses: dict[str, AppliedStatus] = field(default_factory=dict)

    @property
    def attack(self) -> int:
        if "disarmed" in self.keywords:
            return 0
        return max(0, self.card.attack + sum(item.definition.attack for item in self.statuses.values()))

    @property
    def max_health(self) -> int:
        return max(0, self.card.health + sum(item.definition.health for item in self.statuses.values()))

    @property
    def health(self) -> int:
        return self.max_health - self.damage

    @property
    def keywords(self) -> set[str]:
        result = set(self.card.keywords)
        for item in self.statuses.values():
            result.update(item.definition.keywords)
        if "grounded" in result:
            result.discard("flying")
        return result


@dataclass
class Enchantment:
    id: int
    owner: int
    card_id: str
    target: int


@dataclass
class Player:
    deck: list[str]
    hand: list[str] = field(default_factory=list)
    health: int = 25
    mana: int = 0
    terrains: dict[int, CardDefinition] = field(default_factory=dict)
    creatures: dict[int, Creature] = field(default_factory=dict)
    graveyard: list[str] = field(default_factory=list)
    terrain_played: bool = False


class Game:
    """Deck order is supplied by the caller; index zero is drawn first."""

    def __init__(self, catalog: Catalog, decks: tuple[list[str], list[str]]) -> None:
        if len(decks) != 2:
            raise RuleError("A game requires two decks")
        for deck in decks:
            for card_id in deck:
                catalog.card(card_id)
        self.catalog = catalog
        self.players = [Player(list(deck)) for deck in decks]
        self.active = 0
        self.turn = 1
        self.enchants: dict[int, Enchantment] = {}
        self.events: list[str] = []
        self._next_id = 1
        for player in self.players:
            for _ in range(min(5, len(player.deck))):
                player.hand.append(player.deck.pop(0))

    @property
    def winner(self) -> int | None:
        living = [index for index, player in enumerate(self.players) if player.health > 0]
        return living[0] if len(living) == 1 else None

    def _check_player(self, owner: int) -> Player:
        if owner not in (0, 1):
            raise RuleError("Player must be 0 or 1")
        if any(player.health <= 0 for player in self.players):
            raise RuleError("The game has ended")
        if owner != self.active:
            raise RuleError("It is not this player's turn")
        return self.players[owner]

    def _id(self) -> int:
        result = self._next_id
        self._next_id += 1
        return result

    def terrain_types(self, owner: int) -> set[str]:
        if owner not in (0, 1):
            raise RuleError("Player must be 0 or 1")
        return {kind for terrain in self.players[owner].terrains.values() for kind in terrain.terrain_types}

    def _supports(self, owner: int, requires: tuple[str, ...]) -> bool:
        return set(requires) <= self.terrain_types(owner)

    def creature(self, creature_id: int) -> Creature:
        for player in self.players:
            if creature_id in player.creatures:
                return player.creatures[creature_id]
        raise RuleError(f"No living creature with ID {creature_id}")

    def _targets(self, owner: int, effect: Effect, selected: Creature | None, caster: Creature | None) -> list[Creature]:
        if effect.target == "self":
            if caster is None:
                raise RuleError("This effect requires a creature caster")
            return [caster]
        if effect.target in {"all_allies", "all_enemies"}:
            index = owner if effect.target == "all_allies" else 1 - owner
            return list(self.players[index].creatures.values())
        if selected is None:
            raise RuleError("Select a creature target")
        if effect.target == "ally" and selected.owner != owner:
            raise RuleError("Select a friendly creature")
        if effect.target == "enemy" and selected.owner == owner:
            raise RuleError("Select an enemy creature")
        if selected.owner != owner and "ward" in selected.keywords:
            raise RuleError("Ward prevents enemy spells and enchants from targeting this creature")
        return [selected]

    def play(self, owner: int, card_id: str, target: int | None = None) -> int | None:
        player = self._check_player(owner)
        card = self.catalog.card(card_id)
        if card_id not in player.hand:
            raise RuleError(f"{card.name} is not in this player's hand")
        if not self._supports(owner, card.requires):
            raise RuleError(f"{card.name} requires terrain types {card.requires}")
        if player.mana < card.cost:
            raise RuleError("Not enough mana")
        if card.kind == "terrain" and player.terrain_played:
            raise RuleError("Only one terrain may be played per turn")
        if card.kind == "creature" and len(player.creatures) >= 8:
            raise RuleError("A player may control at most eight creatures")
        needs_target = any(effect.target in {"ally", "enemy", "any"} for effect in card.effects)
        if target is not None and not needs_target:
            raise RuleError("This card does not take a selected target")
        selected = self.creature(target) if target is not None else None
        prospective = Creature(self._next_id, owner, card) if card.kind == "creature" else None
        for effect in card.effects:
            targets = self._targets(owner, effect, selected, prospective)
            if effect.action == "apply" and effect.status is not None:
                status = self.catalog.statuses[effect.status]
                if effect.target not in {"all_allies", "all_enemies"}:
                    for unit in targets:
                        if not self._supports(unit.owner, status.requires):
                            raise RuleError(f"{status.name} requires the target's terrain types {status.requires}")
        player.hand.remove(card_id)
        player.mana -= card.cost
        instance_id = None
        caster = None
        if card.kind == "terrain":
            instance_id = self._id()
            player.terrains[instance_id] = card
            player.terrain_played = True
            if len(card.terrain_types) == 1:
                player.mana += 1
        elif card.kind == "creature":
            instance_id = self._id()
            caster = Creature(instance_id, owner, card)
            player.creatures[instance_id] = caster
        elif card.kind == "enchant":
            if selected is None:
                raise RuleError("An enchant requires a selected creature")
            instance_id = self._id()
            self.enchants[instance_id] = Enchantment(instance_id, owner, card_id, selected.id)
        else:
            player.graveyard.append(card_id)
        for effect in card.effects:
            for unit in self._targets(owner, effect, selected, caster):
                self._resolve(effect, unit, instance_id if card.kind == "enchant" else None)
        self.events.append(f"Player {owner} played {card.name}")
        self._cleanup()
        return instance_id

    def _resolve(self, effect: Effect, unit: Creature, source: int | None) -> None:
        if effect.action == "apply" and effect.status is not None:
            definition = self.catalog.statuses[effect.status]
            if not self._supports(unit.owner, definition.requires):
                self.events.append(f"{unit.card.name} cannot gain {definition.name}: missing terrain")
                return
            for status_id in definition.removes:
                unit.statuses.pop(status_id, None)
            unit.statuses[definition.id] = AppliedStatus(
                definition, None if source is not None else definition.duration, source,
            )
        elif effect.action == "remove" and effect.status is not None:
            unit.statuses.pop(effect.status, None)
        elif effect.action == "damage":
            unit.damage += effect.amount
        elif effect.action == "heal":
            self._heal(unit, effect.amount)
        elif effect.action == "dispel":
            for enchant_id in [key for key, enchant in self.enchants.items() if enchant.target == unit.id]:
                self._remove_enchant(enchant_id)

    def _heal(self, unit: Creature, amount: int) -> None:
        if "no_healing" not in unit.keywords:
            unit.damage = max(0, unit.damage - amount)

    def _remove_enchant(self, enchant_id: int) -> None:
        enchant = self.enchants.pop(enchant_id)
        self.players[enchant.owner].graveyard.append(enchant.card_id)
        for player in self.players:
            for unit in player.creatures.values():
                for status_id in [key for key, status in unit.statuses.items() if status.source == enchant_id]:
                    del unit.statuses[status_id]

    def _cleanup(self) -> None:
        for player in self.players:
            for unit in list(player.creatures.values()):
                for status_id, applied in list(unit.statuses.items()):
                    if not self._supports(unit.owner, applied.definition.requires):
                        del unit.statuses[status_id]
                        self.events.append(f"{unit.card.name} lost {applied.definition.name}: missing terrain")
                if unit.health <= 0:
                    del player.creatures[unit.id]
                    player.graveyard.append(unit.card.id)
                    self.events.append(f"{unit.card.name} died")
        for enchant_id in list(self.enchants):
            if not any(
                status.source == enchant_id
                for player in self.players for unit in player.creatures.values()
                for status in unit.statuses.values()
            ):
                self._remove_enchant(enchant_id)

    def remove_terrain(self, owner: int, terrain_id: int) -> None:
        """Voluntarily sacrifice a terrain; conditional statuses are removed immediately."""
        player = self._check_player(owner)
        if terrain_id not in player.terrains:
            raise RuleError("This player does not control that terrain")
        card = player.terrains.pop(terrain_id)
        player.graveyard.append(card.id)
        self.events.append(f"Player {owner} lost {card.name}")
        self._cleanup()

    def dispel(self, owner: int, enchant_id: int) -> None:
        """The controller may voluntarily dismiss an enchant, not an opponent's."""
        self._check_player(owner)
        if enchant_id not in self.enchants or self.enchants[enchant_id].owner != owner:
            raise RuleError("This player does not control that enchant")
        self._remove_enchant(enchant_id)
        self._cleanup()

    def _can_reach(self, attacker: Creature, defender: Creature) -> bool:
        return all(
            keyword not in defender.keywords or keyword in attacker.keywords
            for keyword in ("flying", "water_breathing")
        )

    def attack(self, owner: int, attacker_id: int, defender_id: int | None = None) -> None:
        self._check_player(owner)
        attacker = self.creature(attacker_id)
        if attacker.owner != owner:
            raise RuleError("This player does not control the attacker")
        if attacker.attacked or (not attacker.ready and "haste" not in attacker.keywords):
            raise RuleError("This creature is not ready to attack")
        if "cant_attack" in attacker.keywords or attacker.attack == 0:
            raise RuleError("This creature cannot attack")
        defender = self.creature(defender_id) if defender_id is not None else None
        if defender is not None and (defender.owner == owner or not self._can_reach(attacker, defender)):
            raise RuleError("The defender is friendly or cannot be reached")
        guards = [
            unit for unit in self.players[1 - owner].creatures.values()
            if "guard" in unit.keywords and self._can_reach(attacker, unit)
        ]
        if guards and (defender is None or defender not in guards):
            raise RuleError("A reachable Guard must be attacked first")
        attacker.attacked = True
        attack_damage = attacker.attack
        if defender is None:
            self.players[1 - owner].health -= attack_damage
            if "lifesteal" in attacker.keywords:
                self.players[owner].health = min(25, self.players[owner].health + attack_damage)
        else:
            counter_damage = defender.attack
            dealt = min(attack_damage, max(0, defender.health))
            returned = min(counter_damage, max(0, attacker.health))
            defender.damage += attack_damage
            attacker.damage += counter_damage
            for unit, damage in ((attacker, dealt), (defender, returned)):
                if "lifesteal" in unit.keywords:
                    player = self.players[unit.owner]
                    player.health = min(25, player.health + damage)
        self.events.append(f"{attacker.card.name} attacked")
        self._cleanup()

    def end_turn(self, owner: int) -> None:
        player = self._check_player(owner)
        for unit in list(player.creatures.values()):
            unit.damage += sum(status.definition.damage_per_turn for status in unit.statuses.values())
        self._cleanup()
        for unit in player.creatures.values():
            self._heal(unit, sum(status.definition.healing_per_turn for status in unit.statuses.values()))
            for status_id, applied in list(unit.statuses.items()):
                if applied.remaining is not None:
                    applied.remaining -= 1
                    if applied.remaining == 0:
                        del unit.statuses[status_id]
        self._cleanup()
        self.active = 1 - owner
        self.turn += 1
        incoming = self.players[self.active]
        incoming.terrain_played = False
        incoming.mana = len(incoming.terrains)
        for unit in incoming.creatures.values():
            unit.ready = True
            unit.attacked = False
        if incoming.deck:
            incoming.hand.append(incoming.deck.pop(0))
        else:
            incoming.health -= 1
            self.events.append(f"Player {self.active} took 1 fatigue damage")
