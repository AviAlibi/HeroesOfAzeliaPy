# Heroes of Azelia

An original fantasy card game inspired by resource development, creature combat,
and interacting enchantments. Play against the AI or share a screen in two-player
hotseat. Python 3.10+ and a modern browser; no external dependencies, accounts,
or internet connection are required.

## Run

```sh
python3 main.py
```

This opens the game at **http://127.0.0.1:8765**. Choose a mode and two starter
decks, then begin your duel. Press Ctrl+C in the terminal to stop the server.

```sh
python3 main.py --no-browser --port 8766
python3 main.py --check
python3 main.py --demo
python3 -m unittest discover -s tests -v
```

Use another `--port` if the default is occupied. `--check` validates the catalog
without opening a server. Launching from outside this directory also works when
you give the absolute path to `main.py`.

The demo plays legal turns to show Water Breathing being granted, removed on
losing the final sea terrain, remaining absent when sea returns, and being
explicitly reapplied.

## Playing in the browser

- Select a hand card to inspect its abilities. Glowing cards have a legal play.
  Click **Play** in the inspector, or choose a highlighted creature if the card
  needs a target. Narrow screens also have a quick-action button above the hand.
- Select your creature to highlight legal attack targets. Click an enemy
  creature or the opponent's portrait to attack. The small **i** button inspects
  a creature without accidentally targeting it.
- The inspector shows current stats, keywords, timed statuses, and enchants.
  It also lets you dismiss your own enchants or sacrifice your own terrains,
  with confirmation. Graveyard counts open a card list.
- End your turn to resolve statuses and let the AI act. In hotseat, pass the
  screen before revealing the next hand. The server withholds both hands until
  that player presses **I'm ready**.
- Played cards visibly travel from your hand (or the opponent's side), pause
  face-up so you can read them, then land on their creature, terrain, or spell
  target. AI moves replay one at a time with an action banner; attacks lunge
  toward their target and changed stats/statuses flash. Controls stay locked
  until the sequence finishes, and the victory screen appears afterward.
  Reduced-motion preferences replace movement with a brief face-up reveal.
  Refresh reconnects to the final authoritative board without replaying old moves.
- **How to play** opens the rules. Escape cancels a card selection. **New duel**
  and the victory screen let you restart with different decks.
- Reloading the page reconnects to the current match. **Refresh table** recovers
  from a stale browser tab or interrupted connection without replaying a move.

### Six starter decks

| Starter | Domains | Approach |
|---|---|---|
| Wildwood Covenant | Land | Guardians, regeneration, armor, and a board-wide rally |
| Court of the Deep | Sea | Water Breathing, healing, and late-game leviathans |
| Skybound Accord | Air | Flying attackers, haste, wards, and grounding magic |
| Marshlight Circle | Land + Sea | Coastal creatures, poison, and cleansing |
| Thunderpeak Legion | Land + Air | Chargers, large guards, and flying lifesteal |
| Tempest Voyage | Sea + Air | Evasion, soaking curses, and storm effects |

Each starter has 40 cards and 16 terrains. Both players get a shared assisted
opening rule: two free terrains covering their deck's domains, and three
supported creatures costing at most three mana (at least one costs at most two).
The remaining deck is shuffled. Water starters use free multi-domain coastal
terrains, which enter exhausted; they trade early speed for flexibility.

The AI simulates legal moves and evaluates the public board, healing,
terrain development, creature survival, and lethal attacks. It cannot inspect
your hand or deck order when making decisions. It is a beginner-friendly
one-ply opponent, not a competitive search engine.

### Local table boundaries

This is **one local table** per Python process, shared by browser tabs. Revision
checks reject duplicate/stale moves. AI turns resolve on the server before their
individual public snapshots animate in the browser; snapshots never reveal the
AI hand or its legal moves. Hotseat is a shared-screen privacy feature, not authenticated
remote multiplayer. The server only binds to loopback and accepts same-origin
requests; do not expose it through a public proxy.

Matches are in memory: restarting the server loses progress. There is no custom
deck builder, online matchmaking, save system, or account system.

## Collection

- 50 terrains, including 40 with on-play abilities.
- 80 creatures: 20 land, 20 sea, 20 air, and 20 mixed-domain creatures.
- 14 supporting spells, 20 enchants, 12 buffs, and 12 debuffs.
- Filenames without `.json` are stable IDs. `_template.json` is not a card.
- `load_catalog()` in `assets/asset_import_manager.py` loads and validates the
  whole catalog independently of the current working directory. Category import
  managers expose `load_buffs`, `load_debuffs`, `load_cards`, and `load_enchants`.
- Descriptions explain abilities; structured fields, not prose, drive gameplay.
  Invalid data and broken references raise `AssetError`, illegal moves raise
  `RuleError`, and `game.events` records resolved actions and terrain-based loss.
- `classes/game.py` is the authoritative engine. `classes/match.py` supplies
  starter decks, legal-move discovery, AI, and hand-safe browser snapshots.
  `functions/server.py` serves the local API and the dependency-free `web/` UI.

## Core rules

`Game(catalog, (deck_zero, deck_one))` accepts two ordered lists of card IDs.
Each player starts at 25 health and draws up to five opening cards. Deck index
zero is drawn first; callers may shuffle before creating the game. Small decks
are allowed for scenarios; for normal play, start with 40 cards, roughly 16
terrains and no more than three copies of any non-basic card.

- Player 0 starts. All actions happen on the active player's turn, with no
  response stack or separate blocking phase.
- Play at most one terrain each turn. Existing `value` determines its
  **development cost: `value - 1` mana**, paid before it enters. Each terrain
  supplies **one mana**, not `value` mana. Strong terrain effects therefore
  compete with creatures and spells rather than providing free high-tier power.
- Single-domain terrain supplies one mana immediately. Multi-domain terrain
  enters exhausted: it provides its types and on-play effect immediately, but
  no mana until a later turn. It still supplies only one mana, not one per type.
- Mana refreshes to the number of controlled terrains at the start of your turn;
  unused mana does not carry over. Existing floating mana is not retroactively
  removed when a terrain is sacrificed.
- All listed `requiresTerrain` types must be controlled to play a card. Multiple
  types can be supplied by one multi-domain terrain. `sea` means water; there is
  no separate `water` type.
- Pay mana and consume a card only after checking its requirements and targets.
  Creatures remain alive if their summoning terrain is later lost, but their
  terrain-dependent statuses do not.
- Control at most eight creatures. A creature normally cannot attack until
  your next turn; Haste allows an immediate attack. Attack once per turn.
- `game.attack(owner, attacker_id, defender_id)` deals simultaneous creature
  combat damage and retaliation. Omit the defender to attack the enemy player.
  Damage persists until healed. Zero-health creatures and their attached
  enchants go to their respective controllers' graveyards.
- Reachable Guards must be attacked first. Flying defenders require Flying
  attackers; Water Breathing defenders require Water Breathing attackers.
  A defender with both requires both. An unreachable Guard does not prevent
  attacking the player. These abilities do not prevent spells or area effects.
- Ward blocks **selected enemy** spell/enchant targets, including enemy Unravel.
  It does not stop friendly targeting, combat, or untargeted area effects.
- Lifesteal heals the creature's controller up to 25 health. Creature combat
  healing is capped at actual remaining defender health, so overkill is not
  extra healing. Cursed prevents healing the creature, not its controller.
- At turn end, the outgoing player's creatures take status damage simultaneously.
  Deaths resolve before Regeneration can heal survivors. Their timed statuses
  then expire and deaths from lost bonus health resolve. The incoming player
  refreshes mana and attackers and draws one card. Empty-deck draws deal one
  fatigue damage. A player at zero health loses; no further actions are legal.

## Statuses, enchants, and terrain interactions

- Every status defines its modifiers, keywords, duration, terrain requirements,
  periodic damage/healing, and statuses it removes.
- Timed effects count **the affected controller's turn ends**, including the
  current turn if applied to a friendly creature. Opponent turn ends do not
  advance them. `null` duration means indefinite, not immunity to removal.
- Duplicate statuses **do not stack**. The latest application replaces the
  source and refreshes the duration. Replacing a permanent enchant effect with
  a timed spell makes that status timed; any source left with no effects ends.
- Enchants attach to one friendly or enemy creature. Their granted statuses
  last until dispelled, replaced, cleansed, countered, unsupported, or the
  creature dies, **overriding ordinary timed duration**. Removing an enchant
  removes only its own surviving statuses, never another source's replacement.
- `Unravel` dispels all enchants on its chosen creature. A controller can also
  dismiss its own enchant with `game.dispel(owner, enchant_id)`. An enchant with
  multiple effects remains while at least one effect survives.
- Terrain abilities trigger **once on play**, on creatures currently present.
  They are not continuous auras, and do not trigger for later creatures.
  Their granted statuses use ordinary durations and are not tied to that
  individual terrain. On-play effects cannot be replayed without a new card.
- `game.remove_terrain(owner, terrain_id)` sacrifices a controlled terrain.
  Any status missing a required type is immediately and **permanently removed**.
  Another terrain of that type, including a dual terrain, still supports it.
  An opponent's terrains never support your creatures.
- Returning a type does **not** restore a lost effect. Cast Tidal Gift, play
  Coral Reef after the creature, or reapply Gills of the Deep to regain Water
  Breathing. An unsupported conditional area buff is skipped with an event.
- Soaked, Frostbite, Water Breathing, and Tidal Vigor extinguish Burning.
  Burning removes Soaked and Frostbite. Regeneration removes Poisoned; Poisoned
  and Cursed remove Regeneration. Soaring removes Grounded; Grounded suppresses
  even innate Flying. Swiftness breaks Rooted and Stunned. Battle Fervor counters
  Weakened and Disarmed. Stone Skin counters Frail and Exposed.
- Maximum-health bonuses are not healing. Losing them can kill a damaged unit.

## Balance and extension

Creature base-stat budgets are tested:
`2 * attack + health + 2 * keyword_count <= 3 * cost + 3`.
Costs span 1-7, mixed-domain creatures require setup, area effects cost more than
most individual effects, statuses do not stack, and every effect has counters.
Rarity alone does not bypass costs. This is an initial balance pass, **not a
claim of competitive balance**; matchup playtesting is still needed.

To add content, follow an existing JSON asset. Cards use `type` (`creature` or
`spell`), `cost`, `requiresTerrain`, and `onPlay`; creatures also use `attack`,
`health`, and `keywords`. Terrain retains its original fields. Enchants live in
their own folder and may only apply statuses to `ally` or `enemy`.

Effects use `action` (`apply`, `remove`, `damage`, `heal`, `dispel`) and `target`
(`self`, `ally`, `enemy`, `any`, `all_allies`, `all_enemies`). Apply/remove require
a status ID; damage/heal require a positive integer `amount`. `self` is only for
creature on-play effects. Terrains use area targets. Spells and enchants target
creatures, not player health. Non-null `onDraw` is deliberately rejected until
draw-trigger rules exist.
