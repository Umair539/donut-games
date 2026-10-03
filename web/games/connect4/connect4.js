"use strict";

/* Connect Donut. A game module for the shell in app.js.
 *
 * Every game registers an object in window.Games with:
 *   icon                 sprite shown on the home screen
 *   preview(el, values)  optional, draws a preview of the host's settings
 *   mount(root, api)     build the game's DOM once; api.send(action) sends a move
 *   render(root, ctx)    show the current state. ctx = { you, players, data, over }; each player is { id, name, status }
 * The shell owns everything around the game: lobby, banner, rematch and leave buttons.
 */
(() => {
  const SPRITES = { 1: "sprites/choccy.png", 2: "sprites/pink.png" };
  let send = () => {};
  let lastBoard = null;

  function el(tag, className) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    return node;
  }

  function preview(target, values) {
    const grid = el("div", "c4-preview");
    grid.style.setProperty("--cols", values.cols);
    grid.replaceChildren(...Array.from({ length: values.cols * values.rows }, () => el("i")));
    target.replaceChildren(grid);
  }

  function mount(root, api) {
    send = api.send;
    lastBoard = null;
    root.innerHTML = `
      <div class="c4">
        <div class="c4-players">
          <div class="c4-tag" data-tag="me"><img alt=""><span>You</span></div>
          <span class="muted small" data-round></span>
          <div class="c4-tag" data-tag="opp">
            <img alt=""><span data-opp-name>Opponent</span><i class="dot" title="connected"></i>
          </div>
        </div>
        <p class="c4-status" data-status aria-live="polite"></p>
        <div class="c4-board" data-board role="group" aria-label="Game board"></div>
      </div>`;
  }

  function render(root, { you, players, data: g }) {
    const q = (selector) => root.querySelector(selector);
    const me = you;
    const opp = me === 1 ? 2 : 1;
    const other = players.find((p) => p.id === opp) || {};
    const oppStatus = other.status || "left";

    const playing = g.status === "playing";
    const live = playing && oppStatus !== "left";
    const myTurn = live && g.turn === me;

    for (const [which, player] of [["me", me], ["opp", opp]]) {
      const tag = q(`[data-tag=${which}]`);
      tag.querySelector("img").src = SPRITES[player];
      tag.classList.toggle("active", live && g.turn === player);
    }
    const dot = q("[data-tag=opp] .dot");
    dot.className = `dot ${oppStatus}`;
    dot.title = oppStatus;
    q("[data-opp-name]").textContent = other.name || "Opponent";
    q("[data-round]").textContent = `Round ${g.round}`;

    let status;
    if (g.status === "win") status = g.winner === me ? "You win! 🎉" : "You lose";
    else if (g.status === "draw") status = "It's a draw";
    else if (oppStatus === "left") status = "Game over";
    else status = g.turn === me ? "Your turn" : "Their turn";
    q("[data-status]").textContent = status;

    renderBoard(q("[data-board]"), g, me, myTurn);
  }

  function renderBoard(board, g, me, myTurn) {
    board.style.setProperty("--cols", g.cols);
    board.style.setProperty("--rows", g.rows);
    // url() in a CSS variable is resolved against the stylesheet, not the page
    board.style.setProperty("--me", `url(${new URL(SPRITES[me], document.baseURI).href})`);
    board.classList.toggle("my-turn", myTurn);
    board.classList.remove("busy");

    const resized = !lastBoard || lastBoard.length !== g.cols || lastBoard[0].length !== g.rows;
    if (resized) buildBoard(board, g);

    for (let c = 0; c < g.cols; c++) {
      const col = board.children[c];
      const next = g.board[c].indexOf(0);
      col.disabled = !myTurn || next === -1;

      for (let r = 0; r < g.rows; r++) {
        const value = g.board[c][r];
        const cell = col.children[r];
        cell.classList.toggle("next", r === next);
        if (String(value) === cell.dataset.value) continue;
        cell.dataset.value = value;
        cell.replaceChildren();
        if (value) {
          const img = el("img");
          img.src = SPRITES[value];
          img.alt = value === me ? "your donut" : "opponent's donut";
          if (!resized && lastBoard[c][r] === 0) {
            img.classList.add("drop");
            img.style.setProperty("--fall", g.rows - r);
          }
          cell.append(img);
        }
      }
    }
    lastBoard = g.board.map((col) => col.slice());
  }

  function buildBoard(board, g) {
    board.replaceChildren();
    for (let c = 0; c < g.cols; c++) {
      const col = el("button", "c4-col");
      col.type = "button";
      col.setAttribute("aria-label", `Column ${c + 1}`);
      col.addEventListener("click", () => {
        if (col.disabled || board.classList.contains("busy")) return;
        board.classList.add("busy"); // cleared by the next render, whether it is a state or an error
        send({ col: c });
      });
      for (let r = 0; r < g.rows; r++) {
        const cell = el("div", "c4-cell");
        cell.dataset.value = "0";
        col.append(cell);
      }
      board.append(col);
    }
  }

  window.Games = window.Games || {};
  window.Games.connect4 = { icon: SPRITES[2], preview, mount, render };
})();
