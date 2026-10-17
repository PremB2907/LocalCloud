/* =============================================================
   LocalCloud Console — Premium Frontend
   Vanilla JS, no framework, all existing backend routes preserved
   ============================================================= */

"use strict";

/* ---- Constants ---- */
const serviceOrder = ["s3", "dynamodb", "sqs", "sns", "lambda", "ec2", "cloudwatch", "api_gateway"];
const serviceTitles = {
  overview:    "Overview",
  s3:          "S3 Storage",
  dynamodb:    "DynamoDB",
  sqs:         "SQS Queues",
  sns:         "SNS Topics",
  lambda:      "Lambda Functions",
  ec2:         "EC2 Instances",
  cloudwatch:  "CloudWatch",
  api_gateway: "API Gateway",
};
const serviceBreadcrumbs = {
  overview:    ["Overview"],
  s3:          ["Storage", "S3"],
  dynamodb:    ["Database", "DynamoDB"],
  sqs:         ["App Integration", "SQS"],
  sns:         ["App Integration", "SNS"],
  lambda:      ["Compute", "Lambda"],
  ec2:         ["Compute", "EC2"],
  cloudwatch:  ["Observability", "CloudWatch"],
  api_gateway: ["App Integration", "API Gateway"],
};
const serviceIcons = {
  overview:    "icon-overview",
  s3:          "icon-s3",
  dynamodb:    "icon-dynamodb",
  sqs:         "icon-sqs",
  sns:         "icon-sns",
  lambda:      "icon-lambda",
  ec2:         "icon-ec2",
  cloudwatch:  "icon-cloudwatch",
  api_gateway: "icon-api_gateway",
};

/* ---- State ---- */
let currentView = "overview";
let catalog = {};
const receiptHandles = new Map();
let metricGraphState = null;

/* ---- DOM refs ---- */
const pageContent   = document.getElementById("page-content");
const connPill      = document.getElementById("connection-pill");
const connText      = document.getElementById("connection-text");
const sidebarVer    = document.getElementById("sidebar-version");
const breadcrumb    = document.getElementById("breadcrumb-current");

/* =============================================================
   THEME MANAGEMENT
   ============================================================= */

function getTheme() {
  return document.documentElement.dataset.theme || "dark";
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("lc_theme", theme);
  const use = document.getElementById("theme-icon")?.querySelector("use");
  if (use) use.setAttribute("href", theme === "dark" ? "#icon-sun" : "#icon-moon");
  const btn = document.getElementById("theme-toggle");
  if (btn) btn.setAttribute("aria-label", theme === "dark" ? "Switch to light theme" : "Switch to dark theme");
}

function initTheme() {
  // Already applied by inline script to avoid flash; just sync icon
  applyTheme(getTheme());
  document.getElementById("theme-toggle")?.addEventListener("click", () => {
    applyTheme(getTheme() === "dark" ? "light" : "dark");
  });
}

/* =============================================================
   SECURITY HELPERS
   ============================================================= */

function escapeHtml(value) {
  return String(value ?? "-").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }
  )[c]);
}

/** Safe DOM text node — use instead of innerHTML for user-controlled data */
function txt(str) {
  return document.createTextNode(String(str ?? ""));
}

/** Create element with optional attrs and children */
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("data-")) node.dataset[k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = v;
    else node.setAttribute(k, v);
  }
  for (const child of children.flat()) {
    if (child == null) continue;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

/** Build an SVG <use> icon */
function svgIcon(id, cls = "") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  if (cls) svg.setAttribute("class", cls);
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#${id}`);
  svg.appendChild(use);
  return svg;
}

/* =============================================================
   API HELPERS
   ============================================================= */

async function requestJson(path) {
  const r = await fetch(path, { headers: { Accept: "application/json" } });
  const payload = await r.json().catch(() => ({}));
  if (!r.ok) {
    const err = new Error(payload.error?.message || "Request failed.");
    err.code = payload.error?.code || `HTTP ${r.status}`;
    throw err;
  }
  return payload;
}

async function postJson(path, value) {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(value),
  });
  const payload = await r.json().catch(() => ({}));
  if (!r.ok) {
    const err = new Error(payload.error?.message || "The operation failed.");
    err.code = payload.error?.code || `HTTP ${r.status}`;
    throw err;
  }
  return payload;
}

/* =============================================================
   TOAST NOTIFICATIONS
   ============================================================= */

let _toastTimer = null;

function toast(message, isError = false) {
  const region = document.getElementById("toast-region");
  if (!region) return;
  // Clear existing
  if (_toastTimer) { clearTimeout(_toastTimer); region.innerHTML = ""; }

  const iconId = isError ? "icon-alert-circle" : "icon-check-circle";
  const wrapper = el("div", { class: `toast-message${isError ? " is-error" : ""}` },
    svgIcon(iconId, "toast-icon"),
    el("span", {}, message)
  );
  region.appendChild(wrapper);
  _toastTimer = setTimeout(() => {
    wrapper.style.opacity = "0";
    wrapper.style.transition = "opacity 300ms ease";
    setTimeout(() => { if (region.contains(wrapper)) region.removeChild(wrapper); }, 320);
  }, 4000);
}

/* =============================================================
   CONFIRM DIALOG
   ============================================================= */

function confirmExact(title, description, expected, actionLabel = "Confirm") {
  return new Promise((resolve) => {
    const dialog = document.getElementById("confirm-dialog");
    const form   = document.getElementById("confirm-form");
    const input  = document.getElementById("confirm-input");
    const submit = document.getElementById("confirm-submit");
    const cancel = document.getElementById("confirm-cancel");
    const titleEl = document.getElementById("confirm-title");
    const descEl  = document.getElementById("confirm-description");
    const labelEl = document.getElementById("confirm-label");

    const finish = (accepted) => {
      form.onsubmit = null;
      input.oninput = null;
      cancel.onclick = null;
      dialog.oncancel = null;
      dialog.close();
      resolve(accepted);
    };

    titleEl.textContent = title;
    descEl.textContent  = description;
    labelEl.textContent = `Type exactly: ${expected}`;
    input.value         = "";
    submit.textContent  = actionLabel;
    submit.disabled     = true;

    input.oninput  = () => { submit.disabled = input.value !== expected; };
    form.onsubmit  = (e) => { e.preventDefault(); if (input.value === expected) finish(true); };
    cancel.onclick = () => finish(false);
    dialog.oncancel = (e) => { e.preventDefault(); finish(false); };

    dialog.showModal();
    input.focus();
  });
}

/* =============================================================
   BADGE BUILDER
   ============================================================= */

function badgeEl(text, kind = "") {
  const classes = ["badge"];
  if (kind) classes.push(`badge-${kind}`);
  return el("span", { class: classes.join(" ") }, text);
}

/* Legacy HTML badge — kept for innerHTML sections that are template-driven */
function badge(text, kind = "") {
  return `<span class="badge${kind ? ` badge-${kind}` : ""}">${escapeHtml(text)}</span>`;
}

/* =============================================================
   TABLE BUILDER (DOM-safe)
   ============================================================= */

/**
 * Build a safe DOM table.
 * columns: Array of [key, label, isMono]
 * rows: Array of objects
 */
function buildTable(columns, rows) {
  const thead = el("thead", {},
    el("tr", {}, ...columns.map(([, label]) => el("th", {}, label)))
  );
  const tbody = el("tbody", {});
  for (const row of rows) {
    const tr = el("tr", {});
    for (const [key, , isMono] of columns) {
      let value = row[key];
      if (Array.isArray(value)) {
        value = value.map((v) => (typeof v === "object" ? Object.values(v).join(": ") : String(v))).join(", ");
      } else if (value && typeof value === "object") {
        value = JSON.stringify(value);
      } else {
        value = value ?? "-";
      }
      const td = el("td", { class: isMono ? "mono" : "" }, String(value));
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
  return el("table", {}, thead, tbody);
}

function buildPanel(title, meta, content) {
  return el("section", { class: "panel" },
    el("div", { class: "panel-heading" },
      el("h3", {}, title),
      meta ? el("span", { class: "panel-meta" }, meta) : null,
    ),
    content
  );
}

function buildTablePanel(title, rows, columns, error = null) {
  if (error) {
    return buildPanel(title, null,
      el("div", { class: "error-band" }, error)
    );
  }
  if (!rows.length) {
    return buildPanel(title, "Live query",
      el("div", { class: "empty-state" },
        svgIcon("icon-overview", ""),
        el("strong", {}, `No ${title.toLowerCase()} found`),
        el("span", {}, "This is an empty result, not an unsupported-service response.")
      )
    );
  }
  return buildPanel(title, `${rows.length} item${rows.length !== 1 ? "s" : ""}`,
    el("div", { class: "table-scroll" },
      buildTable(columns, rows)
    )
  );
}

/* =============================================================
   SEARCH / FILTER
   ============================================================= */

let filterInput = null;

/**
 * Apply the client-side filter to every table row currently rendered.
 * Only rows inside a `.table-scroll` container are filtered, so panels that
 * show summaries, action buttons, or details are never hidden.
 */
function applyListingFilter() {
  const content = document.getElementById("page-content");
  if (!content) return;
  const query = (filterInput && filterInput.value ? filterInput.value : "").trim().toLowerCase();
  content.querySelectorAll("tbody tr").forEach((row) => {
    const insideScroll = row.closest(".table-scroll") !== null;
    const text = (row.textContent || "").toLowerCase();
    const hidden = insideScroll && query.length > 0 && !text.includes(query);
    row.classList.toggle("is-filtered-out", hidden);
    row.hidden = hidden;
  });
  let empty = content.querySelector(".list-filter-empty");
  if (empty) empty.remove();
  if (query.length > 0) {
    const visible = content.querySelectorAll("tbody tr:not([hidden])");
    if (visible.length === 0) {
      empty = el("div", { class: "list-filter-empty empty-state", role: "status", "aria-live": "polite" },
        svgIcon("icon-search", ""),
        el("strong", {}, "No matching resources"),
        el("span", {}, "Adjust the filter to narrow this listing.")
      );
      content.appendChild(empty);
    }
  }
}

/**
 * Render a search field above the first listing and wire it to the current
 * page. Re-invoked after every render so the field always reflects the
 * current page.
 */
function renderListingFilter() {
  const content = document.getElementById("page-content");
  if (!content) return;
  let field = content.querySelector(".service-search-field");
  if (field) field.remove();
  const input = el("input", {
    class: "service-search-input",
    type: "search",
    "aria-label": "Filter the displayed resources",
    placeholder: "Filter displayed resources…",
    autocomplete: "off",
    spellcheck: false,
  });
  field = el("div", { class: "service-search-field", role: "search" },
    el("label", { for: "service-search-input" }, "Filter"),
    input
  );
  content.insertBefore(field, content.firstChild);
  filterInput = input;
  input.addEventListener("input", applyListingFilter);
  applyListingFilter();
}

/* =============================================================
   CLOUDWATCH EC2 MONITORING GRAPH
   ============================================================= */

const STAT_KEY = {
  Average:    "average",
  Sum:        "sum",
  Minimum:    "minimum",
  Maximum:    "maximum",
  SampleCount:"sample_count",
};

function presetTimeRange(range) {
  const end = new Date();
  const start = new Date();
  const minutes = {"5m": 5, "15m": 15, "1h": 60, "3h": 180, "6h": 360, "12h": 720, "24h": 1440}[range] || 60;
  start.setMinutes(start.getMinutes() - minutes);
  const iso = (d) => d.toISOString().replace(/\.\d{3}Z$/, "Z");
  return { start: iso(start), end: iso(end) };
}

function formatMetricTimestamp(value) {
  if (!value) return "-";
  return new Date(value).toLocaleString(undefined, { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

/**
 * Draw a line graph of one statistic across the datapoints on a canvas.
 */
function drawMetricGraph(canvas, datapoints, statistic, unit) {
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const displayWidth = canvas.clientWidth || 700;
  const displayHeight = canvas.clientHeight || 300;
  canvas.width = displayWidth * dpr;
  canvas.height = displayHeight * dpr;
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, displayWidth, displayHeight);

  const statKey = STAT_KEY[statistic] || "average";
  const points = datapoints.filter((d) => typeof d[statKey] === "number");
  if (!points.length) {
    ctx.fillStyle = "#6b7280";
    ctx.font = "14px var(--font-sans)";
    ctx.textAlign = "center";
    ctx.fillText("No datapoints to graph", displayWidth / 2, displayHeight / 2);
    return;
  }

  const pad = { left: 56, right: 22, top: 24, bottom: 42 };
  const plotW = displayWidth - pad.left - pad.right;
  const plotH = displayHeight - pad.top - pad.bottom;
  const values = points.map((d) => d[statKey]);
  const vMin = Math.min(...values);
  const vMax = Math.max(...values);
  const vRange = vMax === vMin ? 1 : vMax - vMin;
  const padV = vRange * 0.1;
  const scaleV = (v) => pad.top + plotH - ((v - (vMin - padV)) / (vRange + 2 * padV)) * plotH;
  const scaleX = (i) => pad.left + (i / (points.length - 1 || 1)) * plotW;

  // Axes
  ctx.strokeStyle = "#9ca3af";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(pad.left, pad.top);
  ctx.lineTo(pad.left, pad.top + plotH);
  ctx.lineTo(pad.left + plotW, pad.top + plotH);
  ctx.stroke();

  // Y ticks
  const ticks = 5;
  ctx.fillStyle = "#6b7280";
  ctx.font = "11px var(--font-sans)";
  ctx.textAlign = "right";
  for (let t = 0; t <= ticks; t++) {
    const v = vMin - padV + (vRange + 2 * padV) * (t / ticks);
    const y = scaleV(v);
    ctx.fillText(v.toFixed(2), pad.left - 8, y + 4);
    ctx.beginPath();
    ctx.moveTo(pad.left - 4, y);
    ctx.lineTo(pad.left, y);
    ctx.stroke();
  }
  ctx.fillStyle = "#6b7280";
  ctx.font = "bold 11px var(--font-sans)";
  ctx.textAlign = "right";
  ctx.fillText(unit || "", pad.left - 8, pad.top - 8);

  // X ticks
  ctx.textAlign = "center";
  const xTicks = Math.min(points.length, 6);
  for (let t = 0; t < xTicks; t++) {
    const i = Math.floor((t / (xTicks - 1 || 1)) * (points.length - 1));
    const x = scaleX(i);
    ctx.fillText(formatMetricTimestamp(points[i].timestamp), x, pad.top + plotH + 18);
    ctx.beginPath();
    ctx.moveTo(x, pad.top + plotH);
    ctx.lineTo(x, pad.top + plotH + 4);
    ctx.stroke();
  }

  // Line + points
  ctx.strokeStyle = "#2563eb";
  ctx.lineWidth = 2;
  ctx.beginPath();
  points.forEach((d, i) => {
    const x = scaleX(i);
    const y = scaleV(d[statKey]);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
  ctx.fillStyle = "#2563eb";
  points.forEach((d, i) => {
    ctx.beginPath();
    ctx.arc(scaleX(i), scaleV(d[statKey]), 3, 0, 2 * Math.PI);
    ctx.fill();
  });

  // Latest value annotation
  const last = points[points.length - 1];
  ctx.fillStyle = "#2563eb";
  ctx.font = "bold 13px var(--font-sans)";
  const label = `${last[statKey].toFixed(2)}${unit ? " " + unit : ""}`;
  ctx.fillText(label, scaleX(points.length - 1) + 8, scaleV(last[statKey]));
}

/**
 * Render the metric graph output (canvas + datapoint table) into a container.
 */
function renderMetricGraphOutput(container, datapoints, statistic, unit) {
  container.innerHTML = "";
  const head = el("div", { class: "section-heading" },
    el("h2", {}, "Metric graph"),
    el("span", { class: "section-meta" },
      `${escapeHtml(statistic)} ${escapeHtml(unit || "–")} · ${datapoints.length} datapoint${datapoints.length !== 1 ? "s" : ""}`
    )
  );
  container.appendChild(head);
  const wrap = el("div", { class: "cw-graph-wrap" },
    el("canvas", { class: "cw-graph", width: "700", height: "300", role: "img",
      "aria-label": `${escapeHtml(statistic)} metric graph, ${datapoints.length} datapoints` })
  );
  container.appendChild(wrap);
  drawMetricGraph(wrap.querySelector("canvas"), datapoints, statistic, unit);
  if (datapoints.length) {
    const rows = datapoints.map((d) =>
      el("tr", {},
        el("td", { class: "mono" }, d.timestamp),
        el("td", { class: "mono" }, (d[STAT_KEY[statistic] || "average"] ?? "-").toString()),
        el("td", {}, d.unit || "-")
      )
    );
    container.appendChild(buildTablePanel("Datapoints", rows,
      [["timestamp", "Timestamp"], ["value", "Value"], ["unit", "Unit"]]));
  }
}

/**
 * Fetch metric statistics for a stored query state and render the graph.
 */
async function fetchMetricStatistics(state) {
  const dimensions = JSON.stringify([{ name: "InstanceId", value: state.instanceId }]);
  const query = new URLSearchParams({
    namespace: state.namespace || "AWS/EC2",
    metric_name: state.metricName,
    dimensions,
    start_time: state.start,
    end_time: state.end,
    period: state.period || "300",
    statistic: state.statistic || "Average",
  });
  if (state.unit) query.set("unit", state.unit);
  const output = document.getElementById("cw-graph-output");
  output.innerHTML = `<div class="initial-state"><span>Loading metric data…</span></div>`;
  const result = await requestJson(`/api/services/cloudwatch/metric-statistics?${query}`).catch((error) => {
    output.innerHTML = `<div class="error-band">${escapeHtml(error.code || "Error")}: ${escapeHtml(error.message)}</div>`;
    return null;
  });
  if (!result) return;
  renderMetricGraphOutput(output, result.datapoints || [], state.statistic || "Average", state.unit || "");
}

/**
 * Fetch the EC2 metrics collector status and render it into the status div.
 */
async function fetchEc2MetricsStatus() {
  const output = document.getElementById("ec2-metrics-status");
  if (!output) return;
  try {
    const result = await requestJson("/api/services/ec2/metrics/collector/status");
    if (!result.ok) {
      output.innerHTML = `<div class="error-band">Failed to load collector status.</div>`;
      return;
    }
    const c = result.collector;
    const runningBadge = c.running
      ? `<span class="badge badge-success">● running</span>`
      : `<span class="badge badge-danger">● stopped</span>`;
    const errorHtml = c.last_error
      ? `<div class="error-band">${escapeHtml(c.last_error)}</div>`
      : "";
    output.innerHTML = `
      <div class="action-form">
        <label>Status</label>
        <span class="mono">${runningBadge}</span>
        <label>Collections</label>
        <span class="mono">${c.collections}</span>
        <label>Instances collected</label>
        <span class="mono">${c.instances_collected}</span>
        <label>Interval (s)</label>
        <span class="mono">${c.interval_seconds}</span>
        <label>Max instances</label>
        <span class="mono">${c.max_instances}</span>
        <label>Namespace</label>
        <span class="mono">${escapeHtml(c.namespace)}</span>
        ${errorHtml}
      </div>
    `;
  } catch (error) {
    output.innerHTML = `<div class="error-band">${escapeHtml(error.code || "Error")}: ${escapeHtml(error.message)}</div>`;
  }
}

/* =============================================================
   CONNECTION / HEALTH
   ============================================================= */

function updateConnection(health) {
  connPill.classList.remove("is-checking", "is-connected", "is-disconnected");
  if (health.connected) {
    connPill.classList.add("is-connected");
    connText.textContent = `Floci ${health.version || "connected"}`;
    sidebarVer.textContent = `Floci ${health.version || ""} · ${health.region || "local"}`;
  } else {
    connPill.classList.add("is-disconnected");
    connText.textContent = "Disconnected";
    sidebarVer.textContent = "Endpoint unavailable";
  }
}

async function refreshHealth() {
  try { updateConnection(await requestJson("/api/health")); }
  catch { updateConnection({ connected: false }); }
}

/* =============================================================
   SERVICE COUNT HELPER
   ============================================================= */

function serviceCount(key, result) {
  const d = result.data;
  if (key === "s3")         return d.buckets.length;
  if (key === "dynamodb")   return d.tables.length;
  if (key === "sqs")        return d.queues.length;
  if (key === "sns")        return d.topics.length;
  if (key === "lambda")     return d.functions.length;
  if (key === "ec2")        return d.instances.length;
  if (key === "cloudwatch") {
    if (!d.metrics.ok || !d.log_groups.ok) return null;
    return d.metrics.metrics.length + d.log_groups.log_groups.length;
  }
  if (!d.rest.ok || !d.http.ok) return null;
  return d.rest.apis.length + d.http.apis.length;
}

/* =============================================================
   OVERVIEW RENDER
   ============================================================= */

function validationSummary(service) {
  const ops = service.operations || [];
  const passed = ops.filter((o) => o.validation === "passed").length;
  return passed === ops.length ? "All operations validated" : `${passed}/${ops.length} validated`;
}

async function renderOverview() {
  // Show skeleton while loading
  pageContent.innerHTML = `
    <div class="initial-state" aria-live="polite">
      <div class="skeleton-grid">
        ${Array(8).fill('<div class="skeleton-card"></div>').join("")}
      </div>
      <span>Loading live Floci inventory…</span>
    </div>`;

  const [healthResult, catalogResult, operationResult] = await Promise.allSettled([
    requestJson("/api/health"),
    requestJson("/api/catalog"),
    requestJson("/api/operations"),
  ]);
  if (healthResult.status === "fulfilled") updateConnection(healthResult.value);
  else updateConnection({ connected: false });
  if (catalogResult.status === "fulfilled") catalog = catalogResult.value;

  const inventoryResults = await Promise.allSettled(
    serviceOrder.map((key) => requestJson(`/api/services/${key}`))
  );

  const connected = healthResult.status === "fulfilled" && healthResult.value.connected;

  // Clear and build DOM
  pageContent.innerHTML = "";

  // --- Health banner ---
  const health = healthResult.status === "fulfilled" ? healthResult.value : {};
  const banner = el("div", { class: `health-banner${connected ? "" : " is-down"}`, "aria-live": "polite" },
    el("div", { class: "health-banner-content" },
      el("div", { class: "health-banner-title" }, connected ? "Floci endpoint reachable" : "Floci endpoint unavailable"),
      el("div", { class: "health-banner-detail" },
        connected
          ? "Connected to the configured local emulator. All inventory counts are live."
          : "The dashboard is available but service requests will show individual failures."
      )
    ),
    el("div", { class: "health-banner-meta" },
      `http://127.0.0.1:4566${connected && health.version ? ` · v${health.version}` : ""}`
    )
  );
  pageContent.appendChild(banner);

  // --- Section: Resource counts ---
  const secHead1 = el("div", { class: "section-heading" },
    el("h2", {}, "Service Inventory"),
    el("span", { class: "section-meta" }, "Live read-only queries · empty = no resources, not unsupported")
  );
  pageContent.appendChild(secHead1);

  const grid = el("div", { class: "service-grid", role: "list" });
  serviceOrder.forEach((key, i) => {
    const svc = catalog[key] || { name: serviceTitles[key], group: "" };
    const result = inventoryResults[i];
    const count  = result.status === "fulfilled" ? serviceCount(key, result.value) : null;
    const ok     = result.status === "fulfilled" && result.value.ok && count !== null;

    const card = el("a", {
      class: "service-card",
      href: `#${key}`,
      "data-view": key,
      role: "listitem",
      "aria-label": `${svc.name}: ${count ?? "unavailable"} resources`,
    },
      el("div", { class: "service-card-header" },
        el("div", { class: "service-card-icon", "aria-hidden": "true" },
          svgIcon(serviceIcons[key] || "icon-overview")
        ),
        badgeEl(ok ? "Live" : "Unavailable", ok ? "success" : "error")
      ),
      el("div", { class: `service-count${count === null ? " is-error" : ""}` },
        count === null ? "–" : String(count)
      ),
      el("div", { class: "service-card-name" }, svc.name),
      el("div", { class: "service-count-label" },
        count === null ? "Inventory unavailable" : `resource${count !== 1 ? "s" : ""} in Floci`
      )
    );
    grid.appendChild(card);
  });
  pageContent.appendChild(grid);

  // --- Section: Validation evidence ---
  const secHead2 = el("div", { class: "section-heading" },
    el("h2", {}, "Validation Evidence"),
    el("span", { class: "section-meta" }, "Per-service operation-level testing status")
  );
  pageContent.appendChild(secHead2);

  const validGrid = el("div", { class: "validation-grid" });
  serviceOrder.forEach((key) => {
    const svc = catalog[key] || { name: serviceTitles[key], operations: [] };
    const row = el("div", { class: "validation-row" },
      el("span", { class: "validation-name" }, svc.name),
      el("span", { class: "validation-state" }, validationSummary(svc))
    );
    validGrid.appendChild(row);
  });
  pageContent.appendChild(validGrid);

  // --- Section: Recent operations ---
  const ops = operationResult.status === "fulfilled" ? operationResult.value : [];
  const opsSection = el("div", { class: "section-heading" },
    el("h2", {}, "Recent Operations"),
    el("span", { class: "section-meta" }, "Session audit log · 100 entries max")
  );
  pageContent.appendChild(opsSection);

  let opsPanel;
  if (!ops.length) {
    opsPanel = buildPanel("Operations Log", "Session activity only",
      el("div", { class: "empty-state" },
        svgIcon("icon-ops"),
        el("strong", {}, "No operations yet"),
        el("span", {}, "Dashboard activity will appear here after performing an operation.")
      )
    );
  } else {
    const resultKind = (r) => r === "Succeeded" ? "success" : r === "Blocked" ? "blocked" : "error";
    opsPanel = buildPanel("Operations Log", `${ops.length} session event${ops.length !== 1 ? "s" : ""}`,
      el("div", { class: "table-scroll" },
        el("table", {},
          el("thead", {},
            el("tr", {},
              el("th", {}, "Operation"),
              el("th", {}, "Resource"),
              el("th", {}, "Result"),
              el("th", {}, "Time")
            )
          ),
          el("tbody", {},
            ...ops.map((item) => el("tr", {},
              el("td", {}, item.operation),
              el("td", { class: "mono" }, item.resource),
              el("td", {}, badgeEl(item.result, resultKind(item.result))),
              el("td", {}, item.time)
            ))
          )
        )
      )
    );
  }
  pageContent.appendChild(opsPanel);

  renderListingFilter();
}

/* =============================================================
   SERVICE PAGE RENDER
   ============================================================= */

function serviceCollections(key, data) {
  if (key === "s3")      return [{ title: "Buckets",    rows: data.buckets,    columns: [["name","Bucket"],["created_at","Created"]] }];
  if (key === "dynamodb")return [{ title: "Tables",     rows: data.tables,     columns: [["name","Table name"],["console_owned","Session-owned"]] }];
  if (key === "sqs")     return [{ title: "Queues",     rows: data.queues,     columns: [["name","Queue"],["url","Queue URL",true]] }];
  if (key === "sns")     return [{ title: "Topics",     rows: data.topics,     columns: [["name","Topic"],["arn","Topic ARN",true]] }];
  if (key === "lambda")  return [{ title: "Functions",  rows: data.functions,  columns: [["name","Function"],["runtime","Runtime"],["state","State"],["memory_size","Memory MB"],["timeout","Timeout s"],["handler","Handler"]] }];
  if (key === "ec2")     return [
    { title: "Instances", rows: data.instances, columns: [["id","Instance ID",true],["name","Name"],["state","State"],["instance_type","Type"],["image_id","AMI"],["private_ip","Private IP",true],["key_name","Key pair"],["launch_time","Launched"]] },
    { title: "Key Pairs", rows: data.key_pairs.key_pairs, error: data.key_pairs.ok ? null : data.key_pairs.error.message, columns: [["name","Key pair"],["id","Key pair ID",true],["fingerprint","Fingerprint",true],["console_owned","Session-owned"]] },
  ];
  if (key === "cloudwatch") return [
    { title: "Metrics",    rows: data.metrics.ok    ? data.metrics.metrics       : [], error: data.metrics.ok    ? null : data.metrics.error.message,    columns: [["namespace","Namespace"],["name","Metric"],["dimensions","Dimensions"]] },
    { title: "Log Groups", rows: data.log_groups.ok ? data.log_groups.log_groups : [], error: data.log_groups.ok ? null : data.log_groups.error.message, columns: [["name","Log group"],["stored_bytes","Stored bytes"],["retention_days","Retention"]] },
  ];
  return [
    { title: "REST APIs", rows: data.rest.ok ? data.rest.apis : [], error: data.rest.ok ? null : data.rest.error.message, columns: [["name","API"],["id","API ID",true],["console_owned","Session-owned"],["endpoint_type","Endpoint type"]] },
    { title: "HTTP APIs", rows: data.http.ok ? data.http.apis : [], error: data.http.ok ? null : data.http.error.message, columns: [["name","API"],["id","API ID",true],["protocol_type","Protocol"]] },
  ];
}

/* Build select options as an HTML string (only used in templated forms) */
function selectOptions(items, valueKey, labelKey) {
  return items.map((i) => `<option value="${escapeHtml(i[valueKey])}">${escapeHtml(i[labelKey])}</option>`).join("");
}

/* Service-specific control panels — still HTML template strings for forms */
function serviceControls(key, data) {
  const panel = (title, note, content) =>
    `<section class="panel"><div class="panel-heading"><h3>${escapeHtml(title)}</h3><span class="panel-meta">${note}</span></div><div class="form-panel">${content}</div></section>`;

  if (key === "s3") {
    const opts = selectOptions(data.buckets, "name", "name");
    const bucketRows = data.buckets.map((b) =>
      `<div class="action-list-row"><span><strong>${escapeHtml(b.name)}</strong></span>` +
      `<div class="action-list-buttons"><button class="secondary-button" data-action="list-objects" data-bucket="${escapeHtml(b.name)}">List objects</button>` +
      `${b.console_owned ? `<button class="danger-button" data-action="delete-bucket" data-bucket="${escapeHtml(b.name)}">Delete empty bucket</button>` : ""}</div></div>`
    ).join("");
    return `
      ${panel("Create Bucket", "Live Floci operation",
        `<form class="action-form" data-action="create-bucket">
          <label>Bucket name<input name="name" required minlength="3" maxlength="63" pattern="[a-z0-9][a-z0-9.\\-]{1,61}[a-z0-9]" placeholder="localcloud-demo-bucket" autocomplete="off"></label>
          <button class="primary-button" type="submit">Create bucket</button>
        </form>`
      )}
      ${panel("Upload Object", "Live PutObject",
        `<form class="action-form" data-action="upload-object">
          <label>Bucket<select name="bucket" required ${data.buckets.length ? "" : "disabled"}>${opts}</select></label>
          <label>Object key<input name="key" required maxlength="1024" placeholder="reports/summary.txt" autocomplete="off"></label>
          <label>File<input name="file" type="file" required></label>
          <button class="primary-button" type="submit" ${data.buckets.length ? "" : "disabled"}>Upload file</button>
        </form>`
      )}
      ${data.buckets.length
        ? `<section class="panel"><div class="panel-heading"><h3>Browse Buckets</h3><span class="panel-meta">Click a bucket to list objects</span></div><div class="action-list">${bucketRows}</div></section>`
        : ""}`;
  }

  if (key === "dynamodb") {
    const owned = data.tables.filter((t) => t.console_owned);
    const opts  = selectOptions(owned, "name", "name");
    const tableRows = data.tables.map((t) =>
      `<div class="action-list-row"><span><strong>${escapeHtml(t.name)}</strong>${t.console_owned ? ` <span class="badge badge-info" style="margin-left:6px">Session</span>` : ""}</span>` +
      `<div class="action-list-buttons"><button class="secondary-button" data-action="inspect-table" data-table="${escapeHtml(t.name)}">Inspect</button>` +
      `${t.console_owned ? `<button class="danger-button" data-action="delete-table" data-table="${escapeHtml(t.name)}">Delete</button>` : ""}</div></div>`
    ).join("");
    return `
      ${panel("Create Table", "PAY_PER_REQUEST · String pk",
        `<form class="action-form" data-action="create-table">
          <label>Table name<input name="name" required minlength="3" maxlength="255" pattern="[A-Za-z0-9_.\\-]+" placeholder="localcloud-demo-table" autocomplete="off"></label>
          <span class="form-note">Partition key: <code>pk</code> (String). On-demand billing.</span>
          <button class="primary-button" type="submit">Create table</button>
        </form>`
      )}
      ${panel("Put Item", "JSON · session-owned tables only",
        `<form class="action-form" data-action="put-item">
          <label>Table<select name="table" required ${owned.length ? "" : "disabled"}>${opts}</select></label>
          <label>Item JSON<textarea name="item_json" required rows="7" spellcheck="false" placeholder='{"pk":"sample-001","value":"hello","count":1}'></textarea></label>
          <label class="checkbox-label"><input name="overwrite" type="checkbox" value="true"> Replace the item if this pk already exists</label>
          ${!owned.length ? `<span class="form-note">Create a table this session to enable writes. Pre-existing tables are read-only here.</span>` : ""}
          <button class="primary-button" type="submit" ${owned.length ? "" : "disabled"}>Put item</button>
        </form>`
      )}
      ${owned.length ? panel("Retrieve Item", "Consistent read",
        `<form class="action-form" data-action="get-item">
          <label>Table<select name="table" required>${opts}</select></label>
          <label>Partition key<input name="pk" required maxlength="256" autocomplete="off"></label>
          <button class="secondary-button" type="submit">Retrieve item</button>
        </form>`) : ""}
      ${owned.length ? panel("Browse Items", "Scan · 100 items per page",
        `<form class="action-form" data-action="scan-items">
          <label>Table<select name="table" required>${opts}</select></label>
          <button class="secondary-button" type="submit">Scan table</button>
        </form>`) : ""}
      ${data.tables.length
        ? `<section class="panel"><div class="panel-heading"><h3>Table Details</h3><span class="panel-meta">DescribeTable</span></div><div class="action-list">${tableRows}</div></section>`
        : ""}`;
  }

  if (key === "sqs") {
    const opts = selectOptions(data.queues, "url", "name");
    const queueRows = data.queues.map((q) =>
      `<div class="action-list-row"><span><strong>${escapeHtml(q.name)}</strong><small class="mono">${escapeHtml(q.url)}</small></span>` +
      `<div class="action-list-buttons"><button class="secondary-button" data-action="queue-attributes" data-queue="${escapeHtml(q.url)}">Attributes</button>` +
      `${q.console_owned ? `<button class="danger-button" data-action="delete-queue" data-queue="${escapeHtml(q.url)}" data-queue-name="${escapeHtml(q.name)}">Delete</button>` : ""}</div></div>`
    ).join("");
    return `
      ${panel("Create Standard Queue", "Live Floci operation",
        `<form class="action-form" data-action="create-queue">
          <label>Queue name<input name="name" required maxlength="80" pattern="[A-Za-z0-9_\\-]+" placeholder="localcloud-demo-queue" autocomplete="off"></label>
          <button class="primary-button" type="submit">Create queue</button>
        </form>`
      )}
      ${panel("Send Message", "Live SendMessage",
        `<form class="action-form" data-action="send-message">
          <label>Queue<select name="queue_url" required ${data.queues.length ? "" : "disabled"}>${opts}</select></label>
          <label>Message body<textarea name="message" required maxlength="262144" rows="3" placeholder="A message for this local queue"></textarea></label>
          <button class="primary-button" type="submit" ${data.queues.length ? "" : "disabled"}>Send message</button>
        </form>`
      )}
      ${data.queues.length ? panel("Receive Messages", "Receipt handles are not shown to the browser",
        `<form class="action-form" data-action="receive-messages">
          <label>Queue<select name="queue_url" required>${opts}</select></label>
          <label>Max messages<select name="max_messages"><option value="1">1</option><option value="5" selected>5</option><option value="10">10</option></select></label>
          <button class="secondary-button" type="submit">Receive messages</button>
        </form>`) : ""}
      ${data.queues.length
        ? `<section class="panel"><div class="panel-heading"><h3>Queue Actions</h3><span class="panel-meta">GetQueueAttributes</span></div><div class="action-list">${queueRows}</div></section>`
        : ""}`;
  }

  if (key === "sns") {
    const opts = selectOptions(data.topics, "arn", "name");
    const topicRows = data.topics.map((t) =>
      `<div class="action-list-row"><span class="mono">${escapeHtml(t.name)}</span>` +
      `<div class="action-list-buttons"><button class="secondary-button" data-action="inspect-topic" data-topic="${escapeHtml(t.arn)}">Subscriptions</button>` +
      `${t.console_owned ? `<button class="danger-button" data-action="delete-topic" data-topic="${escapeHtml(t.arn)}" data-topic-name="${escapeHtml(t.name)}">Delete</button>` : ""}</div></div>`
    ).join("");
    return `
      ${panel("Create Topic", "Live Floci operation",
        `<form class="action-form" data-action="create-topic">
          <label>Topic name<input name="name" required maxlength="256" pattern="[A-Za-z0-9_\\-]+" placeholder="localcloud-demo-topic" autocomplete="off"></label>
          <button class="primary-button" type="submit">Create topic</button>
        </form>`
      )}
      ${panel("Publish Message", "Delivers to local SQS subscriptions",
        `<div class="warn-band safety-note">Publishing is allowed only when every subscription targets a local SQS queue in this Floci account and region. Email, HTTP, Lambda, and other endpoints are blocked.</div>
        <form class="action-form" data-action="publish-message">
          <label>Topic<select name="topic_arn" required ${data.topics.length ? "" : "disabled"}>${opts}</select></label>
          <label>Message<textarea name="message" required maxlength="262144" rows="3" placeholder="Local test message"></textarea></label>
          <button class="primary-button" type="submit" ${data.topics.length ? "" : "disabled"}>Publish</button>
        </form>`
      )}
      ${panel("Create Subscription", "Subscribe an endpoint to a topic",
        `<div class="warn-band safety-note">Floci does not send SNS email confirmation messages. An email subscription is only recorded locally; use a local SQS subscription to demonstrate message delivery. Real email confirmation requires AWS SNS.</div>
        <form class="action-form" data-action="subscribe-topic">
          <label>Topic<select name="topic_arn" required ${data.topics.length ? "" : "disabled"}>${opts}</select></label>
          <label>Protocol<select name="protocol" required>
            <option value="email">email</option>
            <option value="email-json">email-json</option>
            <option value="sms">sms</option>
            <option value="http">http</option>
            <option value="https">https</option>
            <option value="sqs">sqs</option>
            <option value="lambda">lambda</option>
          </select></label>
          <label>Endpoint<input name="endpoint" required placeholder="user@example.com or local SQS ARN" autocomplete="off"></label>
          <span class="form-note">For a working local delivery demo, choose SQS and use the ARN shown in SQS &gt; Attributes. Other protocols do not deliver messages through Floci.</span>
          <button class="primary-button" type="submit" ${data.topics.length ? "" : "disabled"}>Subscribe</button>
        </form>`
      )}
      ${data.topics.length
        ? `<section class="panel"><div class="panel-heading"><h3>Topic Subscriptions</h3><span class="panel-meta">ListSubscriptionsByTopic</span></div><div class="action-list">${topicRows}</div></section>`
        : ""}`;
  }

  if (key === "api_gateway") {
    const apis = data.rest.ok ? data.rest.apis : [];
    const httpApis = data.http.ok ? data.http.apis : [];
    const apiRows = apis.map((api) =>
      `<div class="action-list-row"><span><strong>${escapeHtml(api.name)}</strong><small class="mono">${escapeHtml(api.id)}</small></span>` +
      `<div class="action-list-buttons"><button class="secondary-button" data-action="inspect-rest-api" data-api-id="${escapeHtml(api.id)}">Resources</button>` +
      `${api.console_owned ? `<button class="danger-button" data-action="delete-rest-api" data-api-id="${escapeHtml(api.id)}" data-api-name="${escapeHtml(api.name)}">Delete</button>` : ""}</div></div>`
    ).join("");
    const httpApiSelect = httpApis.map((api) =>
      `<option value="${escapeHtml(api.id)}">${escapeHtml(api.name)} (${escapeHtml(api.id)})</option>`
    ).join("");
    const httpApiRows = httpApis.map((api) =>
      `<div class="action-list-row"><span><strong>${escapeHtml(api.name)}</strong><small class="mono">${escapeHtml(api.id)}</small></span>` +
      `<div class="action-list-buttons"><button class="secondary-button" data-action="inspect-http-routes" data-api-id="${escapeHtml(api.id)}">Routes</button>` +
      `${api.console_owned ? `<button class="danger-button" data-action="delete-http-api" data-api-id="${escapeHtml(api.id)}" data-api-name="${escapeHtml(api.name)}">Delete</button>` : ""}</div></div>`
    ).join("");
    return `
      ${panel("Create REST API", "Does not deploy or expose a public stage",
        `<form class="action-form" data-action="create-rest-api">
          <label>API name<input name="name" required maxlength="128" placeholder="localcloud-demo-api" autocomplete="off"></label>
          <span class="form-note">Creation registers the API in Floci only. No stage is deployed.</span>
          <button class="primary-button" type="submit">Create REST API</button>
        </form>`
      )}
      ${apis.length
        ? `<section class="panel"><div class="panel-heading"><h3>REST APIs</h3><span class="panel-meta">Management calls remain local</span></div><div class="action-list">${apiRows}</div></section>`
        : ""}
      ${panel("Create HTTP API", "Creates a local $default stage",
        `<form class="action-form" data-action="create-http-api">
          <label>API name<input name="name" required maxlength="128" placeholder="localcloud-demo-http-api" autocomplete="off"></label>
          <button class="primary-button" type="submit">Create HTTP API</button>
        </form>`
      )}
      ${httpApis.length
        ? `<section class="panel"><div class="panel-heading"><h3>HTTP APIs</h3><span class="panel-meta">Lambda-backed routes · local $default stage</span></div><div class="action-list">${httpApiRows}</div></section>`
        : ""}
      ${httpApis.length ? panel("Add Lambda Route", "Connect an HTTP API route to a local Lambda function",
        `<form class="action-form" data-action="create-http-route">
          <label>HTTP API<select name="api_id" required>${httpApiSelect}</select></label>
          <label>Route key<input name="route_key" required value="GET /hello" placeholder="GET /hello" autocomplete="off"></label>
          <label>Lambda function name<input name="function_name" required pattern="[A-Za-z0-9_-]{1,64}" placeholder="localcloud-hello" autocomplete="off"></label>
          <span class="form-note">Create the function on the Lambda page first. Route uses unauthenticated local AWS_PROXY integration.</span>
          <button class="primary-button" type="submit">Add route</button>
        </form>`) : ""}
      ${panel("Invoke HTTP API", "Send a request to the selected API's local $default stage",
        `<form class="action-form" data-action="invoke-http-api">
          <label>API<select name="api_id" required ${httpApis.length ? "" : "disabled"}>${httpApiSelect}</select></label>
          <label>Method<select name="method">
            <option value="GET" selected>GET</option>
            <option value="POST">POST</option>
            <option value="PUT">PUT</option>
            <option value="DELETE">DELETE</option>
            <option value="PATCH">PATCH</option>
          </select></label>
          <label>Path<input name="path" value="/hello" placeholder="/hello" autocomplete="off"></label>
          <label>Body (JSON, optional)<textarea name="body" rows="2" placeholder='{"hello":"world"}'></textarea></label>
          <button class="primary-button" type="submit" ${httpApis.length ? "" : "disabled"}>Invoke</button>
        </form>`
      )}`;
  }

  if (key === "cloudwatch") {
    const groups = data.log_groups.ok ? data.log_groups.log_groups : [];
    const ownedGroups = groups.filter((group) => group.console_owned);
    const groupOptions = ownedGroups.length
      ? selectOptions(ownedGroups, "name", "name")
      : `<option value="">Create a log group first</option>`;
    const logRows = groups.map((g) =>
      `<div class="action-list-row"><span class="mono">${escapeHtml(g.name)}</span>` +
      `<div class="action-list-buttons"><button class="secondary-button" data-action="inspect-log-group" data-group="${escapeHtml(g.name)}">List streams</button>` +
      `${g.console_owned ? `<button class="danger-button" data-action="delete-log-group" data-group="${escapeHtml(g.name)}">Delete</button>` : ""}</div></div>`
    ).join("");
    const metricsErr = data.metrics.ok ? "" : `<div class="error-band">CloudWatch Metrics request failed: ${escapeHtml(data.metrics.error.message)}</div>`;
    const logsErr    = data.log_groups.ok ? "" : `<div class="error-band">CloudWatch Logs request failed: ${escapeHtml(data.log_groups.error.message)}</div>`;
    return `${metricsErr}${logsErr}
      ${panel("Create Log Group", "New groups are owned by this dashboard session",
        `<form class="action-form" data-action="create-log-group">
          <label>Log group name<input name="group_name" required maxlength="512" pattern="[A-Za-z0-9_./#\\-]+" placeholder="/localcloud/application" autocomplete="off"></label>
          <button class="primary-button" type="submit">Create log group</button>
        </form>`
      )}
      ${panel("Create Log Stream", "Writes are limited to groups created in this session",
        `<form class="action-form" data-action="create-log-stream">
          <label>Log group<select name="group_name" required ${ownedGroups.length ? "" : "disabled"}>${groupOptions}</select></label>
          <label>Log stream name<input name="stream_name" required maxlength="512" pattern="[A-Za-z0-9_./#\\-]+" placeholder="application" autocomplete="off"></label>
          <button class="primary-button" type="submit" ${ownedGroups.length ? "" : "disabled"}>Create log stream</button>
        </form>`
      )}
      ${panel("Write Log Event", "One timestamped event · max 256 KiB",
        `<form class="action-form" data-action="put-log-event">
          <label>Log group<select name="group_name" required ${ownedGroups.length ? "" : "disabled"}>${groupOptions}</select></label>
          <label>Log stream<input name="stream_name" required maxlength="512" placeholder="application" autocomplete="off"></label>
          <label>Message<textarea name="message" required maxlength="262144" rows="3" placeholder="Application event"></textarea></label>
          <button class="primary-button" type="submit" ${ownedGroups.length ? "" : "disabled"}>Put log event</button>
        </form>`
      )}
      ${panel("EC2 Instance Monitoring", "Publish and view real CloudWatch metrics for a running instance. Floci does not auto-emit EC2 metrics, so datapoints appear only after they are published through the PutMetricData form below.",
        `<div class="cw-monitoring">
          <div class="action-form">
            <label>Instance<select id="cw-instance-select" name="instance_id" required><option value="">Loading instances…</option></select></label>
            <label>Metric name<input name="metric_name" list="ec2-metric-names" value="CPUUtilization" autocomplete="off"></label>
            <datalist id="ec2-metric-names"><option value="CPUUtilization"><option value="NetworkIn"><option value="NetworkOut"><option value="DiskReadOps"><option value="DiskWriteOps"><option value="DiskReadBytes"><option value="DiskWriteBytes"><option value="CPUCreditUsage"><option value="CPUCreditBalance"></datalist>
            <label>Statistic<select name="statistic"><option value="Average">Average</option><option value="Sum">Sum</option><option value="Minimum">Minimum</option><option value="Maximum">Maximum</option><option value="SampleCount">Sample count</option></select></label>
            <label>Period (s)<select name="period"><option value="60">60</option><option value="300" selected>300</option><option value="900">900</option><option value="3600">3600</option></select></label>
            <label>Time range<select name="time_range"><option value="5m">Last 5 minutes</option><option value="15m">Last 15 minutes</option><option value="1h" selected>Last hour</option><option value="3h">Last 3 hours</option><option value="6h">Last 6 hours</option><option value="12h">Last 12 hours</option><option value="24h">Last 24 hours</option><option value="custom">Custom</option></select></label>
            <label>Start (ISO)<input name="start_time" class="cw-iso-input" autocomplete="off"></label>
            <label>End (ISO)<input name="end_time" class="cw-iso-input" autocomplete="off"></label>
            <button class="primary-button" type="submit" data-action="fetch-metric-statistics">Fetch metric data</button>
            <button class="secondary-button" type="button" data-action="refresh-metric-graph">Refresh</button>
            <span class="form-note" style="grid-column: 1 / -1;">Presets override Start/End. Choose Custom to set them manually. Times are UTC.</span>
          </div>
          <div id="cw-graph-output" class="cw-graph-output"></div>
        </div>`
      )}
      ${panel("Publish metric datum", "Publish a real datapoint to CloudWatch Metrics for the selected instance (PutMetricData). This is how data appears in the graph above; Floci does not auto-emit EC2 metrics.",
        `<form class="action-form" data-action="publish-metric-datum">
          <label>Namespace<input name="namespace" value="AWS/EC2" autocomplete="off"></label>
          <label>Metric name<input name="metric_name" value="CPUUtilization" autocomplete="off"></label>
          <label>Value<input name="value" type="number" step="any" placeholder="e.g. 42.5" autocomplete="off"></label>
          <label>Unit<select name="unit"><option value="">None</option><option value="Percent">Percent</option><option value="Count">Count</option><option value="Bytes">Bytes</option><option value="Bytes/Second">Bytes/Second</option></select></label>
          <button class="primary-button" type="submit">Publish datapoint</button>
          <span class="form-note" style="grid-column: 1 / -1;">The InstanceId dimension is taken from the instance selector above. The datapoint is timestamped now.</span>
        </form>`
      )}
      ${groups.length
        ? `<section class="panel"><div class="panel-heading"><h3>Log Group Explorer</h3><span class="panel-meta">List streams and inspect events</span></div><div class="action-list">${logRows}</div></section>`
        : ""}`;
  }

  if (key === "ec2") {
    const amiOptions = [
      { value: "ami-ubuntu2204", label: "Ubuntu 22.04 (ami-ubuntu2204)" },
      { value: "ami-ubuntu2404-amd64", label: "Ubuntu 24.04 (ami-ubuntu2404-amd64)" },
      { value: "ami-0abcdef1234567892", label: "Ubuntu 20.04 (ami-0abcdef1234567892)" },
      { value: "ami-debian12", label: "Debian 12 (ami-debian12)" },
      { value: "ami-0abcdef1234567891", label: "Amazon Linux 2023 (ami-0abcdef1234567891)" },
      { value: "ami-0abcdef1234567890", label: "Amazon Linux 2 (ami-0abcdef1234567890)" },
      { value: "ami-alpine", label: "Alpine (ami-alpine)" },
    ].map((o) => `<option value="${escapeHtml(o.value)}">${escapeHtml(o.label)}</option>`).join("");
    const typeOptions = ["t2.micro", "t3.micro", "t3.small", "t3.medium", "m5.large"]
      .map((t) => `<option value="${t}">${t}</option>`).join("");
    const keyPairs = data.key_pairs.key_pairs || [];
    const keyOptions = `<option value="">Proceed without a key pair</option>` + keyPairs
      .map((k) => `<option value="${escapeHtml(k.name)}">${escapeHtml(k.name)}</option>`).join("");
    const instanceRows = data.instances.map((inst) => {
      const running = inst.state === "running";
      const stopped = inst.state === "stopped";
      const gone = inst.state === "terminated" || inst.state === "shutting-down";
      const actionBtns = gone ? `<small class="mono">${escapeHtml(inst.state)}</small>` :
        `<button class="primary-button" data-action="ec2-connect" data-instance-id="${escapeHtml(inst.id)}" ${running ? "" : "disabled"}>Connect</button>` +
        `<button class="secondary-button" data-action="ec2-start" data-instance-id="${escapeHtml(inst.id)}" ${stopped ? "" : "disabled"}>Start</button>` +
        `<button class="secondary-button" data-action="ec2-stop" data-instance-id="${escapeHtml(inst.id)}" ${running ? "" : "disabled"}>Stop</button>` +
        `<button class="secondary-button" data-action="ec2-reboot" data-instance-id="${escapeHtml(inst.id)}" ${running ? "" : "disabled"}>Reboot</button>` +
        `<button class="danger-button" data-action="ec2-terminate" data-instance-id="${escapeHtml(inst.id)}" data-instance-name="${escapeHtml(inst.name || inst.id)}">Terminate</button>`;
      const meta = [inst.state, inst.instance_type, inst.private_ip, inst.key_name ? `key: ${inst.key_name}` : null].filter(Boolean).map(escapeHtml).join(" · ");
      return `<div class="action-list-row"><span><strong>${escapeHtml(inst.id)}</strong>${inst.name ? ` <small>${escapeHtml(inst.name)}</small>` : ""}<small class="mono">${meta}</small></span><div class="action-list-buttons">${actionBtns}</div></div>`;
    }).join("");
    const keyRows = keyPairs.map((k) =>
      `<div class="action-list-row"><span><strong>${escapeHtml(k.name)}</strong><small class="mono">${escapeHtml(k.fingerprint || "")}</small></span>` +
      `<div class="action-list-buttons">${k.console_owned
        ? `<button class="danger-button" data-action="delete-key-pair" data-key-name="${escapeHtml(k.name)}">Delete</button>`
        : `<small>Not created in this session</small>`}</div></div>`
    ).join("");
    return `
      ${panel("Create Key Pair", "The private key (.pem) downloads once, at creation",
        `<form class="action-form" data-action="create-key-pair">
          <label>Key pair name<input name="name" required maxlength="255" pattern="[A-Za-z0-9 ._\\-]+" placeholder="localcloud-key" autocomplete="off"></label>
          <button class="primary-button" type="submit">Create key pair</button>
        </form>`
      )}
      ${keyRows ? `<section class="panel"><div class="panel-heading"><h3>Key Pair Actions</h3><span class="panel-meta">Delete (session-owned only)</span></div><div class="action-list">${keyRows}</div></section>` : ""}
      ${panel("Launch Instance", "Floci launches a Docker container per instance",
        `<form class="action-form" data-action="run-instances">
          <label>AMI<select name="image_id" required>${amiOptions}</select></label>
          <label>Instance type<select name="instance_type" required>${typeOptions}</select></label>
          <label>Name (optional tag)<input name="name" maxlength="128" placeholder="localcloud-demo-instance" autocomplete="off"></label>
          <label>Key pair (for SSH)<select name="key_name">${keyOptions}</select></label>
          <label>Count<select name="count"><option value="1" selected>1</option><option value="2">2</option><option value="3">3</option></select></label>
          <label class="checkbox-label"><input type="checkbox" name="install_ssh" checked> Install &amp; start OpenSSH (user data)</label>
          <button class="primary-button" type="submit">Launch instance</button>
        </form>`
      )}
      ${data.instances.length
        ? `<section class="panel"><div class="panel-heading"><h3>Instance Actions</h3><span class="panel-meta">Connect · Start · Stop · Reboot · Terminate (session-owned only)</span></div><div class="action-list">${instanceRows}</div></section>`
        : ""}
      ${panel("EC2 Metrics Collector", "Collect container-level metrics (CPU, memory, network, disk) from running Floci EC2 instances and publish them to CloudWatch Metrics under namespace LocalCloud/EC2 with an InstanceId dimension. These are host/container measurements, not native AWS EC2 metrics. Requires host Docker access (docker stats).",
        '<div class="action-form">' +
        '<label>Interval (s)<select name="interval_seconds"><option value="10">10</option><option value="30">30</option><option value="60" selected>60</option><option value="120">120</option><option value="300">300</option></select></label>' +
        '<label>Max instances<select name="max_instances"><option value="1">1</option><option value="5">5</option><option value="10" selected>10</option><option value="20">20</option></select></label>' +
        '<button class="primary-button" type="submit" data-action="ec2-metrics-start">Start collector</button>' +
        '<button class="secondary-button" type="submit" data-action="ec2-metrics-stop">Stop collector</button>' +
        '<span class="form-note" style="grid-column: 1 / -1;">Collector status and last error are shown below. Without Docker access the collector runs but reports that \'docker stats\' is unavailable.</span>' +
        '</div>' +
        '<div id="ec2-metrics-status" class="cw-monitoring"></div>'
      )}
    `;
  }

  if (key === "lambda") {
    const runtimeOptions = [
      "python3.12", "python3.11", "python3.10",
      "nodejs22.x", "nodejs20.x", "nodejs18.x",
      "java17", "go1.x", "ruby3.2",
    ].map((r) => `<option value="${r}">${r}</option>`).join("");
    const fnSelect = data.functions.map((fn) =>
      `<option value="${escapeHtml(fn.name)}">${escapeHtml(fn.name)}</option>`
    ).join("");
    const fnRows = data.functions.map((fn) =>
      `<div class="action-list-row"><span><strong>${escapeHtml(fn.name)}</strong><small class="mono">${escapeHtml(fn.runtime)} · ${escapeHtml(fn.handler)}</small></span>` +
      `<div class="action-list-buttons">` +
      `<button class="secondary-button" data-action="inspect-function" data-function="${escapeHtml(fn.name)}">Configuration</button>` +
      `<button class="danger-button" data-action="delete-function" data-function="${escapeHtml(fn.name)}">Delete</button>` +
      `</div></div>`
    ).join("");
    const sampleCode = `def handler(event, context):
    return {"statusCode": 200, "body": "Hello from LocalCloud Lambda", "event": event}`;
    return `
      ${panel("Create Function", "Inline source is packaged into a zip",
        `<form class="action-form" data-action="create-function">
          <label>Function name<input name="name" required maxlength="64" pattern="[A-Za-z0-9_-]{1,64}" placeholder="localcloud-demo-fn" autocomplete="off"></label>
          <label>Runtime<select name="runtime" required>${runtimeOptions}</select></label>
          <label>Handler<input name="handler" required pattern="[A-Za-z_][A-Za-z0-9_]*\\.[A-Za-z_][A-Za-z0-9_]*" value="index.handler" autocomplete="off"></label>
          <label>Memory (MB)<select name="memory_size"><option value="128" selected>128</option><option value="256">256</option><option value="512">512</option><option value="1024">1024</option></select></label>
          <label>Timeout (s)<input name="timeout" type="number" min="1" max="900" value="3"></label>
          <label>Source code<textarea name="code" required rows="6" spellcheck="false">${escapeHtml(sampleCode)}</textarea></label>
          <span class="form-note">The handler filename must match your code module. Python example above uses <code>index.handler</code>.</span>
          <button class="primary-button" type="submit">Create function</button>
        </form>`
      )}
      ${data.functions.length ? panel("Invoke Function", "Synchronous RequestResponse invoke",
        `<form class="action-form" data-action="invoke-function">
          <label>Function<select name="name" required>${fnSelect}</select></label>
          <label>Payload (JSON)<textarea name="payload" rows="3" placeholder='{"key": "value"}'>{}</textarea></label>
          <button class="primary-button" type="submit">Invoke</button>
        </form>`) : ""}
      ${data.functions.length
        ? `<section class="panel"><div class="panel-heading"><h3>Function Actions</h3><span class="panel-meta">Inspect configuration or delete (session-owned only)</span></div><div class="action-list">${fnRows}</div></section>`
        : ""}`;
  }

  return "";
}

function renderService(key, response) {
  const svc  = response.service;
  const data = response.data;
  pageContent.innerHTML = "";

  // Page intro
  const intro = el("div", { class: "page-intro" },
    el("div", {},
      el("h2", {}, svc.name),
      el("p", {}, svc.description)
    ),
    svc.docs
      ? el("a", { class: "panel-meta doc-link", href: svc.docs, target: "_blank", rel: "noreferrer noopener" },
          svgIcon("icon-external", ""), " Floci 2.2.0 docs"
        )
      : null
  );
  pageContent.appendChild(intro);

  // Operation evidence
  const ops = (svc.operations || []).map((op) => {
    const kind = op.validation === "passed" ? "success" : op.validation === "not validated" ? "warning" : "error";
    const chip = el("span", { class: "operation-chip" }, op.name, " ", badgeEl(op.validation, kind));
    return chip;
  });
  if (ops.length) {
    const opsPanel = buildPanel("Operation Evidence",
      "Documented support and validated testing are distinct",
      el("div", { class: "operation-list" }, ...ops)
    );
    pageContent.appendChild(opsPanel);
  }

  // Known limitations
  const limits = svc.limitations || [];
  if (limits.length) {
    const warnDiv = el("div", { class: "warn-band" },
      el("strong", {}, "Known limitations"),
      el("ul", { style: "margin-top:6px;padding-left:18px" },
        ...limits.map((l) => el("li", {}, l))
      )
    );
    pageContent.appendChild(warnDiv);
  }

  // Resource tables (DOM-safe)
  serviceCollections(key, data).forEach(({ title, rows, columns, error }) => {
    pageContent.appendChild(buildTablePanel(title, rows, columns, error));
  });

  // Controls (HTML template strings, still safe because we use escapeHtml throughout)
  const controlsHtml = serviceControls(key, data);
  if (controlsHtml) {
    const wrapper = document.createElement("div");
    wrapper.innerHTML = controlsHtml;
    while (wrapper.firstChild) pageContent.appendChild(wrapper.firstChild);
  }

  // Detail pane anchor
  const detailPane = el("div", { id: "detail-pane" });
  pageContent.appendChild(detailPane);

  renderListingFilter();

  if (key === "cloudwatch") {
    requestJson("/api/services/ec2").then((resp) => {
      const instances = (resp.data && resp.data.instances) || [];
      const sel = document.getElementById("cw-instance-select");
      if (!sel) return;
      if (!instances.length) {
        sel.innerHTML = `<option value="">No instances found</option>`;
        return;
      }
      sel.innerHTML = `<option value="">Choose an instance</option>` +
        instances.map((inst) =>
          `<option value="${escapeHtml(inst.id)}">${escapeHtml(inst.id)}</option>`
        ).join("");
    }).catch(() => {
      const sel = document.getElementById("cw-instance-select");
      if (sel) sel.innerHTML = `<option value="">Instance list unavailable</option>`;
    });
  }
  if (key === "ec2") {
    fetchEc2MetricsStatus();
  }
}

async function renderServicePage(key) {
  pageContent.innerHTML = "";
  const loading = el("div", { class: "initial-state", "aria-live": "polite" },
    el("div", { class: "skeleton-grid" },
      ...Array(4).fill(null).map(() => el("div", { class: "skeleton-card" }))
    ),
    el("span", {}, `Loading ${serviceTitles[key]} inventory…`)
  );
  pageContent.appendChild(loading);

  try {
    const response = await requestJson(`/api/services/${key}`);
    renderService(key, response);
  } catch (error) {
    pageContent.innerHTML = "";
    pageContent.appendChild(el("div", { class: "error-band" }, `${error.code || "Error"}: ${error.message}`));
    pageContent.appendChild(el("div", { class: "empty-state" },
      svgIcon("icon-alert-circle"),
      el("strong", {}, "Service request failed"),
      el("span", {}, "Other service pages can still load independently.")
    ));
  }
}

/* =============================================================
   DETAIL PANE HELPERS
   ============================================================= */

function showDetail(title, rows, columns) {
  const pane = document.getElementById("detail-pane");
  if (!pane) return;
  pane.innerHTML = "";
  pane.appendChild(buildTablePanel(title, rows, columns));
}

function showReceivedMessages(messages) {
  const pane = document.getElementById("detail-pane");
  if (!pane) return;
  pane.innerHTML = "";

  if (!messages.length) {
    pane.appendChild(buildPanel("Received Messages", null,
      el("div", { class: "empty-state" },
        svgIcon("icon-sqs"),
        el("strong", {}, "No messages received"),
        el("span", {}, "The queue returned no visible messages at this time.")
      )
    ));
    return;
  }

  const tbody = el("tbody", {});
  messages.forEach((msg) => {
    receiptHandles.set(msg.MessageId, msg.delete_token);
    const tr = el("tr", {},
      el("td", { class: "mono" }, msg.MessageId),
      el("td", {}, msg.Body),
      el("td", {},
        el("button", {
          class: "danger-button",
          "data-action": "delete-message",
          "data-message-id": msg.MessageId,
        }, "Delete")
      )
    );
    tbody.appendChild(tr);
  });

  pane.appendChild(buildPanel(
    "Received Messages",
    "Receipt handles are not displayed",
    el("div", { class: "table-scroll" },
      el("table", {},
        el("thead", {},
          el("tr", {}, el("th", {}, "Message ID"), el("th", {}, "Body"), el("th", {}, "Action"))
        ),
        tbody
      )
    )
  ));
}

function showObjectListing(bucket, response) {
  const pane = document.getElementById("detail-pane");
  if (!pane) return;
  pane.innerHTML = "";

  const objs = response.objects || [];
  if (!objs.length) {
    pane.appendChild(buildPanel(`Objects in ${bucket}`, null,
      el("div", { class: "empty-state" },
        svgIcon("icon-s3"),
        el("strong", {}, "Bucket is empty"),
        el("span", {}, "No objects were returned.")
      )
    ));
    return;
  }

  const tbody = el("tbody", {});
  objs.forEach((obj) => {
    const encodedKey = obj.key.split("/").map(encodeURIComponent).join("/");
    const downloadUrl = `/api/services/s3/buckets/${encodeURIComponent(bucket)}/objects/${encodedKey}/download`;
    const tr = el("tr", {},
      el("td", { class: "mono" }, obj.key),
      el("td", {}, String(obj.size)),
      el("td", { class: "mono" }, obj.etag),
      el("td", {},
        el("div", { class: "action-list-buttons" },
          el("a", { class: "secondary-button", href: downloadUrl }, "Download"),
          el("button", { class: "secondary-button", "data-action": "object-metadata", "data-bucket": bucket, "data-key": obj.key }, "Metadata"),
          el("button", { class: "danger-button", "data-action": "delete-object", "data-bucket": bucket, "data-key": obj.key }, "Delete")
        )
      )
    );
    tbody.appendChild(tr);
  });

  const nextBtn = response.truncated
    ? el("button", { class: "secondary-button", "data-action": "list-objects", "data-bucket": bucket, "data-token": response.continuation_token }, "Next page")
    : null;

  pane.appendChild(buildPanel(
    `Objects in ${bucket}`,
    `${objs.length} returned`,
    el("div", {}, ...[
      el("div", { class: "table-scroll" },
        el("table", {},
          el("thead", {},
            el("tr", {}, el("th", {}, "Key"), el("th", {}, "Size"), el("th", {}, "ETag"), el("th", {}, "Actions"))
          ),
          tbody
        )
      ),
      nextBtn ? el("div", { style: "padding:12px 16px;border-top:1px solid var(--border)" }, nextBtn) : null,
    ].filter(Boolean))
  ));
}

function showDdbScan(table, response) {
  const pane = document.getElementById("detail-pane");
  if (!pane) return;
  pane.innerHTML = "";
  const items = response.items || [];
  const rows = items.map((item) =>
    el("tr", {},
      el("td", { class: "mono" }, item.pk),
      el("td", { class: "mono" }, JSON.stringify(item)),
      el("td", {}, el("button", {
        class: "danger-button",
        "data-action": "delete-item",
        "data-table": table,
        "data-pk": item.pk,
      }, "Delete"))
    )
  );
  const next = response.next_token
    ? el("button", { class: "secondary-button", "data-action": "scan-items-next", "data-table": table, "data-token": response.next_token }, "Next page")
    : null;
  pane.appendChild(buildPanel(
    `Items in ${table}`,
    `${items.length} returned${response.next_token ? " · more available" : ""}`,
    el("div", {}, ...[
      items.length
        ? el("div", { class: "table-scroll" },
          el("table", {},
            el("thead", {}, el("tr", {}, el("th", {}, "pk"), el("th", {}, "Item JSON"), el("th", {}, "Actions"))),
            el("tbody", {}, ...rows)
          ))
        : el("div", { class: "empty-state" }, el("strong", {}, "No items found"), el("span", {}, "Put an item in this table to get started.")),
      next ? el("div", { style: "padding:12px 16px;border-top:1px solid var(--border)" }, next) : null,
    ].filter(Boolean))
  ));
}

/* =============================================================
   FORM SUBMIT HANDLER
   ============================================================= */

async function handleFormSubmit(event) {
  const form = event.target.closest("form[data-action]");
  if (!form) return;
  event.preventDefault();

  const action = form.dataset.action;
  const values = Object.fromEntries(new FormData(form).entries());

  // Disable submit to prevent double-submission
  const submitBtn = form.querySelector("[type=submit]");
  const wasDisabled = submitBtn?.disabled;
  if (submitBtn) submitBtn.disabled = true;

  try {
    let result;

    if (action === "create-bucket") {
      result = await postJson("/api/services/s3/buckets", values);
    } else if (action === "upload-object") {
      const r = await fetch("/api/services/s3/objects", { method: "POST", body: new FormData(form) });
      result = await r.json().catch(() => ({}));
      if (!r.ok) throw Object.assign(new Error(result.error?.message || "Upload failed."), { code: result.error?.code || `HTTP ${r.status}` });
    } else if (action === "create-table") {
      result = await postJson("/api/services/dynamodb/tables", values);
    } else if (action === "put-item") {
      let item;
      try {
        item = JSON.parse(values.item_json);
      } catch {
        throw new Error("Item JSON is not valid.");
      }
      if (!item || typeof item !== "object" || Array.isArray(item) || typeof item.pk !== "string" || !item.pk) {
        throw new Error('Item JSON must be an object with a non-empty String "pk".');
      }
      const overwrite = form.elements.overwrite.checked;
      const confirmation = overwrite
        ? await confirmExact("Replace DynamoDB Item", "Replaces every attribute on the existing item with this JSON.", item.pk, "Replace item")
        : false;
      if (overwrite && !confirmation) {
        if (submitBtn && !wasDisabled) submitBtn.disabled = false;
        return;
      }
      result = await postJson("/api/services/dynamodb/items", {
        table: values.table,
        item,
        overwrite,
        ...(overwrite ? { confirmed: true, confirm_value: item.pk } : {}),
      });
    } else if (action === "scan-items") {
      const query = new URLSearchParams({ table: values.table });
      result = await requestJson(`/api/services/dynamodb/items/scan?${query}`);
      showDdbScan(values.table, result);
      toast(`${result.items.length} item(s) returned.`);
      if (submitBtn && !wasDisabled) submitBtn.disabled = false;
      return;
    } else if (action === "get-item") {
      const query = new URLSearchParams({ table: values.table, pk: values.pk });
      result = await requestJson(`/api/services/dynamodb/items?${query}`);
      if (result.item) {
        // Unwrap DynamoDB typed attribute notation e.g. {"S":"val"} -> "val"
        function unwrapDdb(attr) {
          if (!attr || typeof attr !== "object") return attr;
          const keys = Object.keys(attr);
          if (keys.length === 1 && ["S","N","B","BOOL","NULL"].includes(keys[0])) return String(attr[keys[0]]);
          return JSON.stringify(attr);
        }
        const flat = Object.fromEntries(Object.entries(result.item).map(([k, v]) => [k, unwrapDdb(v)]));
        showDetail("Retrieved Item", [flat], Object.keys(flat).map((k) => [k, k]));
      } else {
        showDetail("Retrieved Item", [], [["pk","Partition key"],["value","Value"]]);
      }
      toast(result.item ? "Item retrieved from Floci." : "No item matched that partition key.");
      if (submitBtn && !wasDisabled) submitBtn.disabled = false;
      return;
    } else if (action === "create-queue") {
      result = await postJson("/api/services/sqs/queues", values);
    } else if (action === "send-message") {
      result = await postJson("/api/services/sqs/messages/send", values);
    } else if (action === "receive-messages") {
      result = await postJson("/api/services/sqs/messages/receive", { ...values, max_messages: Number(values.max_messages) });
      showReceivedMessages(result.messages || []);
      toast(`${(result.messages || []).length} message(s) received.`);
      if (submitBtn && !wasDisabled) submitBtn.disabled = false;
      return;
    } else if (action === "create-topic") {
      result = await postJson("/api/services/sns/topics", values);
    } else if (action === "publish-message") {
      if (!await confirmExact("Publish local message", "Delivery is permitted only to local SQS queues; other subscription protocols are blocked.", "PUBLISH", "Publish")) {
        if (submitBtn && !wasDisabled) submitBtn.disabled = false;
        return;
      }
      result = await postJson("/api/services/sns/publish", values);
    } else if (action === "subscribe-topic") {
      result = await postJson("/api/services/sns/subscribe", values);
    } else if (action === "create-log-group") {
      result = await postJson("/api/services/cloudwatch/log-groups", values);
    } else if (action === "create-log-stream") {
      result = await postJson("/api/services/cloudwatch/log-streams", values);
    } else if (action === "put-log-event") {
      result = await postJson("/api/services/cloudwatch/events", values);
    } else if (action === "create-rest-api") {
      result = await postJson("/api/services/api_gateway/rest", values);
    } else if (action === "create-http-api") {
      result = await postJson("/api/services/api_gateway/http", values);
    } else if (action === "create-http-route") {
      result = await postJson("/api/services/api_gateway/http/routes", values);
    } else if (action === "invoke-http-api") {
      let body;
      try { body = values.body ? JSON.parse(values.body) : undefined; }
      catch { throw Object.assign(new Error("Body must be valid JSON."), { code: "ValidationError" }); }
      result = await postJson("/api/services/api_gateway/http/invoke", {
        api_id: values.api_id,
        method: values.method,
        path: values.path,
        ...(body === undefined ? {} : { body }),
      });
      showDetail("HTTP API Response", [{ status_code: result.status_code, body: result.body }], [["status_code","Status"],["body","Response body"]]);
      toast(`HTTP API returned ${result.status_code}.`);
      if (submitBtn && !wasDisabled) submitBtn.disabled = false;
      return;
    } else if (action === "fetch-metric-statistics") {
      const instanceId = form.elements.instance_id ? form.elements.instance_id.value : "";
      const metricName = form.elements.metric_name ? form.elements.metric_name.value : "";
      if (!instanceId) throw new Error("Choose an EC2 instance first.");
      if (!metricName) throw new Error("Enter a metric name.");
      const timeRange = form.elements.time_range ? form.elements.time_range.value : "1h";
      let start = form.elements.start_time ? form.elements.start_time.value : "";
      let end = form.elements.end_time ? form.elements.end_time.value : "";
      if (timeRange !== "custom") {
        const range = presetTimeRange(timeRange);
        start = range.start;
        end = range.end;
      }
      const statistic = form.elements.statistic ? form.elements.statistic.value : "Average";
      const period = form.elements.period ? form.elements.period.value : "300";
      const unit = form.elements.unit ? form.elements.unit.value : "";
      metricGraphState = {
        instanceId,
        metricName,
        namespace: "AWS/EC2",
        statistic,
        period,
        start,
        end,
        unit,
      };
      try {
        await fetchMetricStatistics(metricGraphState);
      } catch (error) {
        // error already rendered by fetchMetricStatistics
      }
      if (submitBtn && !wasDisabled) submitBtn.disabled = false;
      return;
    } else if (action === "publish-metric-datum") {
      const instanceId = document.getElementById("cw-instance-select") ? document.getElementById("cw-instance-select").value : "";
      if (!instanceId) throw new Error("Choose an EC2 instance first.");
      const metricName = values.metric_name;
      const value = Number(values.value);
      if (!metricName || typeof metricName !== "string" || !metricName) throw new Error("Enter a metric name.");
      if (!Number.isFinite(value)) throw new Error("Enter a numeric value.");
      const dimensions = [{ name: "InstanceId", value: instanceId }];
      const result = await postJson("/api/services/cloudwatch/metric-data", {
        namespace: values.namespace || "AWS/EC2",
        metric_name: metricName,
        value,
        unit: values.unit || undefined,
        dimensions,
      });
      toast("Metric datapoint published.");
      if (metricGraphState && metricGraphState.instanceId === instanceId && metricGraphState.metricName === metricName) {
        await fetchMetricStatistics(metricGraphState);
      }
      if (submitBtn && !wasDisabled) submitBtn.disabled = false;
      return;
    } else if (action === "ec2-metrics-start") {
      const interval = form.elements.interval_seconds ? Number(form.elements.interval_seconds.value) : 60;
      const maxInstances = form.elements.max_instances ? Number(form.elements.max_instances.value) : 10;
      result = await postJson("/api/services/ec2/metrics/collector/start", {
        interval_seconds: interval,
        max_instances: maxInstances,
      });
      await fetchEc2MetricsStatus();
      if (submitBtn && !wasDisabled) submitBtn.disabled = false;
      return;
    } else if (action === "ec2-metrics-stop") {
      result = await postJson("/api/services/ec2/metrics/collector/stop");
      await fetchEc2MetricsStatus();
      if (submitBtn && !wasDisabled) submitBtn.disabled = false;
      return;
    } else if (action === "run-instances") {
      result = await postJson("/api/services/ec2/instances", {
        ...values,
        count: Number(values.count || 1),
        key_name: values.key_name || null,
        install_ssh: values.install_ssh === "on",
      });
    } else if (action === "create-key-pair") {
      result = await postJson("/api/services/ec2/key-pairs", values);
      downloadTextFile(`${result.resource.name}.pem`, result.resource.private_key);
    } else if (action === "create-function") {
      result = await postJson("/api/services/lambda/functions", {
        ...values,
        memory_size: Number(values.memory_size || 128),
        timeout: Number(values.timeout || 3),
      });
    } else if (action === "invoke-function") {
      let parsed;
      try { parsed = values.payload ? JSON.parse(values.payload) : {}; }
      catch { throw Object.assign(new Error("Payload must be valid JSON."), { code: "ValidationError" }); }
      result = await postJson("/api/services/lambda/functions/invoke", { name: values.name, payload: parsed });
      showDetail("Invocation Result", [result.result], Object.keys(result.result).map((k) => [k, k]));
      toast("Invoke succeeded.");
      if (submitBtn && !wasDisabled) submitBtn.disabled = false;
      return;
    } else {
      if (submitBtn && !wasDisabled) submitBtn.disabled = false;
      return;
    }

    if (action === "subscribe-topic" && ["email", "email-json"].includes(values.protocol)) {
      toast("Subscription recorded in Floci; no confirmation email was sent.");
    } else {
      toast(`${actionLabel(action)} succeeded.`);
    }
    await navigate(currentView);
  } catch (error) {
    toast(`${error.code || "Error"}: ${error.message}`, true);
    if (submitBtn && !wasDisabled) submitBtn.disabled = false;
  }
}

function actionLabel(action) {
  return ({
    "create-bucket":  "CreateBucket",
    "upload-object":  "PutObject",
    "create-table":   "CreateTable",
    "put-item":       "PutItem",
    "create-queue":   "CreateQueue",
    "delete-queue":   "DeleteQueue",
    "send-message":   "SendMessage",
    "create-topic":   "CreateTopic",
    "delete-topic":   "DeleteTopic",
    "publish-message":"Publish",
    "subscribe-topic":"Subscribe",
    "create-log-group":"CreateLogGroup",
    "create-log-stream":"CreateLogStream",
    "put-log-event":"PutLogEvents",
    "create-rest-api":"CreateRestApi",
    "create-http-api":"CreateApi",
    "create-http-route":"CreateRoute",
    "run-instances":  "RunInstances",
    "create-key-pair":"CreateKeyPair",
    "create-function":"CreateFunction",
    "invoke-function":"Invoke",
  })[action] || "Operation";
}

/* =============================================================
   ACTION CLICK HANDLER (buttons with data-action)
   ============================================================= */

async function handleActionClick(event) {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const action = button.dataset.action;
  button.disabled = true;
  try {
    if (action === "list-objects") {
      const q = button.dataset.token ? `?continuation_token=${encodeURIComponent(button.dataset.token)}` : "";
      const r = await requestJson(`/api/services/s3/buckets/${encodeURIComponent(button.dataset.bucket)}/objects${q}`);
      showObjectListing(button.dataset.bucket, r);

    } else if (action === "object-metadata") {
      const bk = encodeURIComponent(button.dataset.bucket);
      const key = button.dataset.key.split("/").map(encodeURIComponent).join("/");
      const r = await requestJson(`/api/services/s3/buckets/${bk}/objects/${key}/metadata`);
      showDetail("Object Metadata", [r.metadata], Object.keys(r.metadata).map((n) => [n, n]));

    } else if (action === "delete-object") {
      const key = button.dataset.key;
      if (!await confirmExact("Delete S3 Object", `Remove object from bucket "${button.dataset.bucket}".`, key, "Delete object")) return;
      await postJson("/api/services/s3/objects/delete", { bucket: button.dataset.bucket, key, confirmed: true, confirm_value: key });
      toast("DeleteObject succeeded.");
      await navigate("s3");

    } else if (action === "delete-bucket") {
      const name = button.dataset.bucket;
      if (!await confirmExact("Delete S3 Bucket", "Only an empty bucket created in this dashboard session can be deleted.", name, "Delete bucket")) return;
      await postJson("/api/services/s3/buckets/delete", { name, confirmed: true, confirm_value: name });
      toast("DeleteBucket succeeded.");
      await navigate("s3");

    } else if (action === "inspect-table") {
      const r = await requestJson(`/api/services/dynamodb/tables/${encodeURIComponent(button.dataset.table)}`);
      showDetail("Table Metadata", [r.table], Object.keys(r.table).map((n) => [n, n]));

    } else if (action === "scan-items-next") {
      const query = new URLSearchParams({ table: button.dataset.table, next_token: button.dataset.token });
      const r = await requestJson(`/api/services/dynamodb/items/scan?${query}`);
      showDdbScan(button.dataset.table, r);

    } else if (action === "delete-item") {
      const table = button.dataset.table;
      const pk = button.dataset.pk;
      if (!await confirmExact("Delete DynamoDB Item", `Deletes this item from "${table}".`, pk, "Delete item")) return;
      await postJson("/api/services/dynamodb/items/delete", { table, pk, confirmed: true, confirm_value: pk });
      toast("DeleteItem succeeded.");
      const query = new URLSearchParams({ table });
      showDdbScan(table, await requestJson(`/api/services/dynamodb/items/scan?${query}`));

    } else if (action === "delete-table") {
      const name = button.dataset.table;
      if (!await confirmExact("Delete DynamoDB Table", "Deletes the table and all items in it.", name, "Delete table")) return;
      await postJson("/api/services/dynamodb/tables/delete", { name, confirmed: true, confirm_value: name });
      toast("DeleteTable succeeded.");
      await navigate("dynamodb");

    } else if (action === "inspect-function") {
      const r = await requestJson(`/api/services/lambda/functions/${encodeURIComponent(button.dataset.function)}`);
      showDetail("Lambda Configuration", [r.function], Object.keys(r.function).map((n) => [n, n]));

    } else if (action === "queue-attributes") {
      const r = await requestJson(`/api/services/sqs/attributes?queue_url=${encodeURIComponent(button.dataset.queue)}`);
      showDetail("Queue Attributes", Object.entries(r.attributes).map(([name, value]) => ({ name, value })), [["name","Attribute"],["value","Value"]]);

    } else if (action === "delete-queue") {
      const name = button.dataset.queueName;
      if (!await confirmExact("Delete SQS Queue", "Removes the queue and any messages it contains.", name, "Delete queue")) return;
      await postJson("/api/services/sqs/queues/delete", {
        queue_url: button.dataset.queue,
        name,
        confirmed: true,
        confirm_value: name,
      });
      toast("DeleteQueue succeeded.");
      await navigate("sqs");

    } else if (action === "delete-message") {
      const id = button.dataset.messageId;
      if (!await confirmExact("Delete SQS Message", "Only this received message will be deleted.", id, "Delete message")) return;
      const deleteToken = receiptHandles.get(id);
      if (!deleteToken) throw new Error("Delete token unavailable; receive the message again.");
      await postJson("/api/services/sqs/messages/delete", { delete_token: deleteToken, message_id: id, confirmed: true, confirm_value: id });
      receiptHandles.delete(id);
      toast("DeleteMessage succeeded.");
      const pane = document.getElementById("detail-pane");
      if (pane) {
        pane.innerHTML = "";
        pane.appendChild(buildPanel("Received Messages", null,
          el("div", { class: "empty-state" },
            el("strong", {}, "Message deleted"),
            el("span", {}, "Receive messages again to refresh.")
          )
        ));
      }

    } else if (action === "inspect-topic") {
      const r = await requestJson(`/api/services/sns/subscriptions?topic_arn=${encodeURIComponent(button.dataset.topic)}`);
      const rows = r.subscriptions.map((s) => ({ protocol: s.Protocol, endpoint: s.Endpoint, status: s.SubscriptionArn }));
      showDetail("Topic Subscriptions", rows, [["protocol","Protocol"],["endpoint","Endpoint"],["status","Subscription ARN"]]);

    } else if (action === "delete-topic") {
      const name = button.dataset.topicName;
      if (!await confirmExact("Delete SNS Topic", "Removes this topic and all its subscriptions.", name, "Delete topic")) return;
      await postJson("/api/services/sns/topics/delete", {
        topic_arn: button.dataset.topic,
        name,
        confirmed: true,
        confirm_value: name,
      });
      toast("DeleteTopic succeeded.");
      await navigate("sns");

    } else if (action === "inspect-rest-api") {
      const r = await requestJson(`/api/services/api_gateway/rest/${encodeURIComponent(button.dataset.apiId)}/resources`);
      showDetail("REST API Resources", r.resources, [["path","Path"],["id","Resource ID"],["methods","Methods"]]);

    } else if (action === "delete-rest-api") {
      const name = button.dataset.apiName;
      if (!await confirmExact("Delete REST API", "Removes only the API created in this session.", name, "Delete API")) return;
      await postJson("/api/services/api_gateway/rest/delete", { api_id: button.dataset.apiId, name, confirmed: true, confirm_value: name });
      toast("DeleteRestApi succeeded.");
      await navigate("api_gateway");

    } else if (action === "inspect-http-routes") {
      const r = await requestJson(`/api/services/api_gateway/http/${encodeURIComponent(button.dataset.apiId)}/routes`);
      showDetail("HTTP API Routes", r.routes, [["key","Route"],["target","Integration"],["authorization_type","Authorization"]]);

    } else if (action === "delete-http-api") {
      const name = button.dataset.apiName;
      if (!await confirmExact("Delete HTTP API", "Removes only the API created in this session.", name, "Delete API")) return;
      await postJson("/api/services/api_gateway/http/delete", { api_id: button.dataset.apiId, name, confirmed: true, confirm_value: name });
      toast("DeleteApi succeeded.");
      await navigate("api_gateway");

    } else if (action === "inspect-log-group") {
      const r = await requestJson(`/api/services/cloudwatch/streams?group=${encodeURIComponent(button.dataset.group)}`);
      if (!r.streams.length) {
        showDetail("Log Streams", [], [["name","Stream"]]);
        return;
      }
      const pane = document.getElementById("detail-pane");
      if (!pane) return;
      pane.innerHTML = "";
      const streamRows = r.streams.map((s) =>
        el("div", { class: "action-list-row" },
          el("span", { class: "mono" }, s.name),
          el("button", { class: "secondary-button", "data-action": "read-log-stream", "data-group": button.dataset.group, "data-stream": s.name }, "Read events")
        )
      );
      pane.appendChild(buildPanel(`Streams in ${button.dataset.group}`, null, el("div", { class: "action-list" }, ...streamRows)));

    } else if (action === "read-log-stream") {
      const query = new URLSearchParams({ group: button.dataset.group, stream: button.dataset.stream });
      const r = await requestJson(`/api/services/cloudwatch/events?${query}`);
      showDetail("Log Events", r.events, [["timestamp","Timestamp"],["message","Message"]]);

    } else if (action === "delete-log-group") {
      const group = button.dataset.group;
      if (!await confirmExact("Delete CloudWatch Log Group", "Removes the log group and all of its streams and events.", group, "Delete log group")) return;
      await postJson("/api/services/cloudwatch/log-groups/delete", {
        group_name: group,
        confirmed: true,
        confirm_value: group,
      });
      toast("DeleteLogGroup succeeded.");
      await navigate("cloudwatch");

    } else if (action === "ec2-start") {
      await postJson("/api/services/ec2/instances/start", { instance_ids: [button.dataset.instanceId] });
      toast("StartInstances succeeded.");
      await navigate("ec2");

    } else if (action === "ec2-stop") {
      await postJson("/api/services/ec2/instances/stop", { instance_ids: [button.dataset.instanceId] });
      toast("StopInstances succeeded.");
      await navigate("ec2");

    } else if (action === "ec2-connect") {
      await openConnectDialog(button.dataset.instanceId);

    } else if (action === "ec2-reboot") {
      await postJson("/api/services/ec2/instances/reboot", { instance_ids: [button.dataset.instanceId] });
      toast("RebootInstances succeeded.");
      await navigate("ec2");

    } else if (action === "delete-key-pair") {
      const name = button.dataset.keyName;
      if (!await confirmExact("Delete Key Pair", "Removes only the key pair created in this session. Running instances keep their injected key.", name, "Delete key pair")) return;
      await postJson("/api/services/ec2/key-pairs/delete", { name, confirmed: true, confirm_value: name });
      toast("DeleteKeyPair succeeded.");
      await navigate("ec2");

    } else if (action === "ec2-terminate") {
      const name = button.dataset.instanceName;
      if (!await confirmExact("Terminate EC2 Instance", "Removes the Docker-backed instance created in this session.", name, "Terminate")) return;
      await postJson("/api/services/ec2/instances/terminate", { instance_ids: [button.dataset.instanceId] });
      toast("TerminateInstances succeeded.");
      await navigate("ec2");

    } else if (action === "delete-function") {
      const name = button.dataset.function;
      if (!await confirmExact("Delete Lambda Function", "Removes only the function created in this session.", name, "Delete function")) return;
      await postJson("/api/services/lambda/functions/delete", { name, confirmed: true, confirm_value: name });
      toast("DeleteFunction succeeded.");
      await navigate("lambda");
    } else if (action === "refresh-metric-graph") {
      if (!metricGraphState) return;
      const output = document.getElementById("cw-graph-output");
      if (output) output.innerHTML = `<div class="initial-state"><span>Refreshing metric data…</span></div>`;
      try {
        await fetchMetricStatistics(metricGraphState);
      } catch {
        // fetchMetricStatistics renders its own error band
      }
      return;
    }

  } catch (error) {
    toast(`${error.code || "Error"}: ${error.message}`, true);
  } finally {
    button.disabled = false;
  }
}

/* =============================================================
   EC2 CONNECT DIALOG
   ============================================================= */

function downloadTextFile(filename, content) {
  const url = URL.createObjectURL(new Blob([content], { type: "application/x-pem-file" }));
  const link = el("a", { href: url, download: filename });
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const connectState = { instanceId: null, cwd: "/", history: [], historyIndex: 0, busy: false };

function copyButton(text) {
  const btn = el("button", { class: "secondary-button copy-button", type: "button" }, "Copy");
  btn.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(text); toast("Copied to clipboard."); }
    catch { toast("Clipboard unavailable; select the text manually.", true); }
  });
  return btn;
}

function commandRow(label, command) {
  return el("div", { class: "connect-command" },
    el("span", { class: "connect-command-label" }, label),
    el("div", { class: "connect-command-line" }, el("code", {}, command), copyButton(command))
  );
}

function setConnectTab(tab) {
  document.querySelectorAll("#connect-dialog [data-connect-tab]").forEach((b) => {
    const active = b.dataset.connectTab === tab;
    b.classList.toggle("is-active", active);
    b.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll("#connect-dialog [data-connect-panel]").forEach((p) => {
    p.hidden = p.dataset.connectPanel !== tab;
  });
  if (tab === "terminal") document.getElementById("terminal-input").focus();
}

function terminalPrompt() {
  return `root@${connectState.instanceId}:${connectState.cwd}#`;
}

function appendTerminal(text, cls = "") {
  const out = document.getElementById("terminal-output");
  out.appendChild(el("span", { class: cls }, text));
  out.scrollTop = out.scrollHeight;
}

function renderSshPanel(info) {
  const panelEl = document.getElementById("connect-ssh-panel");
  panelEl.innerHTML = "";
  const ssh = info.ssh;
  const keyFile = ssh.key_name ? `${ssh.key_name}.pem` : "<key-pair>.pem";
  if (!ssh.key_name) {
    panelEl.appendChild(el("div", { class: "warn-band" },
      el("strong", {}, "No key pair"),
      el("span", {}, " This instance was launched without a key pair, so no SSH key was injected. Launch a new instance with a key pair (and OpenSSH install enabled) to use SSH, or use the browser terminal.")
    ));
  }
  panelEl.appendChild(el("p", { class: "connect-note" },
    "Floci publishes the instance's SSH port (container port 22) on the Docker host. Connect as root with the private key downloaded when the key pair was created."
  ));
  panelEl.appendChild(commandRow("1. Restrict key permissions", `chmod 400 ${keyFile}`));
  panelEl.appendChild(commandRow("2. Connect", ssh.command));
  if (!ssh.port) {
    const container = ssh.container || `$(docker ps -q --filter label=io.floci.resource-id=${info.instance.id})`;
    panelEl.appendChild(el("div", { class: "info-band" },
      "The dashboard could not read the SSH host port from Docker (Docker CLI missing or needs sudo). Find it with the command below, then replace <ssh-port>."
    ));
    panelEl.appendChild(commandRow("Find the SSH port", `sudo docker port ${container} 22`));
  }
  const execTarget = ssh.container || `$(sudo docker ps -q --filter label=io.floci.resource-id=${info.instance.id})`;
  panelEl.appendChild(commandRow("Alternative: shell via Docker", `sudo docker exec -it ${execTarget} /bin/sh`));
  panelEl.appendChild(el("p", { class: "connect-note" },
    "OpenSSH is installed by user data at launch; on a fresh instance it can take a minute before sshd accepts connections."
  ));
}

async function openConnectDialog(instanceId) {
  const info = await requestJson(`/api/services/ec2/instances/${encodeURIComponent(instanceId)}/connect`);
  const dialog = document.getElementById("connect-dialog");
  connectState.instanceId = instanceId;
  connectState.cwd = "/";
  connectState.busy = false;
  document.getElementById("connect-title").textContent = `Connect to ${info.instance.name || instanceId}`;
  document.getElementById("connect-subtitle").textContent =
    [instanceId, info.instance.state, info.instance.private_ip, info.instance.image_id].filter(Boolean).join(" · ");
  const out = document.getElementById("terminal-output");
  out.innerHTML = "";
  appendTerminal(`LocalCloud browser terminal — commands run through SSM Run Command (AWS-RunShellScript).\nEach command runs non-interactively; cd is remembered. Interactive programs (vim, top) are not supported.\n\n`, "terminal-muted");
  document.getElementById("terminal-prompt").textContent = terminalPrompt();
  renderSshPanel(info);
  setConnectTab("terminal");
  if (!dialog.open) dialog.showModal();
  document.getElementById("terminal-input").focus();
}

async function runTerminalCommand(command) {
  if (connectState.busy) return;
  const input = document.getElementById("terminal-input");
  appendTerminal(`${terminalPrompt()} ${command}\n`, "terminal-command");
  if (command === "clear") {
    document.getElementById("terminal-output").innerHTML = "";
    return;
  }
  connectState.busy = true;
  input.disabled = true;
  try {
    const { result } = await postJson("/api/services/ec2/instances/command", {
      instance_id: connectState.instanceId, command, cwd: connectState.cwd,
    });
    if (result.stdout) appendTerminal(result.stdout.endsWith("\n") ? result.stdout : `${result.stdout}\n`);
    if (result.stderr) appendTerminal(result.stderr.endsWith("\n") ? result.stderr : `${result.stderr}\n`, "terminal-error");
    if (!result.finished) appendTerminal(`[command still running after 30s — status ${result.status}, command ID ${result.command_id}]\n`, "terminal-muted");
    else if (result.exit_code) appendTerminal(`[exit ${result.exit_code}]\n`, "terminal-muted");
    connectState.cwd = result.cwd || connectState.cwd;
  } catch (error) {
    appendTerminal(`${error.code || "Error"}: ${error.message}\n`, "terminal-error");
  } finally {
    connectState.busy = false;
    input.disabled = false;
    document.getElementById("terminal-prompt").textContent = terminalPrompt();
    input.focus();
  }
}

document.getElementById("terminal-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = document.getElementById("terminal-input");
  const command = input.value.trim();
  if (!command) return;
  connectState.history.push(command);
  connectState.historyIndex = connectState.history.length;
  input.value = "";
  runTerminalCommand(command);
});

document.getElementById("terminal-input").addEventListener("keydown", (e) => {
  const input = e.target;
  if (e.key === "ArrowUp" && connectState.historyIndex > 0) {
    e.preventDefault();
    connectState.historyIndex -= 1;
    input.value = connectState.history[connectState.historyIndex];
  } else if (e.key === "ArrowDown") {
    e.preventDefault();
    connectState.historyIndex = Math.min(connectState.history.length, connectState.historyIndex + 1);
    input.value = connectState.history[connectState.historyIndex] || "";
  }
});

document.querySelectorAll("#connect-dialog [data-connect-tab]").forEach((b) =>
  b.addEventListener("click", () => setConnectTab(b.dataset.connectTab))
);
document.getElementById("connect-close").addEventListener("click", () => document.getElementById("connect-dialog").close());

/* =============================================================
   NAVIGATION
   ============================================================= */

async function navigate(view) {
  if (!serviceTitles[view]) view = "overview";
  currentView = view;

  // Sync nav items
  document.querySelectorAll(".nav-item").forEach((item) => {
    const active = item.dataset.view === view;
    item.classList.toggle("is-active", active);
    if (active) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");
  });

  // Update breadcrumb
  const crumbs = serviceBreadcrumbs[view] || [serviceTitles[view]];
  if (breadcrumb) breadcrumb.textContent = crumbs[crumbs.length - 1] || serviceTitles[view];

  // Update URL without reload
  history.replaceState(null, "", `#${view}`);

  // Close mobile sidebar
  closeSidebar();

  // Render
  if (view === "overview") await renderOverview();
  else await renderServicePage(view);

  // Focus main for accessibility
  pageContent.focus({ preventScroll: true });
}

/* =============================================================
   SIDEBAR MOBILE TOGGLE
   ============================================================= */

function openSidebar() {
  document.getElementById("sidebar").classList.add("is-open");
  document.getElementById("menu-toggle").setAttribute("aria-expanded", "true");
  document.getElementById("sidebar-overlay").classList.add("is-visible");
  document.getElementById("sidebar-overlay").setAttribute("aria-hidden", "false");
}

function closeSidebar() {
  document.getElementById("sidebar").classList.remove("is-open");
  document.getElementById("menu-toggle").setAttribute("aria-expanded", "false");
  document.getElementById("sidebar-overlay").classList.remove("is-visible");
  document.getElementById("sidebar-overlay").setAttribute("aria-hidden", "true");
}

document.getElementById("menu-toggle").addEventListener("click", () => {
  const isOpen = document.getElementById("sidebar").classList.contains("is-open");
  if (isOpen) closeSidebar(); else openSidebar();
});
document.getElementById("sidebar-overlay").addEventListener("click", closeSidebar);

/* =============================================================
   COMMAND PALETTE
   ============================================================= */

const palette = document.getElementById("cmd-palette");
const paletteInput = document.getElementById("cmd-palette-input");
const paletteResults = document.getElementById("cmd-palette-results");

const paletteItems = [
  { label: "Dashboard (Overview)", icon: "icon-overview", view: "overview" },
  { label: "Lambda Functions", icon: "icon-lambda", view: "lambda" },
  { label: "EC2 Instances", icon: "icon-ec2", view: "ec2" },
  { label: "S3 Storage", icon: "icon-s3", view: "s3" },
  { label: "DynamoDB", icon: "icon-dynamodb", view: "dynamodb" },
  { label: "SQS Queues", icon: "icon-sqs", view: "sqs" },
  { label: "SNS Topics", icon: "icon-sns", view: "sns" },
  { label: "API Gateway", icon: "icon-api_gateway", view: "api_gateway" },
  { label: "CloudWatch", icon: "icon-cloudwatch", view: "cloudwatch" },
];

let _palActiveIdx = -1;

function renderPaletteItems(filter = "") {
  paletteResults.innerHTML = "";
  const filtered = paletteItems.filter((item) => item.label.toLowerCase().includes(filter.toLowerCase()));
  _palActiveIdx = -1;

  if (!filtered.length) {
    paletteResults.appendChild(el("div", { class: "cmd-palette-item", style: "color:var(--text-faint)" }, "No results found"));
    return;
  }

  const section = el("div", { class: "cmd-palette-section" },
    el("div", { class: "cmd-palette-section-label" }, "Navigate to service")
  );
  filtered.forEach((item, i) => {
    const row = el("div", {
      class: "cmd-palette-item",
      role: "option",
      "aria-selected": "false",
      tabindex: "-1",
      "data-view": item.view,
    },
      svgIcon(item.icon),
      el("span", {}, item.label)
    );
    row.addEventListener("click", () => {
      closePalette();
      navigate(item.view);
    });
    section.appendChild(row);
  });
  paletteResults.appendChild(section);
}

function openPalette() {
  palette.classList.remove("is-hidden");
  palette.removeAttribute("hidden");
  paletteInput.value = "";
  renderPaletteItems();
  requestAnimationFrame(() => paletteInput.focus());
}

function closePalette() {
  palette.classList.add("is-hidden");
}

function movePaletteSelection(delta) {
  const items = paletteResults.querySelectorAll(".cmd-palette-item[data-view]");
  if (!items.length) return;
  _palActiveIdx = Math.max(0, Math.min(items.length - 1, _palActiveIdx + delta));
  items.forEach((item, i) => {
    item.classList.toggle("is-active", i === _palActiveIdx);
    item.setAttribute("aria-selected", String(i === _palActiveIdx));
  });
}

paletteInput.addEventListener("input", () => renderPaletteItems(paletteInput.value));
paletteInput.addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown") { e.preventDefault(); movePaletteSelection(1); }
  else if (e.key === "ArrowUp") { e.preventDefault(); movePaletteSelection(-1); }
  else if (e.key === "Enter") {
    const active = paletteResults.querySelector(".cmd-palette-item.is-active[data-view]");
    if (active) { closePalette(); navigate(active.dataset.view); }
  } else if (e.key === "Escape") { closePalette(); }
});

palette.addEventListener("click", (e) => {
  // Close if clicking outside the box
  if (!document.getElementById("cmd-palette-box").contains(e.target)) closePalette();
});

// Ctrl+K / Cmd+K shortcut
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "k") {
    // Don't intercept if a form input is focused (except search trigger area)
    if (document.activeElement.tagName === "INPUT" && document.activeElement !== paletteInput) return;
    if (document.activeElement.tagName === "TEXTAREA") return;
    e.preventDefault();
    palette.classList.contains("is-hidden") ? openPalette() : closePalette();
  }
  if (e.key === "Escape" && !palette.classList.contains("is-hidden")) closePalette();
});

document.getElementById("search-trigger").addEventListener("click", openPalette);

/* =============================================================
   EVENT WIRING
   ============================================================= */

// Nav clicks
document.addEventListener("click", (e) => {
  const link = e.target.closest("[data-view]");
  if (!link) return;
  // Ignore palette items — they handle their own click
  if (link.closest("#cmd-palette")) return;
  e.preventDefault();
  navigate(link.dataset.view);
});

// Form submits
pageContent.addEventListener("submit", handleFormSubmit);
// Action clicks
pageContent.addEventListener("click", handleActionClick);

// Refresh button
document.getElementById("refresh-button").addEventListener("click", () => {
  refreshHealth();
  navigate(currentView);
});

// CloudWatch time-range presets fill the ISO inputs
pageContent.addEventListener("change", (e) => {
  const select = e.target.closest("select[name='time_range']");
  if (!select) return;
  const form = select.closest(".action-form");
  if (!form || select.value === "custom") return;
  const range = presetTimeRange(select.value);
  const startInput = form.elements.start_time;
  const endInput = form.elements.end_time;
  if (startInput) startInput.value = range.start;
  if (endInput) endInput.value = range.end;
});

// Hash navigation
window.addEventListener("hashchange", () => navigate(location.hash.slice(1) || "overview"));

/* =============================================================
   INIT
   ============================================================= */

initTheme();
requestJson("/api/catalog").then((v) => { catalog = v; }).catch(() => {});
refreshHealth();
navigate(location.hash.slice(1) || "overview");