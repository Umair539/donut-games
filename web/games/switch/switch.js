"use strict";

/* Donut Cards. A game module for the shell in app.js (see games/connect4/connect4.js for
 * the interface). The server checks every move; the rules here only light up which cards
 * you can pick, so they must match Server/games/switch.py. */
(() => {
  const CARDS = "games/switch/cards/";
  const BACK = `${CARDS}back.png`;
  const SUITS = ["S", "H", "D", "C"];
  const SYMBOLS = { S: "♠", H: "♥", D: "♦", C: "♣" };
  const SUIT_NAMES = { S: "spades", H: "hearts", D: "diamonds", C: "clubs" };
  // each suit is a donut, as on the card faces
  const DONUTS = {
    S: { src: "sprites/choccy.png", name: "chocolate" },
    H: { src: "sprites/pink.png", name: "pink" },
    D: { src: "sprites/orange.png", name: "orange" },
    C: { src: "sprites/blue.png", name: "blue" },
  };
  const RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"];
  const RANK_NAMES = { A: "ace", J: "jack", Q: "queen", K: "king" };
  const COUNTERS = { two: "2", jack: "J", skip: "8" };

  let send = () => {};
  let ctx = null; // last render context
  let selected = []; // cards picked to play, in order, as indexes into the shown hand
  let chosenSuit = null; // for a turn that ends on an ace
  let handKey = "";

  const rank = (card) => card.slice(0, -1);
  const suit = (card) => card.slice(-1);
  const label = (card) => rank(card) + SYMBOLS[suit(card)];
  const image = (card) =>
    `${CARDS}${RANK_NAMES[rank(card)] || rank(card)}_of_${SUIT_NAMES[suit(card)]}.png`;

  function canStart(card, g) {
    if (g.pending) return rank(card) === COUNTERS[g.pending.kind];
    const top = g.discard[g.discard.length - 1];
    return rank(card) === "A" || suit(card) === g.suit || rank(card) === rank(top);
  }

  function connects(prev, card) {
    if (rank(prev) === "Q") return suit(card) === suit(prev) || rank(card) === "Q";
    if (rank(card) === rank(prev)) return true; // an ace is only wild as the first card
    const gap = Math.abs(RANKS.indexOf(rank(card)) - RANKS.indexOf(rank(prev)));
    return suit(card) === suit(prev) && (gap === 1 || gap === RANKS.length - 1);
  }

  function sortHand(hand) {
    const key = (c) => SUITS.indexOf(suit(c)) * 20 + RANKS.indexOf(rank(c));
    return hand.slice().sort((a, b) => key(a) - key(b));
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function cardImg(src, alt) {
    const img = el("img");
    img.src = src;
    img.alt = alt;
    img.draggable = false;
    return img;
  }

  // The server writes cards as text, e.g. "played 5♠ 6♠". Show each suit as its donut.
  const SUIT_OF = Object.fromEntries(SUITS.map((s) => [SYMBOLS[s], s]));
  function withDonuts(text) {
    return text.split(/([♠♥♦♣])/).filter(Boolean).map((part) => {
      const s = SUIT_OF[part];
      if (!s) return part;
      const img = cardImg(DONUTS[s].src, ` ${DONUTS[s].name}`);
      img.className = "sw-pip";
      img.title = DONUTS[s].name;
      return img;
    });
  }

  function preview(target, values) {
    const fan = el("div", "sw-preview");
    for (let i = 0; i < values.hand_size; i++) fan.append(cardImg(BACK, ""));
    target.replaceChildren(fan);
  }

  function mount(root, api) {
    send = api.send;
    selected = [];
    chosenSuit = null;
    handKey = "";
    root.innerHTML = `
      <div class="sw">
        <ul class="sw-players" data-players></ul>
        <div class="sw-table">
          <button class="sw-pile" type="button" data-pile aria-label="Pick-up pile">
            <img src="${BACK}" alt=""><span data-pile-count></span>
          </button>
          <div class="sw-discard" data-discard></div>
        </div>
        <p class="sw-status" data-status aria-live="polite"></p>
        <ol class="sw-log muted small" data-log></ol>
        <div class="sw-suits" data-suits hidden>
          <span class="small">Ask for</span>
          ${SUITS.map((s) => `<button type="button" class="sw-suit-btn suit-${s}"
              data-choose="${s}" aria-label="${DONUTS[s].name} ${SYMBOLS[s]}"
              title="${DONUTS[s].name}"><img src="${DONUTS[s].src}" alt=""></button>`).join("")}
        </div>
        <div class="sw-controls">
          <button class="btn btn-ghost" type="button" data-clear>Clear</button>
          <button class="btn sw-call" type="button" data-call>Cards!</button>
          <button class="btn" type="button" data-draw>Pick up</button>
          <button class="btn btn-primary" type="button" data-play>Play</button>
        </div>
        <div class="sw-hand" data-hand role="group" aria-label="Your cards"></div>
      </div>`;

    const q = (selector) => root.querySelector(selector);
    q("[data-clear]").addEventListener("click", () => {
      selected = [];
      chosenSuit = null;
      paint(root);
    });
    const draw = () => {
      if (!myTurn() || mustPlay(ctx.data) || root.classList.contains("busy")) return;
      root.classList.add("busy"); // cleared by the next render, a state or an error
      send({ type: "draw" });
    };
    q("[data-call]").addEventListener("click", () => {
      if (!myTurn() || ctx.data.calling || root.classList.contains("busy")) return;
      root.classList.add("busy");
      send({ type: "call" });
    });
    q("[data-draw]").addEventListener("click", draw);
    q("[data-pile]").addEventListener("click", draw);
    q("[data-play]").addEventListener("click", () => {
      if (!selected.length || root.classList.contains("busy")) return;
      const hand = sortHand(ctx.data.hand);
      const cards = selected.map((i) => hand[i]);
      const action = { type: "play", cards };
      if (rank(cards[cards.length - 1]) === "A") action.suit = chosenSuit;
      root.classList.add("busy");
      send(action);
    });
    for (const button of root.querySelectorAll("[data-choose]")) {
      button.addEventListener("click", () => {
        chosenSuit = button.dataset.choose;
        paint(root);
      });
    }
  }

  function myTurn() {
    return ctx && ctx.data.status === "playing" && ctx.data.turn === ctx.you;
  }

  // with "must play if you can" on, you can only pick up when nothing in your hand goes
  function mustPlay(g) {
    return g.force && !g.pending && g.hand.some((card) => canStart(card, g));
  }

  function nameOf(id) {
    if (ctx && id === ctx.you) return "You";
    const player = ctx && ctx.players.find((p) => p.id === id);
    return player ? player.name : `Player ${id}`;
  }

  function render(root, context) {
    ctx = context;
    const g = ctx.data;
    root.classList.remove("busy");
    const key = `${g.round}:${g.hand.join(",")}:${g.turn}`;
    if (key !== handKey) {
      selected = [];
      chosenSuit = null;
      handKey = key;
    }
    const q = (selector) => root.querySelector(selector);

    // players, in seat order starting with you
    const counts = new Map(g.counts.map((c) => [c.id, c.cards]));
    const called = new Set(g.called || []);
    const order = ctx.players.slice().sort((a, b) => a.id - b.id);
    const mine = order.findIndex((p) => p.id === ctx.you);
    const rotated = order.slice(mine).concat(order.slice(0, mine));
    q("[data-players]").replaceChildren(
      ...rotated.map((p) => {
        const item = el("li", "sw-player");
        const playing = g.status === "playing";
        item.classList.toggle("active", playing && g.turn === p.id);
        // who goes after, which shows the way play is going
        item.classList.toggle("next", playing && g.next === p.id && g.next !== g.turn);
        item.classList.toggle("gone", p.status === "left");
        const dot = el("i", `dot ${p.status}`);
        dot.title = p.status;
        const name = el("span", "sw-name", p.id === ctx.you ? `${p.name} (you)` : p.name);
        const place = g.places.indexOf(p.id);
        if (place !== -1) {
          item.classList.add("out");
          item.append(dot, name, el("span", "sw-place", placeText(place + 1)));
          return item;
        }
        const count = el("span", "sw-count");
        count.append(cardImg(BACK, ""), String(counts.get(p.id) ?? 0));
        item.append(dot, name, count);
        if (called.has(p.id)) {
          const badge = el("span", "sw-called", "Cards!");
          badge.title = "Called cards: could go out on their next go";
          item.append(badge);
        }
        return item;
      }),
    );

    // table
    q("[data-pile-count]").textContent = g.pile;
    q("[data-discard]").replaceChildren(
      ...g.discard.map((card, i) => {
        const img = cardImg(image(card), label(card));
        img.style.setProperty("--i", i - g.discard.length + 1);
        return img;
      }),
    );

    // the status line says what the table needs to: whose turn, a suit an ace asked for,
    // and an attack waiting for someone else
    const myPlace = g.places.indexOf(ctx.you) + 1;
    const parts = [];
    if (g.status === "win") {
      if (g.winner === ctx.you) parts.push("You win! 🎉");
      else if (myPlace) parts.push(`${nameOf(g.winner)} wins`, `you came ${placeText(myPlace)}`);
      else parts.push(`${nameOf(g.winner)} wins`);
    } else {
      if (myPlace) parts.push(`You came ${placeText(myPlace)}, watching`);
      parts.push(myTurn() ? "Your turn" : `${nameOf(g.turn)}'s turn`);
      if (rank(g.discard[g.discard.length - 1]) === "A") {
        parts.push(`suit changed to ${DONUTS[g.suit].name} ${SYMBOLS[g.suit]}`);
      }
      if (myTurn()) parts.push(hint(g));
      else if (g.pending) parts.push(facing(g.pending));
    }
    q("[data-status]").replaceChildren(...withDonuts(parts.join(" · ")));

    q("[data-log]").replaceChildren(
      ...g.log.slice().reverse().map((entry) => {
        const item = el("li");
        item.append(...withDonuts(entry.player ? `${nameOf(entry.player)} ${entry.text}` : entry.text));
        return item;
      }),
    );

    paint(root);
  }

  function placeText(n) {
    const medals = { 1: "🥇 1st", 2: "🥈 2nd", 3: "🥉 3rd" };
    return medals[n] || `${n}th`;
  }

  // an attack waiting for someone else
  function facing(pending) {
    if (pending.kind !== "skip") return `facing pick up ${pending.count}`;
    return pending.count === 1 ? "facing an 8" : `facing ${pending.count} skips`;
  }

  function hint(g) {
    if (mustPlay(g)) return "pick cards to play (you must play if you can)";
    if (!g.pending) return "pick cards to play, or pick up";
    if (g.pending.kind === "skip") return "play an 8 or miss your turn";
    const card = g.pending.kind === "two" ? "a 2" : "a jack";
    return `play ${card} or pick up ${g.pending.count}`;
  }

  // the hand and buttons, which change as you pick cards without waiting for the server
  function paint(root) {
    const g = ctx.data;
    const q = (selector) => root.querySelector(selector);
    const hand = sortHand(g.hand);
    const turn = myTurn();
    const last = selected.length ? hand[selected[selected.length - 1]] : null;

    q("[data-hand]").replaceChildren(
      ...hand.map((card, i) => {
        const button = el("button", "sw-card");
        button.type = "button";
        button.append(cardImg(image(card), label(card)));
        const order = selected.indexOf(i);
        // when the host allows mistakes, spotting what goes is up to you
        const pickable = turn && order === -1 && (g.mistakes !== "blocked" ||
          (last === null ? canStart(card, g) : connects(last, card)));
        if (order !== -1) {
          button.classList.add("selected");
          button.append(el("span", "sw-order", String(order + 1)));
        } else if (turn && !pickable) {
          button.classList.add("dim");
        }
        button.disabled = !turn;
        button.addEventListener("click", () => {
          if (order !== -1) selected = selected.slice(0, order); // unpick it and what follows
          else if (pickable) selected.push(i);
          else return;
          if (!selected.length || rank(hand[selected[selected.length - 1]]) !== "A") chosenSuit = null;
          paint(root);
        });
        return button;
      }),
    );

    const endsOnAce = last !== null && rank(last) === "A";
    q("[data-suits]").hidden = !endsOnAce;
    for (const button of root.querySelectorAll("[data-choose]")) {
      button.classList.toggle("chosen", button.dataset.choose === chosenSuit);
    }

    const play = q("[data-play]");
    play.disabled = !turn || !selected.length || (endsOnAce && !chosenSuit);
    play.textContent = selected.length > 1 ? `Play ${selected.length}` : "Play";
    q("[data-clear]").hidden = !selected.length;

    const call = q("[data-call]");
    call.disabled = !turn || g.calling;
    call.textContent = turn && g.calling ? "Called!" : "Cards!";

    const draw = q("[data-draw]");
    draw.disabled = !turn || mustPlay(g);
    q("[data-pile]").disabled = draw.disabled;
    if (g.pending && g.pending.kind === "skip") draw.textContent = "Miss turn";
    else if (g.pending) draw.textContent = `Pick up ${g.pending.count}`;
    else draw.textContent = "Pick up";
  }

  window.Games = window.Games || {};
  window.Games.switch = {
    icon: `${CARDS}ace_of_spades.png`,
    instructions: [
      "Each donut is a suit: chocolate ♠ and blue ♣ are the dark suits, pink ♥ and orange ♦ the bright ones.",
      "Be the first to empty your hand. On your turn play a card matching the suit or rank of the top card, or an ace (wild, you pick the suit). Can't or won't? Pick up a card.",
      "You can play several cards in one turn: same rank, or the same suit one step up or down (A sits next to 2 and K).",
      "2: next player picks up 2 (stacks). Chocolate or blue jack: next player picks up 5, or what the host chose (a pink or orange jack cancels it). 8: next player misses a turn. Counter an attack with the same kind of card, or take it. Whether 8s answered with 8s add up, start again or can't be answered at all is the host's choice, shown in the lobby.",
      "King: reverses direction when your turn ends on it (an odd number of kings at the end: K or K K K, not K K). The player whose name is outlined in orange goes next. Queen: cover it with the same suit or another queen, or pick up 1.",
      "You can't go out on a 2, 8, J, Q, K or ace: you pick up 1 instead.",
      "Play a card that doesn't go (if the host allows it) and it comes back to you with a pick-up of 1, plus any attack you were facing. Depending on the host's choice, the cards before it stay played or come back too.",
      "Cards! Press the red Cards button during the turn before the one you plan to go out on, then play your last cards on your next go. Everyone sees who has called. Go out without calling on your previous turn and you pick up 1 instead. If the host switched it on, calling and then not going out on your next go costs you 1 too. Being skipped by an 8 doesn't use up your call.",
    ],
    preview,
    mount,
    render,
  };
})();
