/* Charts on the Reports page (Chart.js, bundled locally). Data comes from a JSON block, never inline script. */
(function () {
  "use strict";
  var dataEl = document.getElementById("report-data");
  if (!dataEl || typeof window.Chart === "undefined") { return; }
  var data;
  try { data = JSON.parse(dataEl.textContent); } catch (e) { return; }

  // Categorical slots in fixed order (validated for colour-blind separation in both modes).
  var PALETTE = {
    light: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"],
    dark: ["#3987e5", "#d95926", "#199e70", "#c98500"]
  };
  var charts = [];

  function mode() { return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light"; }
  function css(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }
  function reduceMotion() { return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches; }
  function compact(n) { return Math.abs(n) >= 10000 ? (n / 1000).toFixed(0) + "K" : String(n); }

  // Value labels at the tip of single-series bars, in text ink (never the series colour).
  var tipLabels = {
    id: "tipLabels",
    afterDatasetsDraw: function (chart, _args, opts) {
      if (!opts || !opts.enabled) { return; }
      var ctx = chart.ctx, horizontal = chart.options.indexAxis === "y";
      ctx.save();
      ctx.fillStyle = opts.color;
      ctx.font = "600 12px " + opts.font;
      chart.getDatasetMeta(0).data.forEach(function (bar, i) {
        var raw = chart.data.datasets[0].data[i];
        if (raw === null || raw === undefined) { return; }
        var text = opts.suffix ? raw + opts.suffix : compact(raw);
        if (horizontal) {
          ctx.textAlign = "left"; ctx.textBaseline = "middle";
          ctx.fillText(text, bar.x + 6, bar.y);
        } else {
          ctx.textAlign = "center"; ctx.textBaseline = "bottom";
          ctx.fillText(text, bar.x, bar.y - 4);
        }
      });
      ctx.restore();
    }
  };

  function base(theme) {
    var ink = css("--text", theme === "dark" ? "#E7EEF8" : "#0F172A");
    var muted = css("--text-muted", theme === "dark" ? "#A3B2C7" : "#51627A");
    var grid = theme === "dark" ? "rgba(255,255,255,0.09)" : "rgba(15,23,42,0.08)";
    var surface = theme === "dark" ? "#142039" : "#FFFFFF";
    var font = getComputedStyle(document.body).fontFamily;
    return { ink: ink, muted: muted, grid: grid, surface: surface, font: font, colors: PALETTE[theme] };
  }

  function common(t, opts) {
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: reduceMotion() ? false : { duration: 400 },
      layout: { padding: { top: 18, right: opts.rightPad || 8 } },
      interaction: opts.interaction || { mode: "nearest", intersect: true },
      plugins: {
        legend: opts.legend ? {
          display: true, position: "top", align: "start",
          labels: { color: t.ink, usePointStyle: true, pointStyle: opts.legendStyle || "rectRounded", boxWidth: 10,
                    boxHeight: 10, padding: 14, font: { family: t.font, size: 12 } }
        } : { display: false },
        tooltip: {
          backgroundColor: t.surface, titleColor: t.ink, bodyColor: t.ink, borderColor: t.grid, borderWidth: 1,
          padding: 10, cornerRadius: 10, boxPadding: 4, usePointStyle: true,
          titleFont: { family: t.font, weight: "600" }, bodyFont: { family: t.font },
          callbacks: opts.tooltip || {}
        },
        tipLabels: { enabled: !!opts.tipLabels, color: t.ink, font: t.font, suffix: opts.suffix || "" }
      },
      scales: opts.scales
    };
  }

  function axis(t, extra) {
    var a = { grid: { color: t.grid, lineWidth: 1, drawTicks: false }, border: { display: false },
              ticks: { color: t.muted, font: { family: t.font, size: 11 }, padding: 6 } };
    for (var k in extra) { a[k] = extra[k]; }
    return a;
  }
  function plainAxis(t) {
    return { grid: { display: false }, border: { color: t.grid },
             ticks: { color: t.ink, font: { family: t.font, size: 12 }, padding: 6, autoSkip: false } };
  }
  function bar(color) {
    return { backgroundColor: color, hoverBackgroundColor: color, borderWidth: 0, borderRadius: 4,
             borderSkipped: "start", maxBarThickness: 24, categoryPercentage: 0.8, barPercentage: 0.9 };
  }
  function allZero(values) { return !values.some(function (v) { return v; }); }
  function empty(canvas, text) {
    var box = canvas.parentNode;
    box.classList.add("chart-empty");
    box.innerHTML = "";
    var p = document.createElement("p");
    p.className = "muted";
    p.textContent = text;
    box.appendChild(p);
  }

  function make(id, config) {
    var el = document.getElementById(id);
    if (!el) { return; }
    charts.push(new window.Chart(el, config));
  }

  function draw() {
    charts.forEach(function (c) { c.destroy(); });
    charts = [];
    var t = base(mode());
    window.Chart.defaults.font.family = t.font;

    // Results chain: one series, horizontal bars, value at the tip.
    if (data.chain && document.getElementById("chart-chain")) {
      if (allZero(data.chain.values)) { empty(document.getElementById("chart-chain"), "No results in this period yet."); }
      else {
        make("chart-chain", {
          type: "bar", plugins: [tipLabels],
          data: { labels: data.chain.labels, datasets: [Object.assign({ label: "Learners", data: data.chain.values }, bar(t.colors[0]))] },
          options: Object.assign(common(t, { tipLabels: true, rightPad: 34, scales: {
            x: axis(t, { beginAtZero: true, ticks: { display: false }, grid: { display: false } }), y: plainAxis(t) } }),
            { indexAxis: "y" })
        });
      }
    }

    // Month by month: up to four lines, legend + shared tooltip (crosshair-style).
    if (data.trend && document.getElementById("chart-trend")) {
      var any = data.trend.series.some(function (s) { return !allZero(s.values); });
      if (!any) { empty(document.getElementById("chart-trend"), "Nothing recorded in this period yet."); }
      else {
        make("chart-trend", {
          type: "line",
          data: { labels: data.trend.labels, datasets: data.trend.series.map(function (s, i) {
            return { label: s.name, data: s.values, borderColor: t.colors[i], backgroundColor: t.colors[i],
                     borderWidth: 2, tension: 0, pointRadius: data.trend.labels.length > 14 ? 0 : 4,
                     pointHoverRadius: 6, pointBorderColor: t.surface, pointBorderWidth: 2,
                     borderCapStyle: "round", borderJoinStyle: "round" };
          }) },
          options: common(t, { legend: true, interaction: { mode: "index", intersect: false },
            scales: { x: axis(t, { grid: { display: false }, ticks: { color: t.muted, maxRotation: 0, autoSkipPadding: 12 } }),
                      y: axis(t, { beginAtZero: true, ticks: { color: t.muted, precision: 0, callback: compact } }) } })
        });
      }
    }

    // Female and male side by side for each step.
    if (data.gender && document.getElementById("chart-gender")) {
      var anyG = data.gender.series.some(function (s) { return !allZero(s.values); });
      if (!anyG) { empty(document.getElementById("chart-gender"), "No results in this period yet."); }
      else {
        // On narrow screens the steps run down the side so their names stay level and readable.
        var narrow = document.getElementById("chart-gender").parentNode.clientWidth < 560;
        var valueAxis = axis(t, { beginAtZero: true, ticks: { color: t.muted, precision: 0, callback: compact } });
        var g = common(t, { legend: true, interaction: { mode: "index", intersect: false },
          scales: narrow ? { x: valueAxis, y: plainAxis(t) } : { x: plainAxis(t), y: valueAxis } });
        if (narrow) { g.indexAxis = "y"; }
        make("chart-gender", {
          type: "bar",
          data: { labels: data.gender.labels, datasets: data.gender.series.map(function (s, i) {
            return Object.assign({ label: s.name, data: s.values }, bar(t.colors[i]));
          }) },
          options: g
        });
      }
    }

    // Type of work: one series, horizontal.
    if (data.placements && data.placements.values.length && document.getElementById("chart-placements")) {
      make("chart-placements", {
        type: "bar", plugins: [tipLabels],
        data: { labels: data.placements.labels, datasets: [Object.assign({ label: "Placements", data: data.placements.values }, bar(t.colors[0]))] },
        options: Object.assign(common(t, { tipLabels: true, rightPad: 30, scales: {
          x: axis(t, { beginAtZero: true, ticks: { display: false }, grid: { display: false } }), y: plainAxis(t) } }),
          { indexAxis: "y" })
      });
    }

    // Still in work at 3, 6 and 12 months: one series of percentages.
    if (data.retention && document.getElementById("chart-retention")) {
      make("chart-retention", {
        type: "bar", plugins: [tipLabels],
        data: { labels: data.retention.labels, datasets: [Object.assign({ label: "Still working", data: data.retention.values }, bar(t.colors[0]))] },
        options: common(t, { tipLabels: true, suffix: "%",
          tooltip: { label: function (item) {
            var v = item.raw === null ? "no checks" : item.raw + "%";
            return " " + v + " (" + data.retention.notes[item.dataIndex] + ")";
          } },
          scales: { x: plainAxis(t), y: axis(t, { min: 0, max: 100, ticks: { color: t.muted, stepSize: 25, callback: function (v) { return v + "%"; } } }) } })
      });
    }
  }

  draw();
  // Redraw with the right colours when the person switches between light and dark.
  new MutationObserver(function (list) {
    if (list.some(function (m) { return m.attributeName === "data-theme"; })) { draw(); }
  }).observe(document.documentElement, { attributes: true });
  // Re-draw if the screen crosses the narrow/wide point (the gender chart changes direction).
  var wasNarrow = window.innerWidth < 760, timer = null;
  window.addEventListener("resize", function () {
    clearTimeout(timer);
    timer = setTimeout(function () {
      var nowNarrow = window.innerWidth < 760;
      if (nowNarrow !== wasNarrow) { wasNarrow = nowNarrow; draw(); }
    }, 200);
  });
  window.addEventListener("beforeprint", function () { charts.forEach(function (c) { c.resize(); }); });
})();
