"use strict";

const $ = (id) => document.getElementById(id);
const escapeHTML = (value) => String(value).replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[char]));
const title = (value) => String(value).replaceAll("_", " ").replace(/\b\w/g, (char) => char.toUpperCase());
let state = null;
let token = "";
let selected = null;
let primaryAction = null;
let busy = false;
let resultShown = false;
let replaying = false;

const domainClass = (domains) => domains.length > 1 ? "domain-mixed" : `domain-${domains[0] || "land"}`;
const moves = () => state?.match?.actions || [];
const duel = () => state?.match;
const humanName = (owner) => duel()?.mode === "ai" ? (owner === 0 ? "You" : "The AI") : `Player ${owner + 1}`;

function showDialog(id) {
  const dialog = $(id);
  if (!dialog.open) dialog.showModal();
}

function notify(message) {
  $("notice").replaceChildren(document.createTextNode(message));
  const close = document.createElement("button");
  close.textContent = "Dismiss";
  close.addEventListener("click", () => { $("notice").hidden = true; });
  $("notice").append(close);
  $("notice").hidden = false;
}

async function api(path, body) {
  const options = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Azelia-Token": token },
    body: JSON.stringify(body),
  };
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) {
    if (data.state) {
      state = data.state;
      selected = null;
    }
    throw new Error(data.error || `Server error (${response.status})`);
  }
  token = data.token || token;
  return data;
}

function setBusy(value) {
  busy = value;
  $("busy-overlay").hidden = !value;
  $("end-turn").disabled = value || !duel() || duel().over || duel().handoff;
  $("start-button").disabled = value;
  $("ready-button").disabled = value;
}

async function refresh() {
  if (busy) return;
  setBusy(true);
  try {
    state = await api("/api/state");
    selected = null;
    render();
  } catch (error) {
    notify(`Unable to refresh the table: ${error.message}. Check that the Python server is running.`);
  } finally {
    setBusy(false);
  }
}

async function mutate(path, body) {
  if (busy || !state) return false;
  setBusy(true);
  $("notice").hidden = true;
  try {
    const next = await api(path, { ...body, revision: state.revision });
    try {
      await replayFrames(next);
    } finally {
      replaying = false;
      state = next;
      selected = null;
      render();
    }
    return true;
  } catch (error) {
    render();
    notify(`${error.message} If the connection was interrupted, use Refresh table before retrying.`);
    return false;
  } finally {
    replaying = false;
    $("action-banner").hidden = true;
    $("animation-layer").replaceChildren();
    setBusy(false);
  }
}

const pause = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const unitElement = (id) => document.querySelector(`.creature-row [data-select="unit"][data-id="${id}"]`)?.closest(".card");
const side = (owner) => owner === duel().viewer ? "player" : "opponent";

function centerOf(element) {
  const rect = element.getBoundingClientRect();
  return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
}

async function motion(element, keyframes, milliseconds) {
  if (reducedMotion()) return;
  await element.animate(keyframes, {
    duration: milliseconds, easing: "cubic-bezier(.22,.7,.25,1)", fill: "none",
  }).finished;
}

function banner(message) {
  $("action-banner").textContent = message;
  $("action-banner").hidden = false;
}

function flash(element) {
  if (!element || reducedMotion()) return Promise.resolve();
  return motion(element, [
    { filter: "brightness(1)", boxShadow: "0 0 0 transparent" },
    { filter: "brightness(1.7)", boxShadow: "0 0 28px #e6ca82" },
    { filter: "brightness(1)", boxShadow: "0 0 0 transparent" },
  ], 450);
}

async function revealPlay(frame) {
  const card = frame.card;
  const own = frame.owner === duel().viewer;
  const hand = duel().players[frame.owner].hand;
  const index = hand?.findIndex((entry) => entry.id === card.id);
  const origin = own && index >= 0
    ? document.querySelector(`#hand [data-id="${index}"]`)
    : $(`${side(frame.owner)}-summary`);
  const anchor = origin || $(`${side(frame.owner)}-summary`);
  anchor.scrollIntoView({ block: "nearest", behavior: "instant" });
  const start = centerOf(anchor);
  const layer = $("animation-layer");
  layer.innerHTML = `<div class="played-card">${cardHTML(card)}</div>`;
  const ghost = layer.firstElementChild;
  const stage = { x: innerWidth / 2, y: innerHeight / 2 };
  ghost.style.left = `${stage.x}px`;
  ghost.style.top = `${stage.y}px`;
  await motion(ghost, [
    { transform: `translate(-50%, -50%) translate(${start.x - stage.x}px, ${start.y - stage.y}px) scale(.5)`, opacity: 0 },
    { transform: "translate(-50%, -50%) scale(1.2)", opacity: 1 },
  ], 430);
  await pause(reducedMotion() ? 450 : 650);
  return ghost;
}

async function landPlay(frame, ghost, before) {
  const card = frame.card;
  let destination;
  if (card.kind === "creature") {
    const old = new Set(before.players[frame.owner].creatures.map((unit) => unit.id));
    const entered = duel().players[frame.owner].creatures.find((unit) => !old.has(unit.id));
    destination = entered ? unitElement(entered.id) : $(`${side(frame.owner)}-board`);
  } else if (card.kind === "terrain") {
    const old = new Set(before.players[frame.owner].terrains.map((terrain) => terrain.id));
    const entered = duel().players[frame.owner].terrains.find((terrain) => !old.has(terrain.id));
    destination = entered
      ? document.querySelector(`.terrain-strip [data-id="${entered.id}"]`)
      : $(`${side(frame.owner)}-terrains`);
  } else {
    const areaOwner = card.effects.some((effect) => effect.target === "all_enemies")
      ? 1 - frame.owner : frame.owner;
    destination = frame.action.target !== undefined
      ? unitElement(frame.action.target)
      : $(`${side(areaOwner)}-board`);
    destination ||= $(`${side(1 - frame.owner)}-board`);
  }
  destination.scrollIntoView({ block: "nearest", behavior: "instant" });
  const end = centerOf(destination);
  const start = centerOf(ghost);
  await motion(ghost, [
    { transform: "translate(-50%, -50%) scale(1.2)", opacity: 1 },
    { transform: `translate(-50%, -50%) translate(${end.x - start.x}px, ${end.y - start.y}px) scale(.35)`, opacity: 0 },
  ], 430);
  ghost.remove();
  await flash(destination);
}

async function animateAttack(frame) {
  const attacker = unitElement(frame.action.source);
  const defender = frame.action.target === undefined
    ? $(`${side(1 - frame.owner)}-summary`) : unitElement(frame.action.target);
  if (!attacker || !defender) return;
  defender.scrollIntoView({ block: "nearest", behavior: "instant" });
  const from = centerOf(attacker);
  const to = centerOf(defender);
  await motion(attacker, [
    { transform: "translate(0, 0)", zIndex: 5 },
    { transform: `translate(${(to.x - from.x) * .65}px, ${(to.y - from.y) * .65}px) scale(1.08)`, zIndex: 5, offset: .55 },
    { transform: "translate(0, 0)", zIndex: 5 },
  ], 600);
  await flash(defender);
}

async function showChanges(before, after) {
  const effects = [];
  for (const player of after.players) {
    const previous = before.players[player.owner];
    if (player.health !== previous.health) {
      effects.push(flash($(`${side(player.owner)}-summary`)));
    }
    for (const unit of player.creatures) {
      const old = previous.creatures.find((entry) => entry.id === unit.id);
      if (old && (old.health !== unit.health || old.attack !== unit.attack ||
          JSON.stringify(old.statuses) !== JSON.stringify(unit.statuses))) {
        effects.push(flash(unitElement(unit.id)));
      }
    }
  }
  await Promise.all(effects);
}

async function replayFrames(next) {
  if (!duel() || !next.frames?.length) return;
  replaying = true;
  selected = null;
  $("busy-overlay").hidden = true;
  for (const frame of next.frames) {
    const before = duel();
    const actor = humanName(frame.owner);
    const action = frame.action;
    const target = before.players.flatMap((player) => player.creatures)
      .find((unit) => unit.id === action.target);
    if (action.kind === "play") {
      banner(`${actor} played ${frame.card.name}${target ? ` on ${target.card.name}` : ""}`);
      const ghost = await revealPlay(frame);
      state = { ...next, match: frame.match };
      render();
      await landPlay(frame, ghost, before);
    } else {
      if (action.kind === "attack") {
        const attacker = before.players.flatMap((player) => player.creatures)
          .find((unit) => unit.id === action.source);
        banner(`${attacker?.card.name || actor} attacks ${target?.card.name || humanName(1 - frame.owner)}`);
        await animateAttack(frame);
      } else {
        banner(action.kind === "end_turn" ? `${actor} ended the turn` :
          action.kind === "sacrifice" ? `${actor} sacrificed a terrain` : `${actor} dismissed an enchant`);
        await pause(reducedMotion() ? 300 : 500);
      }
      state = { ...next, match: frame.match };
      render();
    }
    await showChanges(before, frame.match);
    await pause(250);
  }
  replaying = false;
}

async function act(action) {
  return mutate("/api/action", { action });
}

function artwork(card) {
  const domains = card.domains;
  let shape;
  if (card.kind === "spell" || card.kind === "enchant") {
    shape = '<path d="M50 6 62 32 88 44 62 56 50 82 38 56 12 44 38 32Z" fill="none"/><circle cx="50" cy="44" r="13" fill="none"/><path d="M50 18v52M24 44h52" fill="none"/>';
  } else if (domains.includes("air") || card.keywords.includes("flying")) {
    shape = '<path d="m50 43-9-15-9-8-20-4 8 21 15 13-9 13 19-7 5 15 5-15 19 7-9-13 15-13 8-21-20 4-9 8Z"/><path d="m20 25 21 16m39-16L59 41M50 43v18" fill="none" stroke="#14251e"/>';
  } else if (domains.includes("sea")) {
    shape = '<path d="M17 44Q42 11 76 41l14-17-2 39-12-17Q42 77 17 44Z"/><circle cx="30" cy="41" r="2" fill="#13241e"/><path d="m40 28 11-13 7 11M40 60l10 12 8-12" fill="none"/><path d="M12 77q12-7 24 0t24 0 24 0" fill="none"/>';
  } else if (card.kind === "terrain") {
    shape = '<path d="m50 8-25 34h13L16 65h29v14h10V65h29L62 42h13Z"/><path d="M12 81h76" fill="none"/>';
  } else {
    shape = '<path d="m29 24-9-15 19 9 11 10 11-10 19-9-9 15 4 17-11 28H36L25 41Z"/><path d="m34 38 9 5m23-5-9 5M43 57l7 6 7-6" fill="none" stroke="#14251e" stroke-width="2"/>';
  }
  return `<div class="card-art"><svg viewBox="0 0 100 90" aria-hidden="true">${shape}</svg></div>`;
}

function cardHTML(card, options = {}) {
  const unit = options.unit;
  const hand = options.handIndex !== undefined;
  const type = hand ? "hand" : unit ? "unit" : "library";
  const id = hand ? options.handIndex : unit ? unit.id : card.id;
  const chosen = selected?.kind === type && String(selected.id) === String(id);
  const available = hand && moves().some((action) => action.kind === "play" && action.card === card.id);
  const targetable = unit && selectionMoves().some((action) => action.target === unit.id);
  const ready = unit && unit.owner === duel().viewer && moves().some((action) => action.kind === "attack" && action.source === unit.id);
  const keywords = unit ? unit.keywords : card.keywords;
  const description = unit ? (keywords.length ? keywords.map(title).join(" / ") : "No active keywords") : card.description;
  const flags = `${available ? " playable" : ""}${chosen ? " selected" : ""}${targetable ? " targetable" : ""}${ready ? " ready" : ""}${unit && unit.attacked ? " exhausted" : ""}`;
  const duration = unit?.statuses.map((status) =>
    `<span class="status-dot ${escapeHTML(status.kind)}" title="${escapeHTML(status.description)}">${escapeHTML(status.name)}${status.remaining !== null ? ` ${status.remaining}` : ""}</span>`).join("") || "";
  const label = `${card.name}${unit ? `, ${unit.attack} attack, ${unit.health} health` : `, cost ${card.cost}`}`;
  const mainAttributes = type === "library" ? "" : `data-select="${type}" data-id="${escapeHTML(id)}"`;
  return `<article class="card ${domainClass(card.domains)}${flags}">
    <button class="card-main" ${mainAttributes} aria-label="${escapeHTML(label)}"${type === "library" ? " disabled" : ""}>
      <div class="card-header"><span class="card-name">${escapeHTML(card.name)}</span><span class="cost" title="Mana cost">${card.cost}</span></div>
      ${artwork(card)}
      <div class="card-type"><span>${escapeHTML(card.kind)}</span><span>${card.domains.map((domain) => domain[0].toUpperCase()).join(" / ")}</span></div>
      <div class="card-text clamp">${escapeHTML(description)}</div>
      ${card.kind === "creature" ? `<div class="card-stats"><span title="Attack">${unit ? unit.attack : card.attack} ATK</span><span class="health-stat" title="Remaining health">${unit ? unit.health : card.health} HP</span></div>` : `<div class="card-meta">${escapeHTML(card.rarity)}</div>`}
      ${duration ? `<div class="status-dots">${duration}</div>` : ""}
    </button>
    ${unit ? `<button class="card-inspect" data-inspect="${unit.id}" aria-label="Inspect ${escapeHTML(card.name)}">i</button>` : ""}
  </article>`;
}

function selectionMoves() {
  if (!selected || !duel()) return [];
  if (selected.kind === "hand") {
    const card = duel().players[duel().viewer].hand?.[selected.id];
    return moves().filter((action) => action.kind === "play" && action.card === card?.id);
  }
  if (selected.kind === "unit") {
    return moves().filter((action) => action.kind === "attack" && action.source === selected.id);
  }
  return [];
}

function summary(player, opponent) {
  const targetable = opponent && selectionMoves().some((action) => action.kind === "attack" && action.target === undefined);
  return `<div class="hero">
    <div class="hero-left">${opponent
    ? `<button class="hero-avatar${targetable ? " targetable" : ""}" data-face="true" aria-label="Attack opponent directly"${targetable ? "" : " disabled"}>A</button>`
    : '<div class="hero-avatar" aria-hidden="true">A</div>'}
      <div><div class="hero-title">${escapeHTML(humanName(player.owner))}</div><div class="hero-subtitle">${escapeHTML(player.deckName)}</div></div>
    </div>
    <div class="hero-resources">
      <div class="hero-counts">${player.deckCount} in deck &middot; ${player.handCount} in hand<br><button data-graveyard="${player.owner}">${player.graveyard.length} in graveyard</button></div>
      <div class="resource mana"><strong>${player.mana}</strong><span>MANA</span></div>
      <div class="resource health"><strong>${Math.max(0, player.health)}</strong><span>HEALTH</span></div>
    </div>
  </div>`;
}

function terrainsHTML(player) {
  if (!player.terrains.length) return '<span class="empty-terrain">NO TERRAINS &mdash; play one to grow your domain</span>';
  return player.terrains.map(({ id, card }) => `<button class="terrain-token ${domainClass(card.domains)}${selected?.kind === "terrain" && selected.id === id ? " selected" : ""}" data-select="terrain" data-id="${id}" title="${escapeHTML(card.description)}"><span class="domain-icon">${card.domains.map((domain) => domain[0].toUpperCase()).join("/")}</span>${escapeHTML(card.name)}</button>`).join("");
}

function renderInspector() {
  primaryAction = null;
  const match = duel();
  let card = null;
  let unit = null;
  let terrain = null;
  let terrainOwner = null;
  if (selected?.kind === "hand") card = match.players[match.viewer].hand?.[selected.id];
  if (selected?.kind === "unit") {
    unit = match.players.flatMap((player) => player.creatures).find((creature) => creature.id === selected.id);
    card = unit?.card;
  }
  if (selected?.kind === "terrain") {
    for (const player of match.players) {
      const found = player.terrains.find((entry) => entry.id === selected.id);
      if (found) { terrain = found; card = found.card; terrainOwner = player.owner; }
    }
  }
  if (!card || match.handoff) {
    $("inspector").innerHTML = '<div class="inspector-empty"><span aria-hidden="true">&#9671;</span><h2>Every card tells a story.</h2><p>Select a card to read its abilities, see its effects, and make your move.</p><p class="small">Glowing hand cards can be played. A green edge marks a creature that can attack.</p></div>';
    return;
  }
  const options = selectionMoves();
  let control = "";
  let reason = "";
  if (!match.over && selected.kind === "hand") {
    const targeted = card.effects.some((effect) => ["ally", "enemy", "any"].includes(effect.target));
    if (targeted && options.length) {
      reason = "Choose a highlighted creature on the battlefield.";
      control = '<button class="gold" disabled>Choose a target</button>';
    } else {
      primaryAction = options.find((action) => action.target === undefined) || null;
      reason = primaryAction ? `${card.cost} mana${card.kind === "terrain" ? " development cost" : ""}.` : (match.blocked[card.id] || "No legal target is available.");
      control = `<button class="gold" id="primary-action"${primaryAction ? "" : " disabled"}>Play ${escapeHTML(card.kind)}</button>`;
    }
  } else if (!match.over && unit && unit.owner === match.viewer) {
    primaryAction = options.find((action) => action.target === undefined) || null;
    reason = options.length ? "Choose a highlighted enemy or attack the opponent directly." : (match.blocked[`unit:${unit.id}`] || "This creature cannot attack.");
    control = `<button class="gold" id="primary-action"${primaryAction ? "" : " disabled"}>${primaryAction ? "Attack opponent" : options.length ? "Choose a highlighted enemy" : "Cannot attack"}</button>`;
  } else if (!match.over && terrain && terrainOwner === match.viewer) {
    control = `<button class="danger" data-sacrifice="${terrain.id}">Sacrifice terrain</button>`;
    reason = "Permanent: dependent buffs may be lost, and damaged creatures can die.";
  }
  const statuses = unit?.statuses.map((status) => `<div class="effect-entry ${escapeHTML(status.kind)}"><strong>${escapeHTML(status.name)} <span class="muted">${status.source !== null ? "/ enchanted" : status.remaining === null ? "/ indefinite" : `/ ${status.remaining} turn ends`}</span></strong><p>${escapeHTML(status.description)}</p></div>`).join("") || "";
  const attached = unit ? match.enchants.filter((enchant) => enchant.target === unit.id) : [];
  $("inspector").innerHTML = `<div class="detail-card ${domainClass(card.domains)}">
    ${artwork(card)}
    <div class="detail-body">
      <div class="detail-line"><span>${escapeHTML(title(card.kind))} / ${escapeHTML(card.rarity)}</span><span>${card.cost} MANA</span></div>
      <h2>${escapeHTML(card.name)}</h2>
      <p>${escapeHTML(card.description)}</p>
      <p class="small">${card.kind === "terrain" ? "Provides" : "Requires"}: ${escapeHTML(card.domains.map(title).join(" + ") || "Any domain")}</p>
      ${card.kind === "creature" ? `<div class="detail-stats"><span><strong>${unit ? unit.attack : card.attack}</strong>ATTACK</span><span><strong>${unit ? `${unit.health}/${unit.maxHealth}` : card.health}</strong>HEALTH</span></div>` : ""}
      ${(unit ? unit.keywords : card.keywords).map((keyword) => `<span class="keyword">${escapeHTML(title(keyword))}</span>`).join("")}
      ${statuses}
      ${attached.length ? `<div class="attached"><p class="eyebrow">ATTACHED ENCHANTS</p>${attached.map((enchant) => `<div><span title="${escapeHTML(enchant.card.description)}">${escapeHTML(enchant.card.name)}<small class="muted"> / ${escapeHTML(humanName(enchant.owner))}</small></span>${enchant.owner === match.viewer && !match.over ? `<button data-dismiss="${enchant.id}" aria-label="Dismiss ${escapeHTML(enchant.card.name)}">Dismiss</button>` : ""}</div>`).join("")}</div>` : ""}
      ${reason ? `<div class="reason">${escapeHTML(reason)}</div>` : ""}
      ${control}
      <button class="text-button" id="clear-selection">Clear selection</button>
    </div>
  </div>`;
}

function render() {
  if (!state) return;
  const match = duel();
  $("welcome").hidden = !!match;
  $("table").hidden = !match;
  if (!match) return;
  const player = match.players[match.viewer];
  const opponent = match.players[1 - match.viewer];
  $("opponent-summary").innerHTML = summary(opponent, true);
  $("player-summary").innerHTML = summary(player, false);
  $("opponent-terrains").innerHTML = terrainsHTML(opponent);
  $("player-terrains").innerHTML = terrainsHTML(player);
  $("opponent-board").innerHTML = opponent.creatures.map((unit) => cardHTML(unit.card, { unit })).join("") || '<div class="empty-board">The other side of the wilds is quiet.</div>';
  $("player-board").innerHTML = player.creatures.map((unit) => cardHTML(unit.card, { unit })).join("") || '<div class="empty-board">Your creatures will gather here.</div>';
  $("hand").innerHTML = player.hand?.map((card, handIndex) => cardHTML(card, { handIndex })).join("") || '<p class="muted small">No cards in hand.</p>';
  $("hand-count").textContent = ` / ${player.handCount}`;
  $("turn-number").textContent = `TURN ${match.turn}`;
  $("turn-status").textContent = match.over ? "Duel complete" : match.mode === "hotseat" ? `Player ${match.active + 1}'s turn` : match.active === 0 ? "Your turn" : "The AI's turn";
  renderInspector();
  $("quick-action").hidden = !primaryAction;
  $("quick-action").textContent = primaryAction?.kind === "attack" ? "Attack opponent" : "Play selected card";
  const options = selectionMoves();
  $("target-prompt").textContent = selected
    ? options.some((action) => action.target !== undefined)
      ? "Click a highlighted target. Escape cancels."
      : primaryAction ? "Use the action in the card details." : "Read this card in the details panel."
    : player.terrainPlayed ? "Terrain played. Summon, cast, or attack." : "Play a terrain to grow your mana.";
  $("event-log").innerHTML = [...match.events].reverse().map((event) => `<li>${escapeHTML(event.replace(/Player ([01])('s)?/g, (_, owner, possessive) => {
    const name = humanName(Number(owner));
    return possessive ? name === "You" ? "Your" : `${name}'s` : name;
  }))}</li>`).join("");
  $("end-turn").disabled = busy || match.over || match.handoff;
  if (!replaying && match.handoff && !match.over) {
    $("handoff-title").textContent = `Player ${match.active + 1}, your turn.`;
    $("handoff-description").textContent = `Pass the screen to Player ${match.active + 1}. The next hand is hidden until they are ready.`;
    showDialog("handoff-dialog");
  } else {
    $("handoff-dialog").close();
  }
  if (!match.over) resultShown = false;
  if (!replaying && match.over && !resultShown) {
    resultShown = true;
    $("result-title").textContent = match.winner === null ? "A draw." : match.mode === "ai" ? match.winner === 0 ? "Victory is yours." : "The wilds prevail." : `Player ${match.winner + 1} wins.`;
    $("result-description").textContent = `A ${match.turn}-turn duel between ${match.players[0].deckName} and ${match.players[1].deckName}. Choose new decks for another adventure.`;
    showDialog("result-dialog");
  }
}

function deckPreview(prefix) {
  const deck = state.starters.find((entry) => entry.id === $(`${prefix}-deck`).value);
  $(`${prefix}-preview`).className = `deck-preview ${domainClass(deck.domains)}`;
  $(`${prefix}-preview`).innerHTML = `<p class="eyebrow">${escapeHTML(deck.domains.map(title).join(" + "))}</p><h3>${escapeHTML(deck.name)}</h3><p>${escapeHTML(deck.description)}</p><details><summary>View 40-card deck</summary><ul class="deck-list">${deck.cards.map(({ card, count }) => `<li title="${escapeHTML(card.description)}">${count} &times; ${escapeHTML(card.name)} <span class="muted">/ ${card.cost} mana</span></li>`).join("")}</ul></details>`;
}

function openSetup() {
  if (!state) { notify("The table is still connecting. Use Refresh table if the server is available."); return; }
  for (const prefix of ["first", "second"]) {
    const previous = $(`${prefix}-deck`).value;
    $(`${prefix}-deck`).innerHTML = state.starters.map((deck) => `<option value="${escapeHTML(deck.id)}">${escapeHTML(deck.name)}</option>`).join("");
    $(`${prefix}-deck`).value = previous || (prefix === "first" ? "wildwood" : "depths");
    deckPreview(prefix);
  }
  showDialog("setup-dialog");
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest("button");
  if (!button || busy) return;
  if (button.dataset.close) { $(button.dataset.close).close(); return; }
  if (["new-button", "welcome-start", "handoff-new", "rematch-button"].includes(button.id)) {
    $("result-dialog").close();
    openSetup();
    return;
  }
  if (button.id === "rules-button") { showDialog("rules-dialog"); return; }
  if (button.id === "refresh-button") { await refresh(); return; }
  if (button.id === "end-turn") { await act({ kind: "end_turn" }); return; }
  if (button.id === "ready-button") { await act({ kind: "ready" }); return; }
  if (["primary-action", "quick-action"].includes(button.id) && primaryAction) { await act(primaryAction); return; }
  if (button.id === "clear-selection") { selected = null; render(); return; }
  if (button.dataset.face) {
    const action = selectionMoves().find((move) => move.kind === "attack" && move.target === undefined);
    if (action) await act(action);
    return;
  }
  if (button.dataset.sacrifice) {
    if (confirm("Permanently sacrifice this terrain? Dependent buffs may be removed and damaged creatures may die.")) {
      await act({ kind: "sacrifice", source: Number(button.dataset.sacrifice) });
    }
    return;
  }
  if (button.dataset.dismiss) {
    if (confirm("Dismiss this enchant and remove its remaining effects?")) {
      await act({ kind: "dismiss", source: Number(button.dataset.dismiss) });
    }
    return;
  }
  if (button.dataset.graveyard !== undefined) {
    const owner = Number(button.dataset.graveyard);
    $("graveyard-title").textContent = `${humanName(owner)} / Graveyard`;
    $("graveyard-cards").innerHTML = duel().players[owner].graveyard.map((card) => cardHTML(card)).join("") || '<p class="muted">No cards have fallen yet.</p>';
    showDialog("graveyard-dialog");
    return;
  }
  if (button.dataset.inspect) {
    selected = { kind: "unit", id: Number(button.dataset.inspect) };
    render();
    return;
  }
  if (button.dataset.select) {
    const kind = button.dataset.select;
    const id = Number(button.dataset.id);
    if (kind === "unit") {
      const action = selectionMoves().find((move) => move.target === id);
      if (action) { await act(action); return; }
    }
    selected = { kind, id };
    render();
  }
});

$("setup-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy) return;
  if (duel() && !duel().over && !confirm("Replace the current duel? Its progress will be lost.")) return;
  $("setup-dialog").close();
  const ok = await mutate("/api/start", {
    mode: $("mode").value, first: $("first-deck").value, second: $("second-deck").value,
  });
  if (!ok) showDialog("setup-dialog");
});
for (const prefix of ["first", "second"]) {
  $(`${prefix}-deck`).addEventListener("change", () => deckPreview(prefix));
}
$("mode").addEventListener("change", () => {
  $("second-label").textContent = $("mode").value === "ai" ? "Opponent's deck" : "Player 2's deck";
});
$("handoff-dialog").addEventListener("cancel", (event) => event.preventDefault());
document.addEventListener("keydown", (event) => {
  if (busy) return;
  if (event.key === "Escape" && selected) { selected = null; render(); }
});
refresh();
