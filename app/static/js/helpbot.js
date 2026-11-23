/* PORTIA Assistant panel. All text is inserted with textContent (no HTML injection). */
(function () {
  "use strict";
  var panel = document.querySelector("[data-help-panel]");
  var fab = document.querySelector("[data-help-open]");
  if (!panel || !fab) return;

  var log = panel.querySelector("[data-help-log]");
  var form = panel.querySelector("[data-help-form]");
  var input = panel.querySelector("[data-help-input]");
  var askUrl = panel.getAttribute("data-ask-url");
  var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  var vv = window.visualViewport;
  var busy = false;

  function isPhone() { return window.matchMedia("(max-width: 760px)").matches; }
  function isOpen() { return !panel.hidden; }

  function open() {
    if (isOpen()) return;
    panel.hidden = false;
    fab.setAttribute("aria-expanded", "true");
    document.body.classList.add("help-open");
    history.pushState({ psbHelp: true }, "");
    // Don't pop the keyboard up on touch screens; focus the box on computers.
    if (!window.matchMedia("(pointer: coarse)").matches) input.focus({ preventScroll: true });
    scrollLog();
  }

  function hide() {
    panel.hidden = true;
    panel.classList.remove("is-typing");
    resetSize();
    fab.setAttribute("aria-expanded", "false");
    document.body.classList.remove("help-open");
    fab.focus({ preventScroll: true });
  }

  function close() {
    if (!isOpen()) return;
    if (history.state && history.state.psbHelp) history.back(); else hide();
  }

  window.addEventListener("popstate", function () { if (isOpen()) hide(); });
  fab.addEventListener("click", function () { if (isOpen()) close(); else open(); });
  panel.querySelector("[data-help-close]").addEventListener("click", close);
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && isOpen()) close(); });

  /* ---- Typing on phones: fill the space above the keyboard ---- */
  function fitToKeyboard() {
    if (!isPhone() || !vv || !panel.classList.contains("is-typing")) return;
    panel.style.top = (vv.offsetTop + 8) + "px";
    panel.style.bottom = "auto";
    panel.style.height = Math.max(200, vv.height - 16) + "px";
  }
  function resetSize() { panel.style.top = ""; panel.style.bottom = ""; panel.style.height = ""; }

  input.addEventListener("focus", function () {
    if (!isPhone()) return;
    panel.classList.add("is-typing");
    fitToKeyboard();
    setTimeout(function () { fitToKeyboard(); scrollLog(); }, 250);
  });
  input.addEventListener("blur", function () {
    setTimeout(function () {
      if (document.activeElement !== input) { panel.classList.remove("is-typing"); resetSize(); }
    }, 120);
  });
  if (vv) { vv.addEventListener("resize", fitToKeyboard); vv.addEventListener("scroll", fitToKeyboard); }

  /* ---- Conversation ---- */
  function scrollLog() { log.scrollTop = log.scrollHeight; }

  function bubble(kind) {
    var div = document.createElement("div");
    div.className = "help-msg " + kind;
    log.appendChild(div);
    return div;
  }

  function addText(parent, tag, text) {
    var el = document.createElement(tag);
    el.textContent = text;
    parent.appendChild(el);
    return el;
  }

  function renderAnswer(data) {
    var b = bubble("bot");
    if (data.title) addText(b, "strong", data.title);
    (data.blocks || []).forEach(function (block) {
      if (block.type === "ul") {
        var ul = document.createElement("ul");
        block.items.forEach(function (item) { addText(ul, "li", item); });
        b.appendChild(ul);
      } else {
        addText(b, "p", block.text);
      }
    });
    if (data.note) addText(b, "p", data.note).className = "help-note";
    if (data.links && data.links.length) {
      var row = document.createElement("div");
      row.className = "help-links";
      data.links.forEach(function (l) {
        if (typeof l.url !== "string" || l.url.charAt(0) !== "/") return; // same-site links only
        var a = addText(row, "a", l.label + " →");
        a.href = l.url;
      });
      b.appendChild(row);
    }
    if (data.related && data.related.length) {
      var rel = document.createElement("div");
      rel.className = "help-related";
      data.related.forEach(function (t) {
        var btn = addText(rel, "button", t);
        btn.type = "button";
        btn.className = "help-chip";
        btn.setAttribute("data-help-q", "");
      });
      b.appendChild(rel);
    }
    scrollLog();
  }

  function ask(question) {
    question = (question || "").trim();
    if (!question || busy) return;
    busy = true;
    addText(bubble("me"), "p", question);
    var waiting = bubble("bot is-waiting");
    addText(waiting, "p", "Looking that up…");
    scrollLog();
    fetch(askUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf, "Accept": "application/json" },
      body: JSON.stringify({ question: question })
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) { return { ok: r.ok, status: r.status, data: data }; });
    }).then(function (res) {
      waiting.remove();
      if (res.ok) renderAnswer(res.data);
      else if (res.status === 429) renderAnswer({ blocks: [{ type: "p", text: "You're asking very quickly. Please wait a moment and try again." }] });
      else if (res.status === 401) renderAnswer({ blocks: [{ type: "p", text: "Your session has ended. Please sign in again." }] });
      else renderAnswer({ blocks: [{ type: "p", text: (res.data && res.data.message) || "Something went wrong. Please try again." }] });
    }).catch(function () {
      waiting.remove();
      renderAnswer({ blocks: [{ type: "p", text: "You seem to be offline. Check your connection and try again." }] });
    }).then(function () { busy = false; });
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var q = input.value;
    input.value = "";
    ask(q);
  });
  panel.addEventListener("click", function (e) {
    var chip = e.target.closest("[data-help-q]");
    if (chip) ask(chip.textContent);
  });
})();
