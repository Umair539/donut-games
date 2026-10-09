"use strict";

/* Donut Checkers. A game module for the shell in app.js (the interface is described in
 * games/connect4/connect4.js).
 *
 * The server sends the board as board[row][col], row 0 at the top on player 1's screen, and
 * the moves the player on turn can make as [row, col, toRow, toCol]. Player 2 sees the board
 * turned round, so everyone has their own donuts at the bottom.
 */
(() => {
  const SIZE = 8;
  const KING = 2; // a king is stored as its player's number + KING
  const PIECES = {
    1: "sprites/blue.png",
    2: "sprites/pink.png",
    3: "games/checkers/king_blue.png",
    4: "games/checkers/king_pink.png",
  };
  let send = () => {};
  let selected = null; // [row, col] of the donut picked up, before choosing where it goes
  let lastSeen = "";
  let state = null; // what render() last drew, for the click handler

  const owner = (square) => (square === 0 ? 0 : ((square - 1) % 2) + 1);
  const same = (a, b) => a && b && a[0] === b[0] && a[1] === b[1];
  // squares named the usual way, a1 in player 1's bottom left
  const label = (r, c) => "abcdefgh"[c] + (SIZE - r);

  function el(tag, className) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    return node;
  }

  function startingSquare(r, c) {
    if ((r + c) % 2 === 0) return 0;
    if (r < 3) return 2;
    if (r >= SIZE - 3) return 1;
    return 0;
  }

  function preview(target) {
    const grid = el("div", "ck-preview");
    for (let r = 0; r < SIZE; r++) {
      for (let c = 0; c < SIZE; c++) {
        const cell = el("i", (r + c) % 2 ? "dark" : "");
        const square = startingSquare(r, c);
        if (square) cell.dataset.piece = square;
        grid.append(cell);
      }
    }
    target.replaceChildren(grid);
  }

  function mount(root, api) {
    send = api.send;
    selected = null;
    lastSeen = "";
    state = null;
    root.innerHTML = `
      <div class="ck">
        <div class="ck-players">
          <div class="ck-tag" data-tag="me"><img alt=""><span>You</span></div>
          <span class="muted small" data-round></span>
          <div class="ck-tag" data-tag="opp">
            <img alt=""><span data-opp-name>Opponent</span><i class="dot" title="connected"></i>
          </div>
        </div>
        <p class="ck-status" data-status aria-live="polite"></p>
        <div class="ck-stop" hidden><button class="btn" type="button" data-stop>End turn</button></div>
        <div class="ck-board" data-board role="group" aria-label="Game board"></div>
      </div>`;
    const board = root.querySelector("[data-board]");
    buildBoard(board);
    root.querySelector("[data-stop]").addEventListener("click", () => {
      if (board.classList.contains("busy")) return;
      board.classList.add("busy");
      send({ type: "stop" });
    });
    board.addEventListener("click", (event) => {
      const cell = event.target.closest(".ck-cell");
      if (cell) clickSquare(board, Number(cell.dataset.r), Number(cell.dataset.c));
    });
  }

  function render(root, { you, players, data: g }) {
    const q = (selector) => root.querySelector(selector);
    const me = you;
    const opp = me === 1 ? 2 : 1;
    const other = players.find((p) => p.id === opp) || {};
    const oppStatus = other.status || "left";

    const live = g.status === "playing" && oppStatus !== "left";
    const myTurn = live && g.turn === me;

    for (const [which, player] of [["me", me], ["opp", opp]]) {
      const tag = q(`[data-tag=${which}]`);
      tag.querySelector("img").src = PIECES[player];
      tag.classList.toggle("active", live && g.turn === player);
    }
    const dot = q("[data-tag=opp] .dot");
    dot.className = `dot ${oppStatus}`;
    dot.title = oppStatus;
    q("[data-opp-name]").textContent = other.name || "Opponent";
    q("[data-round]").textContent = `Round ${g.round}`;

    const mustJump = g.forced && g.moves.some((m) => Math.abs(m[0] - m[2]) === 2);
    let status;
    if (g.status === "win") status = g.winner === me ? "You win! 🎉" : "You lose";
    else if (g.status === "draw") status = "It's a draw";
    else if (oppStatus === "left") status = "Game over";
    else if (!myTurn) status = "Their turn";
    else if (g.jumping) status = g.forced ? "Keep jumping!" : "Keep jumping, or end your turn";
    else if (mustJump) status = "Your turn: you have to jump";
    else status = "Your turn";
    q("[data-status]").textContent = status;
    // without forced jumps, keep room for End turn so the board doesn't move when it shows
    q(".ck-stop").hidden = g.forced;
    q("[data-stop]").style.visibility = myTurn && g.jumping && !g.forced ? "" : "hidden";

    // a multi-jump carries on with the same donut, so it stays picked up
    if (!myTurn) selected = null;
    else if (g.jumping) selected = g.jumping;
    else if (selected && !g.moves.some((m) => same(m, selected))) selected = null;

    state = { g, me, myTurn };
    drawBoard(q("[data-board]"));
  }

  function clickSquare(board, r, c) {
    if (!state || !state.myTurn || board.classList.contains("busy")) return;
    const { g } = state;
    const move = selected && g.moves.find((m) => same(m, selected) && m[2] === r && m[3] === c);
    if (move) {
      board.classList.add("busy"); // cleared by the next render, whether it is a state or an error
      send({ from: [move[0], move[1]], to: [move[2], move[3]] });
      return;
    }
    if (g.jumping) return; // can't swap to another donut half way through
    selected = g.moves.some((m) => m[0] === r && m[1] === c) && !same(selected, [r, c])
      ? [r, c]
      : null;
    drawBoard(board);
  }

  function buildBoard(board) {
    board.replaceChildren();
    for (let i = 0; i < SIZE * SIZE; i++) {
      const cell = el("button", "ck-cell");
      cell.type = "button";
      board.append(cell);
    }
  }

  function drawBoard(board) {
    const { g, me, myTurn } = state;
    const flip = me === 2;
    // url() in a CSS variable is resolved against the stylesheet, not the page
    const ghost = new URL(PIECES[selected ? g.board[selected[0]][selected[1]] : me], document.baseURI);
    board.style.setProperty("--ghost", `url(${ghost.href})`);
    board.classList.toggle("my-turn", myTurn);
    board.classList.remove("busy");

    const seen = JSON.stringify(g.last) + g.round;
    const moved = seen !== lastSeen && g.last.length ? g.last[g.last.length - 1] : null;
    lastSeen = seen;
    const movable = new Set(myTurn ? g.moves.map((m) => `${m[0]},${m[1]}`) : []);
    const targets = new Set(selected
      ? g.moves.filter((m) => same(m, selected)).map((m) => `${m[2]},${m[3]}`)
      : []);
    const trail = new Set(g.last.map(([r, c]) => `${r},${c}`));

    for (let i = 0; i < SIZE * SIZE; i++) {
      const cell = board.children[i];
      const r = flip ? SIZE - 1 - Math.floor(i / SIZE) : Math.floor(i / SIZE);
      const c = flip ? SIZE - 1 - (i % SIZE) : i % SIZE;
      const key = `${r},${c}`;
      const square = g.board[r][c];
      cell.dataset.r = r;
      cell.dataset.c = c;
      cell.classList.toggle("dark", (r + c) % 2 === 1);
      cell.classList.toggle("can-move", movable.has(key) && !selected);
      cell.classList.toggle("selected", same(selected, [r, c]));
      cell.classList.toggle("target", targets.has(key));
      cell.classList.toggle("trail", trail.has(key));

      const mine = owner(square) === me;
      const kind = square > KING ? "king" : "donut";
      cell.setAttribute("aria-label", label(r, c) + (square
        ? `, ${mine ? "your" : "opponent's"} ${kind}` : ""));

      if (String(square) !== cell.dataset.value) {
        cell.dataset.value = square;
        cell.replaceChildren();
        if (square) {
          const img = el("img");
          img.src = PIECES[square];
          img.alt = `${mine ? "your" : "opponent's"} ${kind}`;
          cell.append(img);
        }
      }
      const img = cell.querySelector("img");
      if (img && same(moved, [r, c])) {
        img.classList.remove("pop");
        void img.offsetWidth; // restart the animation when the same donut moves again
        img.classList.add("pop");
      }
    }
  }

  window.Games = window.Games || {};
  window.Games.checkers = {
    icon: PIECES[3],
    instructions: [
      "Take all of the other player's donuts, or leave them with no move to make.",
      { heading: "Moving" },
      {
        list: [
          "Blue goes first. Donuts move one square diagonally forward, onto the dark squares.",
          "Click one of your donuts, then the square to move it to.",
        ],
      },
      { heading: "Jumping" },
      {
        list: [
          "Jump diagonally over an opponent's donut onto the empty square behind it to take it.",
          "After a jump, keep jumping with the same donut while you can.",
          "With forced jumps on (see the lobby), if you can jump you have to, and you have to keep going to the end. When you can choose between jumps, take whichever you like.",
          "With them off, you can move instead of jumping, and end your turn part way through a chain of jumps.",
        ],
      },
      { heading: "Kings" },
      {
        list: [
          "A donut that reaches the far side becomes a king, and that ends your move.",
          "Kings move and jump backwards as well as forwards.",
        ],
      },
      { heading: "Draws" },
      "40 moves each with no jump and no ordinary donut moved is a draw.",
    ],
    preview,
    mount,
    render,
  };
})();
