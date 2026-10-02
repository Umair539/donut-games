"use strict";

/* The shell shared by every game: connection, session, home, create, lobby and the frame
 * around a game. Each game lives in games/<name>/ and registers itself in window.Games
 * (see games/connect4/connect4.js for the interface). */

const SESSION_KEY = "donut-games-session";
const MAX_RETRY_DELAY = 5000;
const Games = window.Games || {};

const $ = (id) => document.getElementById(id);

let gamesInfo = []; // what the server can host, from /api/games
let socket = null;
let queue = []; // messages waiting for the socket to open
let session = loadSession(); // { code, token } once seated
let state = null; // last "state" message
let mountedKey = null; // which game instance is currently built in #game-root
let pending = null; // "create" | "join" | "rejoin" while waiting for a reply
let retryDelay = 1000;
let retryTimer = null;
let closedByServer = false;

// ---------- session storage (per tab, so two tabs can play each other) ----------

function loadSession() {
  try {
    return JSON.parse(sessionStorage.getItem(SESSION_KEY));
  } catch {
    return null;
  }
}

function saveSession(value) {
  session = value;
  try {
    if (value) sessionStorage.setItem(SESSION_KEY, JSON.stringify(value));
    else sessionStorage.removeItem(SESSION_KEY);
  } catch {
    // storage blocked: the game still works, it just can't survive a refresh
  }
}

// ---------- connection ----------

function connect() {
  if (socket) return;
  clearTimeout(retryTimer);
  closedByServer = false;
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${scheme}://${location.host}/ws`);
  socket = ws;

  ws.onopen = () => {
    retryDelay = 1000;
    if (session) {
      pending = "rejoin";
      ws.send(JSON.stringify({ type: "rejoin", code: session.code, token: session.token }));
    }
    for (const msg of queue) ws.send(JSON.stringify(msg));
    queue = [];
  };

  ws.onmessage = (event) => handle(JSON.parse(event.data));

  ws.onclose = () => {
    if (socket !== ws) return;
    socket = null;
    if (pending === "create" || pending === "join") {
      showError(`${pending === "create" ? "create" : "home"}-error`, "Could not reach the server");
      pending = null;
    }
    if (session && !closedByServer) {
      setBanner("Connection lost, reconnecting…");
      retryTimer = setTimeout(connect, retryDelay);
      retryDelay = Math.min(retryDelay * 2, MAX_RETRY_DELAY);
    }
  };
}

function send(msg) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(msg));
  } else {
    queue.push(msg);
    connect();
  }
}

function handle(msg) {
  switch (msg.type) {
    case "joined":
      pending = null;
      saveSession({ code: msg.code, token: msg.token });
      break;

    case "state":
      state = msg;
      render();
      break;

    case "error":
      if (pending === "rejoin") {
        pending = null;
        leaveGame("That game has ended.");
      } else if (pending === "create") {
        pending = null;
        showError("create-error", msg.message);
      } else if (pending === "join") {
        pending = null;
        showError("home-error", msg.message);
      } else {
        toast(msg.message);
        if (state) render(); // lets a game undo anything it did while waiting for the reply
      }
      break;

    case "closed":
      closedByServer = true;
      leaveGame(msg.message);
      break;
  }
}

// ---------- screens ----------

function showScreen(name) {
  for (const screen of document.querySelectorAll(".screen")) {
    screen.hidden = screen.id !== `screen-${name}`;
  }
}

function showError(id, message) {
  const node = $(id);
  node.textContent = message;
  node.hidden = !message;
}

function showHome(message = "") {
  state = null;
  mountedKey = null;
  showScreen("home");
  showError("home-error", message);
}

function leaveGame(message = "") {
  saveSession(null);
  showHome(message);
}

let toastTimer = null;
function toast(message) {
  const node = $("toast");
  node.textContent = message;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (node.hidden = true), 3000);
}

function setBanner(message) {
  $("banner").textContent = message;
  $("banner").hidden = !message;
}

function gameInfo(name) {
  return gamesInfo.find((g) => g.name === name);
}

// ---------- home ----------

function playerRange(info) {
  return info.min_players === info.max_players
    ? `${info.min_players} players`
    : `${info.min_players}–${info.max_players} players`;
}

function buildGameList() {
  const list = $("game-list");
  if (!gamesInfo.length) {
    const note = document.createElement("p");
    note.className = "muted";
    note.textContent = "Could not load the list of games. Try refreshing the page.";
    list.replaceChildren(note);
    return;
  }
  list.replaceChildren(
    ...gamesInfo.map((info) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "game-option";

      const icon = document.createElement("img");
      icon.alt = "";
      icon.src = (Games[info.name] && Games[info.name].icon) || "sprites/pink.png";
      const name = document.createElement("span");
      name.className = "name";
      name.textContent = info.title;
      const meta = document.createElement("span");
      meta.className = "meta";
      meta.textContent = `${info.description} · ${playerRange(info)}`;

      button.append(icon, name, meta);
      button.addEventListener("click", () => openCreate(info));
      return button;
    }),
  );
}

$("join-code").addEventListener("input", (e) => {
  e.target.value = e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, "");
});

$("join-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const code = $("join-code").value.trim();
  if (!code) return;
  showError("home-error", "");
  pending = "join";
  send({ type: "join", code });
});

// ---------- create: the settings form is built from the game's schema ----------

let creating = null; // the game being set up
let values = {}; // setting key -> chosen number
let controls = {}; // setting key -> { input, output }

function openCreate(info) {
  creating = info;
  values = {};
  controls = {};
  $("create-title").textContent = info.title;
  showError("create-error", "");

  const form = $("settings");
  form.replaceChildren();
  for (const setting of info.settings) {
    if (setting.type !== "int") continue; // the only kind of setting so far
    values[setting.key] = setting.default;

    const wrap = document.createElement("div");
    wrap.className = "setting";
    const label = document.createElement("label");
    label.htmlFor = `set-${setting.key}`;
    label.append(setting.label + " ");
    const output = document.createElement("output");
    label.append(output);
    const input = document.createElement("input");
    input.type = "range";
    input.id = `set-${setting.key}`;
    input.min = setting.min;
    input.max = setting.max === undefined ? setting.default : setting.max;
    input.value = setting.default;
    input.addEventListener("input", refreshSettings);

    controls[setting.key] = { input, output };
    wrap.append(label, input);
    form.append(wrap);
  }
  refreshSettings();
  showScreen("create");
}

function refreshSettings() {
  for (const setting of creating.settings) {
    const control = controls[setting.key];
    if (!control) continue;
    if (setting.max_of) {
      // the maximum follows other settings, e.g. you can't need 9 in a row on a 6 x 7 board
      control.input.max = Math.max(...setting.max_of.map((key) => values[key]));
    }
    values[setting.key] = Number(control.input.value); // the browser clamps it to the new max
    control.output.textContent = control.input.value;
  }
  const game = Games[creating.name];
  if (game && game.preview) game.preview($("preview"), { ...values });
}

$("create-back").addEventListener("click", () => showHome());

$("create-form").addEventListener("submit", (e) => {
  e.preventDefault();
  showError("create-error", "");
  pending = "create";
  send({ type: "create", game: creating.name, settings: { ...values } });
});

// ---------- lobby ----------

function describeSettings(info, settings) {
  if (!info) return "";
  return info.settings.map((s) => `${s.label}: ${settings[s.key]}`).join(" · ");
}

function renderLobby() {
  const info = gameInfo(state.game);
  const isHost = state.you === state.host;
  const count = state.players.length;
  const ready = count >= state.min_players;

  $("lobby-game").textContent = info ? info.title : state.game;
  $("lobby-code").textContent = state.code;
  $("lobby-settings").textContent = describeSettings(info, state.settings);
  $("lobby-heading").textContent = `Players (${count}/${state.max_players})`;

  $("lobby-players").replaceChildren(
    ...state.players.map((p) => {
      const item = document.createElement("li");
      const dot = document.createElement("i");
      dot.className = `dot ${p.status}`;
      dot.title = p.status;
      item.append(dot, `Player ${p.id}`);
      if (p.id === state.you) {
        const you = document.createElement("span");
        you.className = "you";
        you.textContent = "(you)";
        item.append(you);
      }
      if (p.id === state.host) {
        const badge = document.createElement("span");
        badge.className = "badge";
        badge.textContent = "Host";
        item.append(badge);
      }
      return item;
    }),
  );

  const wait = $("lobby-wait");
  wait.replaceChildren();
  if (!ready) {
    const spinner = document.createElement("span");
    spinner.className = "spinner small";
    spinner.setAttribute("aria-hidden", "true");
    wait.append(spinner, "Waiting for players to join…");
  } else if (isHost) {
    wait.textContent = "Start when everyone is here";
  } else {
    wait.textContent = "Waiting for the host to start…";
  }

  $("start").hidden = !isHost;
  $("start").disabled = !ready;
  $("lobby-leave").textContent = isHost ? "Cancel game" : "Leave";
}

$("copy-link").addEventListener("click", async () => {
  const link = `${location.origin}/?code=${state.code}`;
  const button = $("copy-link");
  try {
    await navigator.clipboard.writeText(link);
    button.textContent = "Copied!";
  } catch {
    button.textContent = link; // clipboard blocked: show it so it can be copied by hand
  }
  setTimeout(() => (button.textContent = "Copy invite link"), 2000);
});

$("start").addEventListener("click", () => send({ type: "start" }));

// ---------- in a game ----------

function render() {
  if (!session || !state) return;

  if (state.phase === "lobby") {
    mountedKey = null;
    showScreen("lobby");
    renderLobby();
    return;
  }

  const game = Games[state.game];
  if (!game) {
    leaveGame("This game is not available in this version.");
    return;
  }
  showScreen("game");

  const root = $("game-root");
  const key = `${state.game}:${state.code}`;
  if (mountedKey !== key) {
    root.replaceChildren();
    game.mount(root, { send: (action) => send({ type: "action", action }) });
    mountedKey = key;
  }
  game.render(root, {
    you: state.you,
    players: state.players,
    data: state.data,
    over: state.over,
  });
  renderFrame();
}

// the banner, rematch and leave buttons around whichever game is showing
function renderFrame() {
  const others = state.players.filter((p) => p.id !== state.you);
  const left = others.filter((p) => p.status === "left").length;
  const away = others.filter((p) => p.status === "reconnecting").length;

  if (left) {
    setBanner(others.length === 1 ? "Your opponent left the game."
      : `${left} player${left > 1 ? "s" : ""} left the game.`);
  } else if (away) {
    setBanner("A player disconnected, waiting for them…");
  } else {
    setBanner("");
  }

  const rematch = $("rematch");
  const iAsked = state.rematch.includes(state.you);
  const theyAsked = state.rematch.some((p) => p !== state.you);
  rematch.hidden = !state.over || left > 0;
  rematch.disabled = iAsked;
  rematch.textContent = iAsked
    ? "Waiting for others…"
    : theyAsked ? "Others want a rematch!" : "Rematch";
  $("game-leave").textContent = left && others.length === 1 ? "Back to home" : "Leave";
}

$("rematch").addEventListener("click", () => send({ type: "rematch" }));

function quit() {
  // if offline there is nothing to tell: the server frees the seat after the grace period
  if (session && socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: "leave" }));
  }
  leaveGame();
}

$("lobby-leave").addEventListener("click", quit);
$("game-leave").addEventListener("click", quit);

// ---------- start ----------

async function init() {
  try {
    gamesInfo = await (await fetch("/api/games")).json();
  } catch {
    gamesInfo = [];
  }
  buildGameList();

  const inviteCode = new URLSearchParams(location.search).get("code");
  if (inviteCode) history.replaceState(null, "", location.pathname);

  if (session) {
    showScreen("connecting");
    connect();
  } else {
    showHome();
    if (inviteCode) {
      $("join-code").value = inviteCode.toUpperCase().slice(0, 4);
      $("join-form").requestSubmit();
    }
  }
}

init();
