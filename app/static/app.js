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
   CONNECTION / HEALTH
   ============================================================= */

function updateConnection(health) {
  connPill.classList.remove("is-checking", "is-connected", "is-disconnected");
  if (health.connected) {
    connPill.classList.add("is-connected");
    connText.textContent = `Floci ${health.version || "connected"}`;
    sidebarVer.textContent = `Floci ${health.version || ""}`;
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
  if (key === "ec2")     return [{ title: "Instances",  rows: data.instances,  columns: [["id","Instance ID",true],["name","Name"],["state","State"],["instance_type","Type"],["image_id","AMI"],["launch_time","Launched"]] }];
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
      `<div class="action-list-buttons"><button class="secondary-button" data-action="list-objects" data-bucket="${escapeHtml(b.name)}">List objects</button></div></div>`
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
      `<button class="secondary-button" data-action="inspect-table" data-table="${escapeHtml(t.name)}">Inspect</button></div>`
    ).join("");
    return `
      ${panel("Create Table", "PAY_PER_REQUEST · String pk",
        `<form class="action-form" data-action="create-table">
          <label>Table name<input name="name" required minlength="3" maxlength="255" pattern="[A-Za-z0-9_.\\-]+" placeholder="localcloud-demo-table" autocomplete="off"></label>
          <span class="form-note">Partition key: <code>pk</code> (String). On-demand billing.</span>
          <button class="primary-button" type="submit">Create table</button>
        </form>`
      )}
      ${panel("Insert Item", "Session-owned tables only",
        `<form class="action-form" data-action="put-item">
          <label>Table<select name="table" required ${owned.length ? "" : "disabled"}>${opts}</select></label>
          <label>Partition key (pk)<input name="pk" required maxlength="256" placeholder="sample-001" autocomplete="off"></label>
          <label>Value<input name="value" required maxlength="4096" placeholder="Local test record" autocomplete="off"></label>
          ${!owned.length ? `<span class="form-note">Create a table this session to enable writes. Pre-existing tables are read-only here.</span>` : ""}
          <button class="primary-button" type="submit" ${owned.length ? "" : "disabled"}>Insert item</button>
        </form>`
      )}
      ${owned.length ? panel("Retrieve Item", "Consistent read",
        `<form class="action-form" data-action="get-item">
          <label>Table<select name="table" required>${opts}</select></label>
          <label>Partition key<input name="pk" required maxlength="256" autocomplete="off"></label>
          <button class="secondary-button" type="submit">Retrieve item</button>
        </form>`) : ""}
      ${data.tables.length
        ? `<section class="panel"><div class="panel-heading"><h3>Table Details</h3><span class="panel-meta">DescribeTable</span></div><div class="action-list">${tableRows}</div></section>`
        : ""}`;
  }

  if (key === "sqs") {
    const opts = selectOptions(data.queues, "url", "name");
    const queueRows = data.queues.map((q) =>
      `<div class="action-list-row"><span><strong>${escapeHtml(q.name)}</strong><small class="mono">${escapeHtml(q.url)}</small></span>` +
      `<button class="secondary-button" data-action="queue-attributes" data-queue="${escapeHtml(q.url)}">Attributes</button></div>`
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
      `<button class="secondary-button" data-action="inspect-topic" data-topic="${escapeHtml(t.arn)}">Subscriptions</button></div>`
    ).join("");
    return `
      ${panel("Create Topic", "Live Floci operation",
        `<form class="action-form" data-action="create-topic">
          <label>Topic name<input name="name" required maxlength="256" pattern="[A-Za-z0-9_\\-]+" placeholder="localcloud-demo-topic" autocomplete="off"></label>
          <button class="primary-button" type="submit">Create topic</button>
        </form>`
      )}
      ${panel("Publish Message", "Requires zero subscriptions — server validates before publish",
        `<div class="warn-band safety-note">Publishing is blocked if the selected topic has any subscriptions. The server re-checks immediately before publishing.</div>
        <form class="action-form" data-action="publish-message">
          <label>Topic<select name="topic_arn" required ${data.topics.length ? "" : "disabled"}>${opts}</select></label>
          <label>Message<textarea name="message" required maxlength="262144" rows="3" placeholder="Local test message"></textarea></label>
          <button class="primary-button" type="submit" ${data.topics.length ? "" : "disabled"}>Publish</button>
        </form>`
      )}
      ${data.topics.length
        ? `<section class="panel"><div class="panel-heading"><h3>Topic Subscriptions</h3><span class="panel-meta">ListSubscriptionsByTopic</span></div><div class="action-list">${topicRows}</div></section>`
        : ""}`;
  }

  if (key === "api_gateway") {
    const apis = data.rest.ok ? data.rest.apis : [];
    const apiRows = apis.map((api) =>
      `<div class="action-list-row"><span><strong>${escapeHtml(api.name)}</strong><small class="mono">${escapeHtml(api.id)}</small></span>` +
      `<div class="action-list-buttons"><button class="secondary-button" data-action="inspect-rest-api" data-api-id="${escapeHtml(api.id)}">Resources</button>` +
      `${api.console_owned ? `<button class="danger-button" data-action="delete-rest-api" data-api-id="${escapeHtml(api.id)}" data-api-name="${escapeHtml(api.name)}">Delete</button>` : ""}</div></div>`
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
      ${data.http.ok && data.http.apis.length
        ? `<div class="note-band">HTTP API inventory is read-only here; route inspection has not been validated.</div>`
        : ""}`;
  }

  if (key === "cloudwatch") {
    const groups = data.log_groups.ok ? data.log_groups.log_groups : [];
    const logRows = groups.map((g) =>
      `<div class="action-list-row"><span class="mono">${escapeHtml(g.name)}</span>` +
      `<button class="secondary-button" data-action="inspect-log-group" data-group="${escapeHtml(g.name)}">List streams</button></div>`
    ).join("");
    const metricsErr = data.metrics.ok ? "" : `<div class="error-band">CloudWatch Metrics request failed: ${escapeHtml(data.metrics.error.message)}</div>`;
    const logsErr    = data.log_groups.ok ? "" : `<div class="error-band">CloudWatch Logs request failed: ${escapeHtml(data.log_groups.error.message)}</div>`;
    return `${metricsErr}${logsErr}${groups.length
      ? `<section class="panel"><div class="panel-heading"><h3>Log Group Explorer</h3><span class="panel-meta">Read-only event inspection</span></div><div class="action-list">${logRows}</div></section>`
      : ""}`;
  }

  if (key === "lambda") {
    const fns = data.functions.map((fn) =>
      `<div class="action-list-row"><span><strong>${escapeHtml(fn.name)}</strong><small class="mono">${escapeHtml(fn.arn)}</small></span>` +
      `<button class="secondary-button" data-action="inspect-function" data-function="${escapeHtml(fn.name)}">Configuration</button></div>`
    ).join("");
    return fns
      ? `<section class="panel"><div class="panel-heading"><h3>Function Configuration</h3><span class="panel-meta">GetFunctionConfiguration — read only</span></div><div class="action-list">${fns}</div></section>`
      : "";
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
      result = await postJson("/api/services/dynamodb/items", values);
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
      if (!await confirmExact("Publish local message", "The server publishes only if this Floci topic has no subscriptions.", "PUBLISH", "Publish")) {
        if (submitBtn && !wasDisabled) submitBtn.disabled = false;
        return;
      }
      result = await postJson("/api/services/sns/publish", values);
    } else if (action === "create-rest-api") {
      result = await postJson("/api/services/api_gateway/rest", values);
    } else {
      if (submitBtn && !wasDisabled) submitBtn.disabled = false;
      return;
    }

    toast(`${actionLabel(action)} succeeded.`);
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
    "send-message":   "SendMessage",
    "create-topic":   "CreateTopic",
    "publish-message":"Publish",
    "create-rest-api":"CreateRestApi",
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

    } else if (action === "inspect-table") {
      const r = await requestJson(`/api/services/dynamodb/tables/${encodeURIComponent(button.dataset.table)}`);
      showDetail("Table Metadata", [r.table], Object.keys(r.table).map((n) => [n, n]));

    } else if (action === "inspect-function") {
      const r = await requestJson(`/api/services/lambda/functions/${encodeURIComponent(button.dataset.function)}`);
      showDetail("Lambda Configuration", [r.function], Object.keys(r.function).map((n) => [n, n]));

    } else if (action === "queue-attributes") {
      const r = await requestJson(`/api/services/sqs/attributes?queue_url=${encodeURIComponent(button.dataset.queue)}`);
      showDetail("Queue Attributes", Object.entries(r.attributes).map(([name, value]) => ({ name, value })), [["name","Attribute"],["value","Value"]]);

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

    } else if (action === "inspect-rest-api") {
      const r = await requestJson(`/api/services/api_gateway/rest/${encodeURIComponent(button.dataset.apiId)}/resources`);
      showDetail("REST API Resources", r.resources, [["path","Path"],["id","Resource ID"],["methods","Methods"]]);

    } else if (action === "delete-rest-api") {
      const name = button.dataset.apiName;
      if (!await confirmExact("Delete REST API", "Removes only the API created in this session.", name, "Delete API")) return;
      await postJson("/api/services/api_gateway/rest/delete", { api_id: button.dataset.apiId, name, confirmed: true, confirm_value: name });
      toast("DeleteRestApi succeeded.");
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
    }

  } catch (error) {
    toast(`${error.code || "Error"}: ${error.message}`, true);
  } finally {
    button.disabled = false;
  }
}

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

// Hash navigation
window.addEventListener("hashchange", () => navigate(location.hash.slice(1) || "overview"));

/* =============================================================
   INIT
   ============================================================= */

initTheme();
requestJson("/api/catalog").then((v) => { catalog = v; }).catch(() => {});
refreshHealth();
navigate(location.hash.slice(1) || "overview");