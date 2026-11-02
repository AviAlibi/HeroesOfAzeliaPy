import contextlib
from copy import deepcopy
from dataclasses import replace
import io
import json
from pathlib import Path
import random
import tempfile
import unittest

from assets.asset_import_manager import AssetError, Catalog, Effect, load_catalog
from assets.buffs.buffs_import_manager import load_buffs
from assets.cards.cards_import_manager import load_cards
from assets.debuffs.debuffs_import_manager import load_debuffs
from assets.enchants.enchants_import_manager import load_enchants
from classes.game import AppliedStatus, Creature, Game, RuleError
from main import demo


class GameTests(unittest.TestCase):
    catalog: Catalog

    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_catalog()

    def setUp(self) -> None:
        self.game = Game(self.catalog, (["forest"] * 30, ["forest"] * 30))
        for owner in (0, 1):
            player = self.game.players[owner]
            player.mana = 30
            for index, terrain in enumerate(("forest", "beach", "floating_island")):
                player.terrains[100 + owner * 10 + index] = self.catalog.terrains[terrain]

    def summon(self, card_id: str = "granite_warden", owner: int = 0) -> Creature:
        previous = self.game.active
        self.game.active = owner
        player = self.game.players[owner]
        player.hand.append(card_id)
        creature_id = self.game.play(owner, card_id)
        self.game.active = previous
        assert creature_id is not None
        return self.game.creature(creature_id)

    def cast(self, card_id: str, target: int | None = None, owner: int = 0) -> int | None:
        self.game.players[owner].hand.append(card_id)
        return self.game.play(owner, card_id, target)

    def status(self, unit: Creature, status_id: str, remaining: int | None = None) -> None:
        definition = self.catalog.statuses[status_id]
        unit.statuses[status_id] = AppliedStatus(
            definition, definition.duration if remaining is None else remaining,
        )

    def round(self) -> None:
        self.game.end_turn(0)
        self.game.end_turn(1)

    def test_collection_counts_and_category_importers(self) -> None:
        self.assertEqual(len(self.catalog.terrains), 50)
        self.assertEqual(sum(card.kind == "creature" for card in self.catalog.cards.values()), 80)
        self.assertEqual(sum(card.kind == "spell" for card in self.catalog.cards.values()), 14)
        self.assertEqual(len(load_buffs()), 12)
        self.assertEqual(len(load_debuffs()), 12)
        self.assertEqual(len(load_cards()), 94)
        self.assertEqual(len(load_enchants()), 20)
        self.assertNotIn("_template", self.catalog.terrains)

    def test_creature_stat_budget_and_cost_curve(self) -> None:
        counts: dict[int, int] = {}
        for card in self.catalog.cards.values():
            if card.kind != "creature":
                continue
            with self.subTest(card=card.id):
                self.assertLessEqual(card.attack * 2 + card.health + len(card.keywords) * 2, card.cost * 3 + 3)
                self.assertTrue(card.requires)
            counts[card.cost] = counts.get(card.cost, 0) + 1
        self.assertEqual(set(counts), set(range(1, 8)))

    def test_every_status_has_a_playable_source(self) -> None:
        used = {
            effect.status
            for group in (self.catalog.cards, self.catalog.enchants, self.catalog.terrains)
            for card in group.values() for effect in card.effects if effect.action == "apply"
        }
        self.assertEqual(used, set(self.catalog.statuses))

    def test_all_creatures_can_be_played(self) -> None:
        for card in self.catalog.cards.values():
            if card.kind != "creature":
                continue
            with self.subTest(card=card.id):
                self.setUp()
                unit = self.summon(card.id)
                self.assertGreater(unit.health, 0)
                self.assertEqual(unit.card.id, card.id)
                self.assertEqual(self.game.players[0].mana, 30 - card.cost)

    def test_all_spells_and_enchants_resolve(self) -> None:
        for card in (*self.catalog.cards.values(), *self.catalog.enchants.values()):
            if card.kind not in {"spell", "enchant"}:
                continue
            with self.subTest(card=card.id):
                self.setUp()
                ally = self.summon()
                enemy = self.summon(owner=1)
                target = None
                if any(effect.target in {"ally", "any"} for effect in card.effects):
                    target = ally.id
                elif any(effect.target == "enemy" for effect in card.effects):
                    target = enemy.id
                self.cast(card.id, target)
                self.assertNotIn(card.id, self.game.players[0].hand)

    def test_all_terrains_resolve_and_pay_development_cost(self) -> None:
        for card in self.catalog.terrains.values():
            with self.subTest(card=card.id):
                self.setUp()
                ally = self.summon()
                self.summon(owner=1)
                before = self.game.players[0].mana
                terrain_id = self.cast(card.id)
                self.assertIn(terrain_id, self.game.players[0].terrains)
                self.assertEqual(
                    self.game.players[0].mana,
                    before - (card.value - 1) + (1 if len(card.terrain_types) == 1 else 0),
                )
                self.assertGreater(ally.health, 0)

    def test_water_breathing_removed_only_when_last_sea_is_lost(self) -> None:
        unit = self.summon("reef_dartfish")
        self.game.players[0].terrains[200] = self.catalog.terrains["coral_reef"]
        self.game.remove_terrain(0, 101)
        self.assertIn("water_breathing", unit.statuses)
        self.game.remove_terrain(0, 200)
        self.assertNotIn("water_breathing", unit.statuses)
        self.cast("beach")
        self.assertNotIn("water_breathing", unit.statuses)
        self.cast("tidal_gift", unit.id)
        self.assertIn("water_breathing", unit.statuses)

    def test_opponents_sea_does_not_support_my_buff(self) -> None:
        unit = self.summon("reef_dartfish")
        self.game.remove_terrain(0, 101)
        self.assertIn("sea", self.game.terrain_types(1))
        self.assertNotIn("water_breathing", unit.statuses)
        self.game.players[0].hand.append("tidal_gift")
        before = deepcopy(self.game.__dict__)
        with self.assertRaisesRegex(RuleError, "requires terrain"):
            self.game.play(0, "tidal_gift", unit.id)
        self.assertEqual(self.game.__dict__, before)

    def test_air_and_land_requirements_also_remove_buffs(self) -> None:
        unit = self.summon()
        self.cast("windcall", unit.id)
        self.game.remove_terrain(0, 102)
        self.assertNotIn("soaring", unit.statuses)
        self.status(unit, "woodland_vigor")
        self.game.remove_terrain(0, 100)
        self.assertIn("woodland_vigor", unit.statuses)
        self.game.remove_terrain(0, 101)
        self.assertNotIn("woodland_vigor", unit.statuses)

    def test_terrain_grants_and_cleanses_without_becoming_an_aura(self) -> None:
        unit = self.summon()
        self.status(unit, "burning")
        self.cast("coral_reef")
        self.assertNotIn("burning", unit.statuses)
        self.assertIn("water_breathing", unit.statuses)
        later = self.summon("briar_cub")
        self.assertNotIn("water_breathing", later.statuses)
        self.status(unit, "burning")
        self.game.players[0].terrain_played = False
        self.cast("whispering_marsh")
        self.assertNotIn("burning", unit.statuses)

    def test_terrain_can_debuff_enemies_and_bypass_ward(self) -> None:
        enemy = self.summon("mistveil_owl", owner=1)
        self.cast("ashen_caldera")
        self.assertIn("burning", enemy.statuses)

    def test_bad_targets_do_not_spend_mana_or_consume_cards(self) -> None:
        ally = self.summon()
        self.game.players[0].hand.append("venom_dart")
        before = deepcopy(self.game.__dict__)
        for target in (None, ally.id, 9999):
            with self.subTest(target=target), self.assertRaises(RuleError):
                self.game.play(0, "venom_dart", target)
            self.assertEqual(self.game.__dict__, before)

    def test_missing_hand_mana_and_mixed_requirements_preserve_state(self) -> None:
        for failure in ("hand", "mana", "terrain"):
            with self.subTest(failure=failure):
                self.setUp()
                if failure != "hand":
                    self.game.players[0].hand.append("confluence_avatar")
                if failure == "mana":
                    self.game.players[0].mana = 0
                if failure == "terrain":
                    del self.game.players[0].terrains[102]
                before = deepcopy(self.game.__dict__)
                with self.assertRaises(RuleError):
                    self.game.play(0, "confluence_avatar")
                self.assertEqual(self.game.__dict__, before)

    def test_enemy_enchants_cannot_be_cast_on_allies(self) -> None:
        ally = self.summon()
        with self.assertRaisesRegex(RuleError, "enemy creature"):
            self.cast("brand_of_cinders", ally.id)

    def test_ward_prevents_selected_enemy_effects_not_area_effects(self) -> None:
        enemy = self.summon("mistveil_owl", owner=1)
        for card in ("venom_dart", "lead_feathers", "unravel"):
            self.game.players[0].hand.append(card)
            before = deepcopy(self.game.__dict__)
            with self.subTest(card=card), self.assertRaisesRegex(RuleError, "Ward"):
                self.game.play(0, card, enemy.id)
            self.assertEqual(self.game.__dict__, before)
        self.cast("ashen_caldera")
        self.assertIn("burning", enemy.statuses)

    def test_enchant_is_persistent_and_dispel_reverts_its_stats(self) -> None:
        unit = self.summon()
        base = unit.max_health
        enchant_id = self.cast("granite_oath", unit.id)
        self.assertEqual(unit.max_health, base + 2)
        for _ in range(4):
            self.round()
        self.assertEqual(unit.max_health, base + 2)
        self.assertIn(enchant_id, self.game.enchants)
        self.cast("unravel", unit.id)
        self.assertEqual(unit.max_health, base)
        self.assertNotIn(enchant_id, self.game.enchants)
        self.assertIn("granite_oath", self.game.players[0].graveyard)

    def test_conditional_enchant_is_not_restored_after_terrain_returns(self) -> None:
        unit = self.summon()
        enchant_id = self.cast("gills_of_the_deep", unit.id)
        self.game.remove_terrain(0, 101)
        self.assertNotIn("water_breathing", unit.statuses)
        self.assertNotIn("tidal_vigor", unit.statuses)
        self.assertNotIn(enchant_id, self.game.enchants)
        self.cast("beach")
        self.assertNotIn("water_breathing", unit.statuses)

    def test_partial_enchant_keeps_its_supported_effect(self) -> None:
        unit = self.summon()
        enchant_id = self.cast("stormrunners_pact", unit.id)
        self.game.remove_terrain(0, 102)
        self.assertNotIn("windborne_focus", unit.statuses)
        self.assertIn("swiftness", unit.statuses)
        self.assertIn(enchant_id, self.game.enchants)

    def test_reapplying_same_status_does_not_stack_and_retires_old_source(self) -> None:
        unit = self.summon()
        first = self.cast("banner_of_embers", unit.id)
        second = self.cast("banner_of_embers", unit.id)
        self.assertEqual(unit.attack, unit.card.attack + 2)
        self.assertNotIn(first, self.game.enchants)
        self.assertIn(second, self.game.enchants)
        self.cast("rally_the_wilds")
        self.assertNotIn(second, self.game.enchants)
        self.assertEqual(unit.attack, unit.card.attack + 2)
        self.assertEqual(unit.statuses["battle_fervor"].remaining, 2)

    def test_counter_effects_remove_enchants_and_restore_flying(self) -> None:
        unit = self.summon("spire_griffin")
        self.game.active = 1
        self.game.players[1].hand.append("lead_feathers")
        enchant_id = self.game.play(1, "lead_feathers", unit.id)
        self.assertNotIn("flying", unit.keywords)
        self.game.active = 0
        self.cast("windcall", unit.id)
        self.assertIn("flying", unit.keywords)
        self.assertNotIn(enchant_id, self.game.enchants)

    def test_dot_ticks_only_at_affected_owners_turn_end_and_expires(self) -> None:
        unit = self.summon(owner=1)
        self.cast("venom_dart", unit.id)
        self.game.end_turn(0)
        self.assertEqual(unit.damage, 0)
        self.game.end_turn(1)
        self.assertEqual(unit.damage, 1)
        self.round()
        self.round()
        self.assertEqual(unit.damage, 3)
        self.assertNotIn("poisoned", unit.statuses)

    def test_dot_death_cannot_be_prevented_by_debuff_expiration(self) -> None:
        unit = self.summon("frosthorn_elk")
        self.status(unit, "exposed", remaining=1)
        self.status(unit, "poisoned", remaining=1)
        unit.damage = 1
        self.game.end_turn(0)
        self.assertNotIn(unit.id, self.game.players[0].creatures)

    def test_curse_prevents_healing_and_cleansing_restores_it(self) -> None:
        unit = self.summon()
        unit.damage = 3
        self.status(unit, "cursed")
        self.cast("healing_rain", unit.id)
        self.assertEqual(unit.damage, 3)
        self.cast("purifying_light", unit.id)
        self.cast("healing_rain", unit.id)
        self.assertEqual(unit.damage, 0)

    def test_maximum_health_loss_can_kill_and_clears_attachments(self) -> None:
        unit = self.summon("briar_cub")
        self.cast("granite_oath", unit.id)
        unit.damage = 2
        self.cast("unravel", unit.id)
        self.assertNotIn(unit.id, self.game.players[0].creatures)
        self.assertFalse(self.game.enchants)

    def test_expired_bonus_health_can_kill(self) -> None:
        unit = self.summon("briar_cub")
        self.status(unit, "stone_skin", remaining=1)
        unit.damage = 2
        self.game.end_turn(0)
        self.assertNotIn(unit.id, self.game.players[0].creatures)

    def test_summoning_sickness_haste_and_one_attack_per_turn(self) -> None:
        unit = self.summon("briar_cub")
        with self.assertRaisesRegex(RuleError, "not ready"):
            self.game.attack(0, unit.id)
        self.status(unit, "swiftness")
        self.game.attack(0, unit.id)
        self.assertEqual(self.game.players[1].health, 24)
        with self.assertRaises(RuleError):
            self.game.attack(0, unit.id)
        self.round()
        self.game.attack(0, unit.id)
        self.assertEqual(self.game.players[1].health, 23)

    def test_guard_and_simultaneous_retaliation(self) -> None:
        attacker = self.summon("thunderhoof_charger")
        defender = self.summon("mossback_turtle", owner=1)
        with self.assertRaisesRegex(RuleError, "Guard"):
            self.game.attack(0, attacker.id)
        self.game.attack(0, attacker.id, defender.id)
        self.assertEqual(attacker.damage, 1)
        self.assertNotIn(defender.id, self.game.players[1].creatures)

    def test_flying_water_breathing_and_grounding_affect_reach(self) -> None:
        attacker = self.summon("thunderhoof_charger")
        flyer = self.summon("spire_griffin", owner=1)
        swimmer = self.summon("leviathan_hatchling", owner=1)
        for defender in (flyer, swimmer):
            with self.assertRaisesRegex(RuleError, "cannot be reached"):
                self.game.attack(0, attacker.id, defender.id)
        self.cast("grounding_bolt", flyer.id)
        self.game.attack(0, attacker.id, flyer.id)
        self.assertNotIn(flyer.id, self.game.players[1].creatures)

    def test_lifesteal_caps_healing_and_does_not_count_overkill(self) -> None:
        attacker = self.summon("sunwing_lion")
        defender = self.summon("briar_cub", owner=1)
        attacker.ready = True
        self.game.players[0].health = 20
        self.game.attack(0, attacker.id, defender.id)
        self.assertEqual(self.game.players[0].health, 22)
        attacker.attacked = False
        self.game.attack(0, attacker.id)
        self.assertEqual(self.game.players[0].health, 25)

    def test_stunned_and_disarmed_prevent_attack(self) -> None:
        for status_id in ("stunned", "rooted", "disarmed"):
            with self.subTest(status=status_id):
                self.setUp()
                unit = self.summon("thunderhoof_charger")
                self.status(unit, status_id)
                with self.assertRaisesRegex(RuleError, "cannot attack"):
                    self.game.attack(0, unit.id)

    def test_terrain_limit_development_cost_and_exhaustion(self) -> None:
        game = Game(self.catalog, (
            ["forest", "beach", "coral_reef", "mountain", "forest"] + ["forest"] * 10,
            ["forest"] * 15,
        ))
        with self.assertRaisesRegex(RuleError, "mana"):
            game.play(0, "coral_reef")
        game.play(0, "forest")
        self.assertEqual(game.players[0].mana, 1)
        with self.assertRaisesRegex(RuleError, "one terrain"):
            game.play(0, "mountain")
        game.end_turn(0)
        game.end_turn(1)
        game.play(0, "beach")
        self.assertEqual(game.players[0].mana, 1)
        game.end_turn(0)
        game.end_turn(1)
        self.assertEqual(game.players[0].mana, 2)

    def test_board_limit_and_ownership(self) -> None:
        units = [self.summon("briar_cub") for _ in range(8)]
        with self.assertRaisesRegex(RuleError, "eight"):
            self.summon("briar_cub")
        with self.assertRaisesRegex(RuleError, "not this player's turn"):
            self.game.attack(1, units[0].id)
        with self.assertRaisesRegex(RuleError, "does not control"):
            self.game.remove_terrain(0, 110)
        self.game.active = 1
        with self.assertRaisesRegex(RuleError, "does not control"):
            self.game.attack(1, units[0].id)

    def test_dismissal_cannot_remove_an_opponents_enchant(self) -> None:
        unit = self.summon()
        self.game.active = 1
        enchant_id = self.cast("brand_of_cinders", unit.id, owner=1)
        assert enchant_id is not None
        self.game.active = 0
        with self.assertRaisesRegex(RuleError, "does not control"):
            self.game.dispel(0, enchant_id)
        self.assertIn(enchant_id, self.game.enchants)
        self.game.active = 1
        self.game.dispel(1, enchant_id)
        self.assertNotIn("burning", unit.statuses)

    def test_fatigue_and_victory_prevent_later_actions(self) -> None:
        game = Game(self.catalog, ([], []))
        game.players[1].health = 1
        game.end_turn(0)
        self.assertEqual(game.winner, 0)
        with self.assertRaisesRegex(RuleError, "ended"):
            game.end_turn(1)

    def test_full_demo_uses_legal_turns_and_explicit_reapplication(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            game = demo(self.catalog)
        self.assertEqual(
            [line.rsplit(" = ", 1)[-1] for line in output.getvalue().splitlines()],
            ["True", "False", "False", "True"],
        )
        self.assertIn("coral_reef", game.players[0].graveyard)
        self.assertIn("tidal_gift", game.players[0].graveyard)

    def test_seeded_full_matches_finish_without_rules_corruption(self) -> None:
        decks = [
            (
                ["forest"] * 10 + ["lanternwood"] * 3 + ["emberfall_ridge"] * 3
                + ["briar_cub", "cinder_beetle", "mossback_turtle", "thornstalker",
                   "briarheart_dryad", "ember_salamander"] * 3
                + ["venom_dart", "stone_shelter", "banner_of_embers"] * 2
            ),
            (
                ["beach"] * 8 + ["duskmire"] * 4 + ["coral_reef"] * 4
                + ["reef_dartfish", "shellback_crab", "kelp_grazer", "tidal_serpent",
                   "current_dancer", "pearl_healer"] * 3
                + ["quenching_tide", "healing_rain", "undertow_shroud"] * 2
            ),
            (
                ["floating_island"] * 10 + ["farwatch_summit"] * 3 + ["skyglass_spire"] * 3
                + ["cloudwing_sparrow", "gustling", "zephyr_courier", "stormcrest_hawk",
                   "spire_griffin", "windweaver"] * 3
                + ["grounding_bolt", "windcall", "lead_feathers"] * 2
            ),
        ]
        for seed in range(3):
            with self.subTest(seed=seed):
                shuffled = [list(decks[seed]), list(decks[(seed + 1) % 3])]
                rng = random.Random(seed)
                for deck in shuffled:
                    self.assertEqual(len(deck), 40)
                    rng.shuffle(deck)
                game = Game(self.catalog, (shuffled[0], shuffled[1]))
                for _ in range(150):
                    if any(player.health <= 0 for player in game.players):
                        break
                    owner = game.active
                    player = game.players[owner]
                    candidates = sorted(player.hand, key=lambda key: game.catalog.card(key).kind != "terrain")
                    for card_id in candidates:
                        card = game.catalog.card(card_id)
                        selected_targets: list[int | None] = [None]
                        if any(effect.target in {"ally", "enemy", "any"} for effect in card.effects):
                            selected_targets = [
                                unit.id for board in game.players for unit in board.creatures.values()
                            ]
                        for target in selected_targets:
                            try:
                                game.play(owner, card_id, target)
                            except RuleError:
                                continue
                            break
                    for unit in list(player.creatures.values()):
                        targets = [
                            enemy.id for enemy in game.players[1 - owner].creatures.values()
                        ]
                        for defender_id in [None, *targets]:
                            try:
                                game.attack(owner, unit.id, defender_id)
                            except RuleError:
                                continue
                            break
                    for board in game.players:
                        self.assertGreaterEqual(board.mana, 0)
                        self.assertLessEqual(len(board.creatures), 8)
                        for unit in board.creatures.values():
                            self.assertGreater(unit.health, 0)
                            for status in unit.statuses.values():
                                self.assertTrue(
                                    set(status.definition.requires) <= game.terrain_types(unit.owner),
                                )
                    if any(board.health <= 0 for board in game.players):
                        break
                    game.end_turn(owner)
                self.assertIsNotNone(game.winner)

    def test_unknown_cards_and_invalid_player_are_explicit_errors(self) -> None:
        with self.assertRaises(AssetError):
            self.catalog.card("missing")
        with self.assertRaises(AssetError):
            Game(self.catalog, (["missing"], []))
        with self.assertRaises(RuleError):
            self.game.end_turn(2)
        with self.assertRaises(RuleError):
            self.game.terrain_types(-1)

    def test_target_terrain_requirement_is_checked_before_spending(self) -> None:
        unit = self.summon(owner=1)
        self.game.players[1].terrains.clear()
        original = self.catalog.enchants["gills_of_the_deep"]
        enemy_version = replace(
            original, effects=(Effect("apply", "enemy", "water_breathing"),),
        )
        catalog = replace(self.catalog, enchants={"gills_of_the_deep": enemy_version})
        self.game.catalog = catalog
        self.game.players[0].hand.append("gills_of_the_deep")
        before = deepcopy(self.game.__dict__)
        with self.assertRaisesRegex(RuleError, "target's terrain"):
            self.game.play(0, "gills_of_the_deep", unit.id)
        self.assertEqual(self.game.__dict__, before)


class CatalogValidationTests(unittest.TestCase):
    def invalid_asset(self, group: str, data: object) -> str:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for folder in ("buffs", "debuffs", "terrains", "cards", "enchants"):
                (root / folder).mkdir()
            (root / group / "invalid.json").write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(AssetError) as caught:
                load_catalog(root)
            return str(caught.exception)

    def test_broken_status_reference_is_rejected(self) -> None:
        message = self.invalid_asset("enchants", {
            "name": "Bad Enchant", "description": "Broken reference", "rarity": "common",
            "cost": 1, "onPlay": [{"action": "apply", "target": "ally", "status": "missing"}],
        })
        self.assertIn("unknown status missing", message)

    def test_unsupported_effects_and_targets_are_rejected(self) -> None:
        for action, target in (("invented", "ally"), ("damage", "invented")):
            with self.subTest(action=action, target=target):
                message = self.invalid_asset("cards", {
                    "name": "Bad Spell", "description": "Invalid action", "type": "spell",
                    "rarity": "common", "cost": 1,
                    "onPlay": [{"action": action, "target": target, "amount": 1}],
                })
                self.assertIn("unsupported effect", message)

    def test_invalid_terrain_types_durations_and_boolean_costs_are_rejected(self) -> None:
        self.assertIn("unsupported entries", self.invalid_asset("buffs", {
            "name": "Bad Buff", "description": "Invalid terrain", "requiresTerrain": ["water"],
        }))
        self.assertIn("integer >= 1", self.invalid_asset("debuffs", {
            "name": "Bad Debuff", "description": "Invalid duration", "duration": 0,
        }))
        self.assertIn("integer >= 1", self.invalid_asset("cards", {
            "name": "Bad Creature", "description": "Boolean cost", "rarity": "common",
            "type": "creature", "cost": True, "health": 1,
        }))

    def test_unknown_fields_are_rejected_instead_of_silently_ignored(self) -> None:
        self.assertIn("unknown status fields", self.invalid_asset("buffs", {
            "name": "Typo", "description": "Misspelled modifier", "healt": 2,
        }))

    def test_missing_directories_and_malformed_json_are_explicit_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(AssetError, "Missing asset directory"):
                load_catalog(root)
            for folder in ("buffs", "debuffs", "terrains", "cards", "enchants"):
                (root / folder).mkdir()
            (root / "buffs" / "broken.json").write_text("{", encoding="utf-8")
            with self.assertRaisesRegex(AssetError, "broken.json"):
                load_catalog(root)


if __name__ == "__main__":
    unittest.main()
