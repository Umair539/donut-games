"use strict";

/* The shell shared by every game: connection, session, home, create, lobby and the frame
 * around a game. Each game lives in games/<name>/ and registers itself in window.Games
 * (see games/connect4/connect4.js for the interface). */

const SESSION_KEY = "donut-games-session";
const NAME_KEY = "donut-games-name"; // remembered in this browser so you don't retype it
const MAX_RETRY_DELAY = 5000;
const Games = window.Games || {};

const SERVER = SERVER_HOST || location.host; // see config.js
const SECURE = location.protocol === "https:";

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

// ---------- player name ----------

function savedName() {
  try {
    return localStorage.getItem(NAME_KEY) || "";
  } catch {
    return "";
  }
}

function playerName() {
  const name = $("player-name").value.trim();
  try {
    localStorage.setItem(NAME_KEY, name);
  } catch {
    // storage blocked: the name just isn't remembered
  }
  return name;
}

// ---------- connection ----------

function connect() {
  if (socket) return;
  clearTimeout(retryTimer);
  closedByServer = false;
  const ws = new WebSocket(`${SECURE ? "wss" : "ws"}://${SERVER}/ws`);
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
      state.receivedAt = performance.now();
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

    case "chat":
      addChat(msg);
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
  $("game-float").hidden = name !== "game";
  $("confirm-leave").hidden = true;
}

function showError(id, message) {
  const node = $(id);
  node.textContent = message;
  node.hidden = !message;
}

function showHome(message = "") {
  state = null;
  mountedKey = null;
  clearChat();
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

      const help = document.createElement("button");
      help.type = "button";
      help.className = "info-btn";
      help.textContent = "i";
      help.setAttribute("aria-label", `Instructions for ${info.title}`);
      help.addEventListener("click", () => openHelp(info));

      const row = document.createElement("div");
      row.className = "game-row";
      row.append(button, help);
      return row;
    }),
  );
}

// ---------- instructions overlay ----------

function openHelp(info) {
  const game = Games[info.name];
  const parts = (game && game.instructions) || ["No instructions for this game yet."];
  $("help-game").textContent = info.title;
  $("help-body").replaceChildren(
    ...parts.map((part) => {
      const p = document.createElement("p");
      if (typeof part === "string") {
        p.textContent = part;
      } else {
        p.className = "help-big";
        p.textContent = part.big;
      }
      return p;
    }),
  );
  $("help").hidden = false;
  $("help-close").focus();
}

function closeHelp() {
  $("help").hidden = true;
}

$("help-close").addEventListener("click", closeHelp);
$("help").addEventListener("click", (e) => {
  if (e.target === $("help")) closeHelp();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closeHelp();
    $("confirm-leave").hidden = true;
  }
});
$("lobby-help").addEventListener("click", () => {
  const info = state && gameInfo(state.game);
  if (info) openHelp(info);
});

$("join-code").addEventListener("input", (e) => {
  e.target.value = e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, "");
});

$("player-name").addEventListener("keydown", (e) => {
  // Enter in the name box joins, when someone sent you a code
  if (e.key === "Enter" && $("join-code").value) {
    e.preventDefault();
    $("join-form").requestSubmit();
  }
});

$("join-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const code = $("join-code").value.trim();
  if (!code) return;
  showError("home-error", "");
  pending = "join";
  send({ type: "join", code, name: playerName() });
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
    values[setting.key] = setting.default;
    const wrap = document.createElement("div");
    wrap.className = "setting";

    if (setting.type === "choice") {
      const group = document.createElement("fieldset");
      group.className = "setting-choice";
      const legend = document.createElement("legend");
      legend.textContent = setting.label;
      group.append(legend);
      const inputs = setting.options.map((option) => {
        const label = document.createElement("label");
        const input = document.createElement("input");
        input.type = "radio";
        input.name = `set-${setting.key}`;
        input.value = option.value;
        input.checked = option.value === setting.default;
        input.addEventListener("change", refreshSettings);
        const text = document.createElement("span");
        const name = document.createElement("strong");
        name.textContent = option.label;
        text.append(name);
        if (option.help) {
          const help = document.createElement("small");
          help.textContent = option.help;
          text.append(help);
        }
        label.append(input, text);
        group.append(label);
        return input;
      });
      controls[setting.key] = { inputs, wrap };
      wrap.append(group);
      form.append(wrap);
      continue;
    }

    if (setting.type === "bool") {
      const label = document.createElement("label");
      label.className = "setting-toggle";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.checked = setting.default;
      input.addEventListener("change", refreshSettings);
      label.append(input, " " + setting.label);
      controls[setting.key] = { input, wrap };
      wrap.append(label);
      form.append(wrap);
      continue;
    }
    if (setting.type !== "int") continue;

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

    controls[setting.key] = { input, output, wrap };
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
    control.wrap.hidden = !applies(setting, values);
    if (setting.type === "choice") {
      values[setting.key] = control.inputs.find((input) => input.checked).value;
      continue;
    }
    if (setting.type === "bool") {
      values[setting.key] = control.input.checked;
      continue;
    }
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
  send({ type: "create", game: creating.name, name: playerName(), settings: { ...values } });
});

// ---------- lobby ----------

// whether a setting means anything with the others as they are, e.g. seconds per turn only
// with the turn timer on
function applies(setting, settings) {
  return !setting.only_if || Boolean(settings[setting.only_if]);
}

// one line for each setting that applies, as [label, value shown]
function describeSettings(info, settings) {
  if (!info) return [];
  const shown = (setting, value) => {
    if (value === true) return "Yes";
    if (value === false) return "No";
    const option = (setting.options || []).find((o) => o.value === value);
    return option ? option.label : String(value);
  };
  return info.settings
    .filter((s) => applies(s, settings))
    .map((s) => [s.label, shown(s, settings[s.key])]);
}

function renderLobby() {
  const info = gameInfo(state.game);
  const isHost = state.you === state.host;
  const count = state.players.length;
  const ready = count >= state.min_players;

  $("lobby-game").textContent = info ? info.title : state.game;
  $("lobby-code").textContent = state.code;
  $("lobby-settings").replaceChildren(
    ...describeSettings(info, state.settings).map(([label, value]) => {
      const item = document.createElement("li");
      const name = document.createElement("span");
      name.textContent = label;
      const shown = document.createElement("strong");
      shown.textContent = value;
      item.append(name, shown);
      return item;
    }),
  );
  $("lobby-heading").textContent = `Players (${count}/${state.max_players})`;

  $("lobby-players").replaceChildren(
    ...state.players.map((p) => {
      const item = document.createElement("li");
      const dot = document.createElement("i");
      dot.className = `dot ${p.status}`;
      dot.title = p.status;
      item.append(dot, p.name);
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
      if (p.bot && isHost) {
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "remove-bot";
        remove.textContent = "×";
        remove.setAttribute("aria-label", `Remove ${p.name}`);
        remove.addEventListener("click", () => send({ type: "remove_bot", player: p.id }));
        item.append(remove);
      }
      return item;
    }),
  );

  // the host can fill empty seats with computer players, if this game has any
  const levels = (info && info.bots) || [];
  const addBots = $("add-bots");
  addBots.hidden = !isHost || !levels.length || count >= state.max_players;
  if (!addBots.hidden) {
    const addButton = (level, text) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn";
      button.textContent = text;
      button.addEventListener("click", () => send({ type: "add_bot", level }));
      return button;
    };
    if (levels.length === 1) {
      // nothing to choose between, so no need to name the level
      addBots.replaceChildren(addButton(levels[0], "Add a bot"));
    } else {
      const label = document.createElement("span");
      label.className = "muted";
      label.textContent = "Add a bot:";
      addBots.replaceChildren(
        label,
        ...levels.map((level) => addButton(level, level[0].toUpperCase() + level.slice(1))),
      );
    }
  }

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
}

async function copyText(text) {
  // navigator.clipboard only exists on https or localhost, so fall back for plain http
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.cssText = "position:fixed;top:0;left:0;opacity:0";
    document.body.append(area);
    area.select();
    try {
      return document.execCommand("copy");
    } catch {
      return false;
    } finally {
      area.remove();
    }
  }
}

function copyButton(id, label, getText) {
  const button = $(id);
  button.addEventListener("click", async () => {
    if (await copyText(getText())) {
      button.textContent = "Copied!";
      setTimeout(() => (button.textContent = label), 2000);
    } else {
      toast("Couldn't copy. Select the code and copy it by hand.");
    }
  });
}

copyButton("copy-code", "Copy code", () => state.code);
copyButton("copy-link", "Copy invite link", () => `${location.origin}/?code=${state.code}`);

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
  const left = others.filter((p) => p.status === "left");
  const away = others.filter((p) => p.status === "reconnecting");
  const names = (list) => list.map((p) => p.name).join(", ");

  if (left.length) setBanner(`${names(left)} left the game.`);
  else if (away.length) setBanner(`${names(away)} disconnected, waiting for them…`);
  else setBanner("");

  const rematch = $("rematch");
  const iAsked = state.rematch.includes(state.you);
  const theyAsked = state.rematch.some((p) => p !== state.you);
  // whoever left is dropped from the rematch, so it only needs enough players still here
  rematch.hidden = !state.over || state.players.length - left.length < state.min_players;
  rematch.disabled = iAsked;
  rematch.textContent = iAsked
    ? "Waiting for others…"
    : theyAsked ? "Others want a rematch!" : "Rematch";
  const alone = others.every((p) => p.status === "left");
  $("game-leave").title = alone ? "Back to home" : "Leave game";
  $("game-leave").setAttribute("aria-label", $("game-leave").title);
  updateTimer();
}

// the turn countdown: the server sends the seconds left, and we count down from when it arrived
function updateTimer() {
  const node = $("turn-timer");
  if (!state || state.seconds_left == null || state.over) {
    node.hidden = true;
    return;
  }
  const left = Math.max(0, Math.ceil(state.seconds_left - (performance.now() - state.receivedAt) / 1000));
  node.textContent = `⏱ ${left}s`;
  node.classList.toggle("low", left <= 5);
  node.hidden = false;
}

setInterval(() => {
  if (state && state.phase === "playing") updateTimer();
}, 250);

$("rematch").addEventListener("click", () => send({ type: "rematch" }));

function quit() {
  // if offline there is nothing to tell: the server frees the seat after the grace period
  if (session && socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: "leave" }));
  }
  leaveGame();
}

$("lobby-leave").addEventListener("click", quit);
$("game-leave").addEventListener("click", () => {
  // nothing to lose once everyone else has gone, so no need to ask
  if (state && state.players.every((p) => p.id === state.you || p.status === "left")) {
    quit();
    return;
  }
  $("confirm-leave").hidden = false;
  $("confirm-stay").focus();
});
$("confirm-stay").addEventListener("click", () => ($("confirm-leave").hidden = true));
$("confirm-go").addEventListener("click", quit);
$("confirm-leave").addEventListener("click", (e) => {
  if (e.target === $("confirm-leave")) $("confirm-leave").hidden = true;
});

// ---------- chat: kept only in this tab while the game lasts ----------

const MAX_CHAT_LINES = 100;
let unread = 0;

function chatOpen() {
  return !$("chat").hidden;
}

function addChat(msg) {
  const log = $("chat-log");
  const item = document.createElement("li");
  if (state && msg.player === state.you) item.className = "mine";
  const who = document.createElement("b");
  who.textContent = state && msg.player === state.you ? "You" : msg.name;
  item.append(who, " ", msg.text);
  log.append(item);
  while (log.children.length > MAX_CHAT_LINES) log.firstChild.remove();
  log.scrollTop = log.scrollHeight;
  if (!chatOpen()) {
    unread += 1;
    showUnread();
  }
}

function showUnread() {
  $("chat-unread").textContent = unread > 9 ? "9+" : String(unread);
  $("chat-unread").hidden = !unread;
}

function clearChat() {
  $("chat-log").replaceChildren();
  $("chat").hidden = true;
  unread = 0;
  showUnread();
}

$("chat-open").addEventListener("click", () => {
  if (chatOpen()) {
    $("chat").hidden = true;
    return;
  }
  $("chat").hidden = false;
  unread = 0;
  showUnread();
  $("chat-log").scrollTop = $("chat-log").scrollHeight;
  $("chat-input").focus();
});

$("chat-close").addEventListener("click", () => ($("chat").hidden = true));

$("chat-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = $("chat-input").value.trim();
  if (!text) return;
  send({ type: "chat", text });
  $("chat-input").value = "";
});

// ---------- start ----------

async function init() {
  try {
    gamesInfo = await (await fetch(`${SECURE ? "https" : "http"}://${SERVER}/api/games`)).json();
  } catch {
    gamesInfo = [];
  }
  buildGameList();
  $("player-name").value = savedName();

  const inviteCode = new URLSearchParams(location.search).get("code");
  if (inviteCode) history.replaceState(null, "", location.pathname);

  if (session) {
    showScreen("connecting");
    connect();
  } else {
    showHome();
    if (inviteCode) {
      $("join-code").value = inviteCode.toUpperCase().slice(0, 6);
      // with a remembered name join straight away, otherwise let them pick one first
      if ($("player-name").value) $("join-form").requestSubmit();
      else $("player-name").focus();
    }
  }
}

init();
