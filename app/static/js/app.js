/* PORTIA SkillBridge — shared page behaviour (theme, phone menu, nav filter). No inline scripts (strict CSP). */
(function () {
  "use strict";
  var root = document.documentElement;

  /* ---------- Light / dark mode ---------- */
  function currentTheme() { return root.getAttribute("data-theme") === "dark" ? "dark" : "light"; }

  function applyTheme(theme, remember) {
    root.setAttribute("data-theme", theme);
    root.setAttribute("data-bs-theme", theme);
    if (remember) {
      root.removeAttribute("data-theme-source");
      try { window.localStorage.setItem("psb-theme", theme); } catch (e) { /* ignore */ }
      document.cookie = "psb-theme=" + theme + "; path=/; max-age=31536000; SameSite=Lax" +
        (location.protocol === "https:" ? "; Secure" : "");
    }
    syncToggles();
  }

  function syncToggles() {
    var dark = currentTheme() === "dark";
    document.querySelectorAll("[data-theme-toggle]").forEach(function (btn) {
      var label = dark ? "Light mode" : "Dark mode";
      btn.setAttribute("aria-label", "Switch to " + label.toLowerCase());
      btn.setAttribute("title", label);
      var text = btn.querySelector("[data-theme-label]");
      if (text) text.textContent = label;
    });
  }

  document.addEventListener("click", function (event) {
    var btn = event.target.closest("[data-theme-toggle]");
    if (!btn) return;
    applyTheme(currentTheme() === "dark" ? "light" : "dark", true);
  });

  if (window.matchMedia) {
    var mq = window.matchMedia("(prefers-color-scheme: dark)");
    var onSystemChange = function (e) {
      if (root.getAttribute("data-theme-source") === "system") applyTheme(e.matches ? "dark" : "light", false);
    };
    if (mq.addEventListener) mq.addEventListener("change", onSystemChange);
  }
  syncToggles();

  /* ---------- Phone menu (slides in from the left over a dimmed page) ---------- */
  var drawer = document.querySelector("[data-drawer]");
  var scrim = document.querySelector("[data-drawer-scrim]");
  var pendingHref = null;

  function isOpen() { return drawer && drawer.classList.contains("is-open"); }

  function openDrawer() {
    if (!drawer || isOpen()) return;
    drawer.classList.add("is-open");
    if (scrim) scrim.classList.add("is-visible");
    document.body.classList.add("drawer-open");
    drawer.setAttribute("aria-hidden", "false");
    document.querySelectorAll("[data-drawer-open]").forEach(function (b) { b.setAttribute("aria-expanded", "true"); });
    // A history entry lets the Android Back button close the menu.
    history.pushState({ psbDrawer: true }, "");
    var focusTarget = drawer.querySelector("[data-drawer-close]");
    if (focusTarget) focusTarget.focus({ preventScroll: true });
  }

  function hideDrawer() {
    if (!drawer) return;
    drawer.classList.remove("is-open");
    if (scrim) scrim.classList.remove("is-visible");
    document.body.classList.remove("drawer-open");
    if (isMobile()) drawer.setAttribute("aria-hidden", "true");
    document.querySelectorAll("[data-drawer-open]").forEach(function (b) { b.setAttribute("aria-expanded", "false"); });
  }

  function closeDrawer() {
    if (!isOpen()) return;
    if (history.state && history.state.psbDrawer) history.back(); // popstate hides it
    else hideDrawer();
  }

  function isMobile() { return window.matchMedia("(max-width: 760px)").matches; }

  window.addEventListener("popstate", function () {
    if (isOpen()) hideDrawer();
    if (pendingHref) {
      var href = pendingHref;
      pendingHref = null;
      window.location.assign(href); // no extra Back step left behind
    }
  });

  document.addEventListener("click", function (event) {
    if (event.target.closest("[data-drawer-open]")) { event.preventDefault(); openDrawer(); return; }
    if (event.target.closest("[data-drawer-close]") || event.target.closest("[data-drawer-scrim]")) {
      event.preventDefault(); closeDrawer(); return;
    }
    var link = event.target.closest("[data-drawer] a[href]");
    if (link && isOpen() && isMobile() && !event.metaKey && !event.ctrlKey && !event.shiftKey) {
      event.preventDefault();
      if (history.state && history.state.psbDrawer) {
        pendingHref = link.href;
        history.back();
      } else {
        hideDrawer();
        window.location.assign(link.href);
      }
    }
    if (event.target.closest("[data-app-back]")) { event.preventDefault(); history.back(); }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && isOpen()) closeDrawer();
  });

  if (drawer) {
    var syncAria = function () {
      if (isMobile()) { if (!isOpen()) drawer.setAttribute("aria-hidden", "true"); }
      else { drawer.removeAttribute("aria-hidden"); hideDrawer(); }
    };
    syncAria();
    window.addEventListener("resize", syncAria);
  }

  /* ---------- Keyboard open: hide the tab bar while typing ---------- */
  var typingSel = "input:not([type=checkbox]):not([type=radio]):not([type=submit]):not([type=button]), textarea, select";
  document.addEventListener("focusin", function (e) {
    if (e.target.matches && e.target.matches(typingSel) && !e.target.closest("[data-drawer]")) document.body.classList.add("is-typing");
  });
  document.addEventListener("focusout", function () {
    setTimeout(function () {
      var a = document.activeElement;
      if (!(a && a.matches && a.matches(typingSel))) document.body.classList.remove("is-typing");
    }, 50);
  });

  /* ---------- Find a page (accent-insensitive menu filter) ---------- */
  function fold(text) {
    return (text || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().trim();
  }
  document.querySelectorAll("[data-nav-filter]").forEach(function (input) {
    var list = document.querySelector(input.getAttribute("data-nav-filter"));
    if (!list) return;
    var empty = list.querySelector("[data-nav-empty]");
    input.addEventListener("input", function () {
      var q = fold(input.value), shown = 0;
      list.querySelectorAll("[data-nav-item]").forEach(function (item) {
        var hit = !q || fold(item.textContent).indexOf(q) !== -1;
        item.hidden = !hit;
        if (hit) shown++;
      });
      if (empty) empty.hidden = shown !== 0;
    });
  });
})();

/* Upload a chosen photo straight away (forms marked with .file-auto inputs). */
document.addEventListener("change", function (e) {
  if (e.target.matches && e.target.matches("input[type=file].file-auto") && e.target.files.length) {
    e.target.form.submit();
  }
});

/* Small helpers used by registers and lists (no inline scripts). */
document.addEventListener("click", function (e) {
  var btn = e.target.closest && e.target.closest("[data-mark-all]");
  if (btn) {
    var value = btn.getAttribute("data-mark-all");
    btn.closest("form").querySelectorAll('input[type=radio][value="' + value + '"]').forEach(function (r) { r.checked = true; });
  }
});
document.addEventListener("change", function (e) {
  var t = e.target;
  if (t.matches && t.matches("[data-check-all]")) {
    t.closest("form").querySelectorAll('input[type=checkbox][name="' + t.getAttribute("data-check-all") + '"]')
      .forEach(function (c) { c.checked = t.checked; });
  }
  if (t.matches && t.matches("select.auto-submit")) t.form.submit();
});

/* Exam timer: counts down to the server's deadline and submits the answers when time is up. */
(function () {
  var box = document.querySelector("[data-deadline]");
  if (!box) return;
  var deadline = new Date(box.getAttribute("data-deadline")).getTime();
  var text = box.querySelector("[data-timer-text]");
  var form = document.getElementById(box.getAttribute("data-form"));
  var sent = false;
  function tick() {
    var left = Math.max(0, Math.floor((deadline - Date.now()) / 1000));
    var m = Math.floor(left / 60), s = left % 60;
    text.textContent = "Time left: " + m + ":" + (s < 10 ? "0" : "") + s;
    box.classList.toggle("is-low", left <= 60);
    if (left === 0 && !sent && form) { sent = true; text.textContent = "Time is up — submitting…"; form.submit(); return; }
    setTimeout(tick, 1000);
  }
  tick();
})();

/* ---- Installable app (PWA): service worker and the "Install app" button ---- */
(function () {
  "use strict";
  var secure = location.protocol === "https:" || location.hostname === "localhost" || location.hostname === "127.0.0.1";
  if ("serviceWorker" in navigator && secure) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function () { /* the site works without it */ });
    });
  }
  var standalone = (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) ||
    window.navigator.standalone === true;
  if (standalone) { return; }  // already installed
  var deferred = null;
  function show(selector, on) {
    document.querySelectorAll(selector).forEach(function (el) { el.hidden = !on; });
  }
  window.addEventListener("beforeinstallprompt", function (e) {
    e.preventDefault();          // show our own button instead of the browser's mini bar
    deferred = e;
    show("[data-install-app]", true);
  });
  window.addEventListener("appinstalled", function () {
    deferred = null;
    show("[data-install-app]", false);
  });
  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest("[data-install-app]");
    if (!btn || !deferred) { return; }
    deferred.prompt();
    deferred.userChoice.finally(function () { deferred = null; show("[data-install-app]", false); });
  });
  // iPhone and iPad Safari have no install prompt: point to the instructions instead.
  var ua = navigator.userAgent || "";
  var ios = /iPhone|iPad|iPod/.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  if (ios && !/CriOS|FxiOS|EdgiOS/.test(ua)) { show("[data-install-ios]", true); }
})();
