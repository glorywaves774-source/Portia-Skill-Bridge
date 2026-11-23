/* Runs synchronously in <head> so the page never flashes the wrong theme. */
(function () {
  var root = document.documentElement;
  var theme = null;
  try { theme = window.localStorage.getItem("psb-theme"); } catch (e) { /* storage blocked */ }
  if (theme !== "light" && theme !== "dark") {
    var match = document.cookie.match(/(?:^|; )psb-theme=(light|dark)/);
    theme = match ? match[1] : null;
  }
  if (theme !== "light" && theme !== "dark") {
    theme = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    root.setAttribute("data-theme-source", "system");
  }
  root.setAttribute("data-theme", theme);
  root.setAttribute("data-bs-theme", theme);
  var standalone = (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) ||
    window.navigator.standalone === true;
  if (standalone) root.classList.add("is-standalone");
})();
