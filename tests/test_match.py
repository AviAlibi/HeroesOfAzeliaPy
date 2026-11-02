from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from http.client import HTTPConnection
import json
import random
import threading
import unittest

from assets.asset_import_manager import load_catalog
from classes.game import RuleError
from classes.match import Action, Match, STARTERS, ai_turn, finished, legal_moves, starter_deck
from functions.server import GameServer, parse_action


class MatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_catalog()

    def test_all_starters_have_fair_supported_openings(self) -> None:
        for starter in STARTERS:
            for seed in range(12):
                with self.subTest(deck=starter.id, seed=seed):
                    cards = starter_deck(self.catalog, starter.id, random.Random(seed))
                    self.assertEqual(len(cards), 40)
                    self.assertEqual(Counter(cards), Counter(starter.cards))
                    opening = [self.catalog.card(key) for key in cards[:5]]
                    terrains = [card for card in opening if card.kind == "terrain"]
                    self.assertEqual(len(terrains), 2)
                    self.assertTrue(all(card.cost == 0 for card in terrains))
                    supported = {domain for card in terrains for domain in card.terrain_types}
                    self.assertTrue(set(starter.domains) <= supported)
                    creatures = [card for card in opening if card.kind == "creature"]
                    self.assertEqual(len(creatures), 3)
                    self.assertTrue(all(set(card.requires) <= supported for card in creatures))
                    self.assertLessEqual(min(card.cost for card in creatures), 2)
                    for card_id, count in Counter(cards).items():
                        card = self.catalog.card(card_id)
                        if card.kind != "terrain" or card.effects:
                            self.assertLessEqual(count, 3)

    def test_decks_are_shuffled_and_reproducible_with_seed(self) -> None:
        first = starter_deck(self.catalog, "wildwood", random.Random(3))
        self.assertEqual(first, starter_deck(self.catalog, "wildwood", random.Random(3)))
        self.assertNotEqual(first, starter_deck(self.catalog, "wildwood", random.Random(4)))
        with self.assertRaises(RuleError):
            starter_deck(self.catalog, "missing", random.Random())

    def test_legal_moves_never_mutate_original_state(self) -> None:
        match = Match(self.catalog)
        match.start("ai", "wildwood", "depths", seed=1)
        assert match.game is not None
        before = deepcopy(match.game)
        moves, blocked = legal_moves(match.game)
        self.assertTrue(moves)
        self.assertTrue(blocked)
        self.assertEqual(match.game.__dict__, before.__dict__)
        for action in moves:
            self.assertEqual(action.kind, "play")
            assert action.card is not None
            self.assertEqual(self.catalog.card(action.card).kind, "terrain")

    def test_state_hides_other_hand_and_both_deck_orders(self) -> None:
        match = Match(self.catalog)
        match.start("ai", "wildwood", "depths", seed=1)
        state = match.state()["match"]
        assert isinstance(state, dict)
        self.assertEqual(len(state["players"][0]["hand"]), 5)
        self.assertIsNone(state["players"][1]["hand"])
        self.assertEqual(state["players"][1]["handCount"], 5)
        self.assertNotIn("deck", state["players"][0])
        self.assertNotIn("deck", state["players"][1])

    def test_hotseat_handoff_hides_both_hands_and_blocks_moves(self) -> None:
        match = Match(self.catalog)
        match.start("hotseat", "wildwood", "skies", seed=1)
        assert match.game is not None
        state = match.state()["match"]
        assert isinstance(state, dict)
        self.assertTrue(state["handoff"])
        self.assertTrue(all(player["hand"] is None for player in state["players"]))
        self.assertEqual(state["actions"], [])
        before = deepcopy(match.game)
        with self.assertRaisesRegex(RuleError, "Pass the screen"):
            match.act(Action("end_turn"), match.revision)
        self.assertEqual(match.game.__dict__, before.__dict__)
        match.act(Action("ready"), match.revision)
        state = match.state()["match"]
        assert isinstance(state, dict)
        self.assertIsNotNone(state["players"][0]["hand"])
        self.assertIsNone(state["players"][1]["hand"])
        match.act(Action("end_turn"), match.revision)
        self.assertEqual(match.game.active, 1)
        self.assertTrue(match.handoff)
        state = match.state()["match"]
        assert isinstance(state, dict)
        self.assertTrue(all(player["hand"] is None for player in state["players"]))
        match.act(Action("ready"), match.revision)
        state = match.state()["match"]
        assert isinstance(state, dict)
        self.assertIsNone(state["players"][0]["hand"])
        self.assertEqual(len(state["players"][1]["hand"]), 6)

    def test_stale_move_and_invalid_start_preserve_match(self) -> None:
        match = Match(self.catalog)
        match.start("ai", "wildwood", "depths", seed=1)
        assert match.game is not None
        before_game = deepcopy(match.game.__dict__)
        before_state = deepcopy(match.state())
        with self.assertRaisesRegex(RuleError, "table changed"):
            match.act(Action("end_turn"), match.revision - 1)
        with self.assertRaises(RuleError):
            match.start("online", "wildwood", "depths")
        with self.assertRaises(RuleError):
            match.start("ai", "missing", "depths")
        self.assertEqual(match.game.__dict__, before_game)
        self.assertEqual(match.state(), before_state)

    def test_end_turn_runs_ai_and_returns_control(self) -> None:
        match = Match(self.catalog)
        match.start("ai", "wildwood", "skies", seed=2)
        assert match.game is not None
        match.act(Action("end_turn"), match.revision)
        self.assertEqual(match.game.active, 0)
        self.assertEqual(match.game.turn, 3)
        self.assertEqual(len(match.game.players[1].terrains), 1)
        state = match.state()["match"]
        assert isinstance(state, dict)
        self.assertIsNone(state["players"][1]["hand"])

    def test_animation_frames_show_each_ai_action_without_hidden_hand(self) -> None:
        match = Match(self.catalog)
        match.start("ai", "wildwood", "skies", seed=2)
        frames = match.act(Action("end_turn"), match.revision)
        self.assertGreaterEqual(len(frames), 3)
        self.assertEqual(frames[0]["action"], {"kind": "end_turn"})
        self.assertEqual(frames[0]["owner"], 0)
        self.assertEqual(frames[-1]["action"], {"kind": "end_turn"})
        self.assertEqual(frames[-1]["owner"], 1)
        terrain_counts = []
        for frame in frames:
            snapshot = frame["match"]
            assert isinstance(snapshot, dict)
            self.assertIsNone(snapshot["players"][1]["hand"])
            self.assertNotIn("deck", snapshot["players"][1])
            if snapshot["active"] == 1:
                self.assertEqual(snapshot["actions"], [])
                self.assertEqual(snapshot["blocked"], {})
            terrain_counts.append(len(snapshot["players"][1]["terrains"]))
        self.assertEqual(terrain_counts[0], 0)
        self.assertEqual(terrain_counts[-1], 1)
        self.assertEqual(frames[-1]["match"], match.state()["match"])
        self.assertNotIn("frames", match.state())

    def test_player_play_has_exactly_one_animation_frame(self) -> None:
        match = Match(self.catalog)
        match.start("ai", "wildwood", "skies", seed=2)
        assert match.game is not None
        card_id = next(key for key in match.game.players[0].hand if self.catalog.card(key).kind == "terrain")
        frames = match.act(Action("play", card=card_id), match.revision)
        self.assertEqual(len(frames), 1)
        card = frames[0]["card"]
        assert isinstance(card, dict)
        self.assertEqual(card["id"], card_id)
        self.assertEqual(frames[0]["owner"], 0)
        snapshot = frames[0]["match"]
        assert isinstance(snapshot, dict)
        self.assertEqual(len(snapshot["players"][0]["terrains"]), 1)

    def test_hotseat_end_turn_never_replays_next_players_hand(self) -> None:
        match = Match(self.catalog)
        match.start("hotseat", "wildwood", "skies", seed=2)
        self.assertEqual(match.act(Action("ready"), match.revision), [])
        self.assertEqual(match.act(Action("end_turn"), match.revision), [])
        self.assertTrue(match.handoff)
        snapshot = match.state()["match"]
        assert isinstance(snapshot, dict)
        self.assertTrue(all(player["hand"] is None for player in snapshot["players"]))

    def test_ai_full_matches_finish_for_every_starter(self) -> None:
        for index, starter in enumerate(STARTERS):
            with self.subTest(starter=starter.id):
                match = Match(self.catalog)
                match.start("hotseat", starter.id, STARTERS[(index + 1) % len(STARTERS)].id, seed=index)
                assert match.game is not None
                for _ in range(170):
                    if finished(match.game):
                        break
                    ai_turn(match.game)
                    for player in match.game.players:
                        self.assertGreaterEqual(player.mana, 0)
                        self.assertLessEqual(len(player.creatures), 8)
                        for unit in player.creatures.values():
                            self.assertGreater(unit.health, 0)
                self.assertTrue(finished(match.game))
                self.assertIsNotNone(match.game.winner)

    def test_ai_takes_lethal_attack_and_does_not_peek_at_hidden_cards(self) -> None:
        match = Match(self.catalog)
        match.start("ai", "wildwood", "skies", seed=1)
        game = match.game
        assert game is not None
        game.players[0].hand = ["forest", "briar_cub"]
        game.play(0, "forest")
        unit_id = game.play(0, "briar_cub")
        assert unit_id is not None
        game.creature(unit_id).ready = True
        game.players[1].health = 1
        game.players[1].hand = ["unknown_hidden_card"]
        game.players[1].deck = ["unknown_hidden_card"]
        ai_turn(game)
        self.assertEqual(game.winner, 0)

    def test_no_moves_or_handoff_after_victory_and_restart_clears_state(self) -> None:
        match = Match(self.catalog)
        match.start("hotseat", "wildwood", "depths", seed=2)
        assert match.game is not None
        match.game.players[0].health = 0
        state = match.state()["match"]
        assert isinstance(state, dict)
        self.assertTrue(state["over"])
        self.assertEqual(state["actions"], [])
        with self.assertRaisesRegex(RuleError, "ended"):
            match.act(Action("ready"), match.revision)
        revision = match.revision
        match.start("ai", "skies", "wildwood", seed=3)
        assert match.game is not None
        self.assertFalse(match.handoff)
        self.assertGreater(match.revision, revision)
        self.assertEqual(match.game.players[0].health, 25)
        self.assertFalse(match.game.enchants)

    def test_action_parser_rejects_bad_shapes(self) -> None:
        invalid: tuple[dict[str, object], ...] = (
            {}, {"kind": "invent"}, {"kind": "play"}, {"kind": "play", "card": 3},
            {"kind": "attack", "source": True}, {"kind": "attack", "source": -1},
            {"kind": "attack", "source": 1, "target": "2"},
            {"kind": "end_turn", "owner": 1},
            {"kind": "sacrifice"}, {"kind": "ready", "source": 1},
        )
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(RuleError):
                parse_action(raw)
        self.assertEqual(parse_action({"kind": "attack", "source": 2}), Action("attack", source=2))


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = GameServer(load_catalog(), port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.thread.join(timeout=5)
        cls.server.server_close()

    def setUp(self) -> None:
        with self.server.lock:
            self.server.match = Match(self.server.match.catalog)

    def request(self, method: str, path: str, data: object = None, headers: dict[str, str] | None = None) -> tuple[int, bytes]:
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=30)
        body = json.dumps(data).encode() if data is not None else None
        combined = {"Content-Type": "application/json", "X-Azelia-Token": self.server.token}
        combined.update(headers or {})
        try:
            connection.request(method, path, body=body, headers=combined)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def start(self, mode: str = "ai") -> dict:
        status, body = self.request("POST", "/api/start", {
            "mode": mode, "first": "wildwood", "second": "skies", "revision": 0,
        })
        self.assertEqual(status, 200, body)
        return json.loads(body)

    def test_static_files_and_api_bootstrap_are_served(self) -> None:
        for path, snippet in (("/", b"Heroes of Azelia"), ("/app.js", b"refresh()"), ("/style.css", b":root")):
            with self.subTest(path=path):
                status, body = self.request("GET", path)
                self.assertEqual(status, 200)
                self.assertIn(snippet, body)
        status, body = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["token"], self.server.token)
        self.assertIsNone(data["match"])
        self.assertEqual(len(data["starters"]), 6)

    def test_real_api_play_attack_and_ai_turn(self) -> None:
        state = self.start()
        move = state["match"]["actions"][0]
        status, body = self.request("POST", "/api/action", {"action": move, "revision": state["revision"]})
        self.assertEqual(status, 200, body)
        state = json.loads(body)
        self.assertEqual(len(state["match"]["players"][0]["terrains"]), 1)
        status, body = self.request("POST", "/api/action", {
            "action": {"kind": "end_turn"}, "revision": state["revision"],
        })
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["match"]["active"], 0)
        self.assertEqual(json.loads(body)["match"]["turn"], 3)

    def test_duplicate_requests_apply_exactly_once(self) -> None:
        state = self.start()
        payload = {"action": state["match"]["actions"][0], "revision": state["revision"]}
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: self.request("POST", "/api/action", payload), range(2)))
        self.assertEqual(sorted(status for status, _ in responses), [200, 409])
        assert self.server.match.game is not None
        self.assertEqual(len(self.server.match.game.players[0].terrains), 1)

    def test_invalid_moves_are_json_errors_without_mutation(self) -> None:
        state = self.start()
        assert self.server.match.game is not None
        before = deepcopy(self.server.match.game)
        for action in ({"kind": "play", "card": "missing"}, {"kind": "attack", "source": 9999}):
            with self.subTest(action=action):
                status, body = self.request("POST", "/api/action", {"action": action, "revision": state["revision"]})
                self.assertEqual(status, 409)
                self.assertIn("error", json.loads(body))
                self.assertEqual(self.server.match.game.__dict__, before.__dict__)

    def test_hotseat_api_never_reveals_hands_before_ready(self) -> None:
        state = self.start("hotseat")
        self.assertTrue(all(player["hand"] is None for player in state["match"]["players"]))
        status, body = self.request("POST", "/api/action", {
            "action": {"kind": "ready"}, "revision": state["revision"],
        })
        self.assertEqual(status, 200)
        state = json.loads(body)
        self.assertIsNotNone(state["match"]["players"][0]["hand"])
        self.assertIsNone(state["match"]["players"][1]["hand"])

    def test_api_rejects_foreign_origins_missing_tokens_and_bad_hosts(self) -> None:
        for headers in (
            {"Origin": "https://example.invalid"},
            {"X-Azelia-Token": ""},
            {"Host": "example.invalid"},
        ):
            with self.subTest(headers=headers):
                status, body = self.request("POST", "/api/start", {}, headers)
                self.assertEqual(status, 403)
                self.assertIn("error", json.loads(body))
        self.assertEqual(self.request("GET", "/api/state", headers={"Host": "example.invalid"})[0], 403)

    def test_api_rejects_invalid_payloads_and_unknown_paths(self) -> None:
        for data in ([], None, {"revision": True}, {"revision": 0, "action": "attack"}):
            with self.subTest(data=data):
                status, body = self.request("POST", "/api/action", data)
                self.assertIn(status, (400, 409, 413))
                self.assertIn("error", json.loads(body))
        self.assertEqual(self.request("GET", "/../main.py")[0], 404)
        self.assertEqual(self.request("POST", "/api/missing", {})[0], 404)
        self.assertEqual(self.request("POST", "/api/action", {}, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("POST", "/api/action", "a" * 9000)[0], 413)


if __name__ == "__main__":
    unittest.main()
