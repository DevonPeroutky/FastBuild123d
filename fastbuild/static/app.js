import {
  Display,
  Viewer,
  isInstancedFormat,
  decodeInstancedFormat,
} from "/static/vendor/three-cad-viewer.esm.min.js";

const PROTOCOL_VERSION = 1;

const el = {
  partSelect: document.getElementById("part-select"),
  params: document.getElementById("params"),
  status: document.getElementById("status"),
  building: document.getElementById("status-building"),
  error: document.getElementById("status-error"),
  traceback: document.getElementById("error-traceback"),
  statsLine: document.getElementById("stats-line"),
  cacheProgress: document.getElementById("cache-progress"),
  cacheProgressBar: document.getElementById("cache-progress-bar"),
  cacheProgressValue: document.getElementById("cache-progress-value"),
  viewerHost: document.getElementById("viewer-host"),
  cadView: document.getElementById("cad_view"),
};

const state = {
  ws: null,
  parts: [],
  selected: null,
  viewer: null,
  display: null,
  rendered: false,
  renderedGroupId: null,
  canvasSize: null,
  collapsedGroups: new Set(),
};

// --- viewer ------------------------------------------------------------

const renderOptions = {
  ambientIntensity: 1.0,
  directIntensity: 1.1,
  metalness: 0.3,
  roughness: 0.65,
  edgeColor: 0x707070,
  defaultOpacity: 0.5,
  normalLen: 0,
};

const TREE_WIDTH = 240;

// Pixels the viewer's own frame (margins, toolbar rows) adds around the canvas;
// zero until the Display has built its DOM.
function viewerChrome() {
  const frame = el.cadView.querySelector(".tcv_cad_viewer");
  const canvasBox = el.cadView.querySelector(".tcv_cad_view");
  if (!frame || !canvasBox) return { width: 0, height: 0 };
  const margin = getComputedStyle(frame);
  const outer = frame.getBoundingClientRect();
  const inner = canvasBox.getBoundingClientRect();
  return {
    width: outer.width + parseFloat(margin.marginLeft) + parseFloat(margin.marginRight) - inner.width,
    height: outer.height + parseFloat(margin.marginTop) + parseFloat(margin.marginBottom) - inner.height,
  };
}

function viewerSize() {
  const chrome = viewerChrome();
  return {
    cadWidth: Math.max(el.viewerHost.clientWidth - chrome.width, 400),
    height: Math.max(el.viewerHost.clientHeight - chrome.height, 300),
  };
}

function applyViewerSize(size) {
  const current = state.canvasSize;
  if (current && current.cadWidth === size.cadWidth && current.height === size.height) return;
  state.canvasSize = size;
  state.viewer.resizeCadView(size.cadWidth, TREE_WIDTH, size.height, true);
}

// Size the canvas to fill the host. Only valid once a model is rendered
// (resizeCadView touches the rendered camera).
function fitViewer() {
  applyViewerSize(viewerSize());
  // The toolbar wraps to a second row below ~740px, changing the chrome height.
  applyViewerSize(viewerSize());
}

function viewerOptions() {
  return { up: "Z", control: "trackball" };
}

// Display and Viewer each read these directly (Display applies no defaults, so
// an omitted tool flag hides that toolbar group); hand both the same object.
function displayOptions() {
  const { cadWidth, height } = viewerSize();
  return {
    cadWidth,
    height,
    treeWidth: TREE_WIDTH,
    theme: "dark",
    glass: true,
    tools: true,
    pinning: false,
    measureTools: true,
    selectTool: true,
    explodeTool: true,
    zscaleTool: false,
  };
}

function ensureViewer() {
  if (state.viewer) return;
  const options = displayOptions();
  state.display = new Display(el.cadView, options);
  // three-cad-viewer 5.0.2 folds toolbar groups behind "…" hover menus whenever
  // the canvas is narrower than this threshold (render, resize, and mouse-leave
  // all consult it). Zero keeps the full toolbar visible at every width; the
  // row wraps instead (app.css).
  state.display._widthThreshold = () => 0;
  state.viewer = new Viewer(state.display, { ...options, ...viewerOptions() }, () => {});
  window.addEventListener("resize", fitViewer);
}

function collectLeaves(node, out) {
  if (node.parts) {
    for (const child of node.parts) collectLeaves(child, out);
  } else if (node.shape) {
    out.set(node.id, node);
  }
  return out;
}

function fullRender(shapes) {
  const camera = state.rendered ? state.viewer.getCameraLocationSettings() : null;
  if (state.rendered) state.viewer.clear();
  state.viewer.render(shapes, renderOptions, viewerOptions());
  if (!state.rendered) fitViewer();
  state.rendered = true;
  state.renderedGroupId = shapes.id;
  state.renderedLeaves = collectLeaves(shapes, new Map());
  if (camera) {
    state.viewer.setCameraLocationSettings(
      camera.position, camera.quaternion, camera.target, camera.zoom, false,
    );
  }
}

function showShapes(payload) {
  ensureViewer();
  const shapes = isInstancedFormat(payload) ? decodeInstancedFormat(payload) : payload;
  if (!state.rendered || state.renderedGroupId !== shapes.id) {
    fullRender(shapes);
    return;
  }
  const next = collectLeaves(shapes, new Map());
  const prev = state.renderedLeaves;
  const samePaths =
    next.size === prev.size && [...next.keys()].every((id) => prev.has(id));
  if (!samePaths) {
    fullRender(shapes);
    return;
  }
  for (const [id, part] of next) {
    state.viewer.updatePart(id, part, { skipBounds: true });
  }
  state.viewer.updateBounds();
  state.renderedLeaves = next;
}

// --- param panel -------------------------------------------------------

function send(msg) {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify({ v: PROTOCOL_VERSION, ...msg }));
  }
}

function sendParam(name, value, final) {
  send({ type: "set_param", part: state.selected, name, value, final });
}

function formatNumber(value) {
  return Number.isInteger(value) ? String(value) : String(Math.round(value * 1000) / 1000);
}

function paramName(param) {
  const span = document.createElement("span");
  span.append(param.unit ? `${param.name} (${param.unit})` : param.name);
  if (param.description !== null) span.append(infoIcon(param));
  return span;
}

function infoIcon(param) {
  const icon = document.createElement("button");
  icon.type = "button";
  icon.className = "info-icon";
  icon.setAttribute("aria-label", param.description);
  icon.textContent = "\u24D8";
  const tip = document.createElement("span");
  tip.className = "info-tooltip";
  tip.textContent = param.description;
  // The tooltip is position: fixed so it can extend past the panel's edge;
  // anchor it to the icon's right just before CSS :hover/:focus reveals it.
  const place = () => {
    const rect = icon.getBoundingClientRect();
    tip.style.left = `${rect.right + 8}px`;
    tip.style.top = `${rect.top + rect.height / 2}px`;
  };
  icon.addEventListener("mouseenter", place);
  icon.addEventListener("focus", place);
  icon.append(tip);
  return icon;
}

function sliderWidget(param) {
  const wrap = document.createElement("div");
  wrap.className = "param";
  const label = document.createElement("div");
  label.className = "param-label";
  const valueSpan = document.createElement("span");
  valueSpan.className = "value";
  valueSpan.textContent = formatNumber(param.value);
  label.append(paramName(param), valueSpan);

  const input = document.createElement("input");
  input.type = "range";
  input.min = param.min;
  input.max = param.max;
  input.step = param.step;
  input.value = param.value;
  input.addEventListener("input", () => {
    valueSpan.textContent = formatNumber(Number(input.value));
    sendParam(param.name, Number(input.value), false);
  });
  input.addEventListener("change", () => {
    sendParam(param.name, Number(input.value), true);
  });
  wrap.append(label, input);
  return wrap;
}

function numberWidget(param) {
  const wrap = document.createElement("div");
  wrap.className = "param";
  const label = document.createElement("div");
  label.className = "param-label";
  label.append(paramName(param));
  const input = document.createElement("input");
  input.type = "number";
  input.value = param.value;
  input.addEventListener("change", () => {
    sendParam(param.name, Number(input.value), true);
  });
  wrap.append(label, input);
  return wrap;
}

function checkboxWidget(param) {
  const wrap = document.createElement("label");
  wrap.className = "param checkbox";
  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = param.value;
  input.addEventListener("change", () => {
    sendParam(param.name, input.checked, true);
  });
  wrap.append(input, paramName(param));
  return wrap;
}

function dropdownWidget(param) {
  const wrap = document.createElement("div");
  wrap.className = "param";
  const label = document.createElement("div");
  label.className = "param-label";
  label.append(paramName(param));
  const select = document.createElement("select");
  for (const option of param.options) {
    const opt = document.createElement("option");
    opt.value = option;
    opt.textContent = option;
    opt.selected = option === param.value;
    select.append(opt);
  }
  select.addEventListener("change", () => {
    sendParam(param.name, select.value, true);
  });
  wrap.append(label, select);
  return wrap;
}

function readonlyWidget(param) {
  const wrap = document.createElement("div");
  wrap.className = "param";
  const label = document.createElement("div");
  label.className = "param-label";
  label.append(paramName(param));
  const note = document.createElement("div");
  note.className = "readonly-note";
  note.textContent = `${param.value} — unsupported parameter type`;
  wrap.append(label, note);
  return wrap;
}

function widgetFor(param) {
  switch (param.kind) {
    case "slider": return sliderWidget(param);
    case "number": return numberWidget(param);
    case "checkbox": return checkboxWidget(param);
    case "dropdown": return dropdownWidget(param);
    default: return readonlyWidget(param);
  }
}

function groupContainer(label) {
  const details = document.createElement("details");
  details.className = "param-group";
  const key = `${state.selected}::${label}`;
  details.open = !state.collapsedGroups.has(key);
  const summary = document.createElement("summary");
  summary.textContent = label;
  details.append(summary);
  details.addEventListener("toggle", () => {
    if (details.open) state.collapsedGroups.delete(key);
    else state.collapsedGroups.add(key);
  });
  return details;
}

function renderPanel() {
  const part = state.parts.find((p) => p.name === state.selected);
  el.params.replaceChildren();
  if (!part) return;
  // Relies on the server's panel_order guarantee (ungrouped first, groups
  // contiguous); a repeated label here would emit a duplicate group.
  let group = null;
  for (const param of part.params) {
    if (param.group === null) {
      el.params.append(widgetFor(param));
    } else {
      if (group === null || group.label !== param.group) {
        group = { label: param.group, container: groupContainer(param.group) };
        el.params.append(group.container);
      }
      group.container.append(widgetFor(param));
    }
  }
}

function renderPartSelector() {
  el.partSelect.replaceChildren();
  for (const part of state.parts) {
    const opt = document.createElement("option");
    opt.value = part.name;
    opt.textContent = part.name;
    opt.selected = part.name === state.selected;
    el.partSelect.append(opt);
  }
  el.partSelect.hidden = state.parts.length < 2;
}

el.partSelect.addEventListener("change", () => {
  send({ type: "select_part", part: el.partSelect.value });
});

// --- status banner -----------------------------------------------------

function showStatus(msg) {
  switch (msg.state) {
    case "building":
      el.status.classList.remove("hidden");
      el.building.classList.remove("hidden");
      break;
    case "ok": {
      el.building.classList.add("hidden");
      el.error.classList.add("hidden");
      el.status.classList.add("hidden");
      const t = msg.timings || {};
      el.statsLine.textContent = `build ${t.build_ms}ms · tessellate ${t.tessellate_ms}ms`;
      showCacheProgress(msg.cache);
      break;
    }
    case "error":
      el.building.classList.add("hidden");
      el.status.classList.remove("hidden");
      el.error.classList.remove("hidden");
      el.traceback.textContent = msg.error;
      break;
  }
}

function showCacheProgress(cache) {
  if (cache?.hit_rate == null) {
    el.cacheProgress.classList.add("hidden");
    return;
  }
  const pct = Math.round(cache.hit_rate * 100);
  el.cacheProgress.classList.remove("hidden");
  el.cacheProgressBar.style.width = `${pct}%`;
  el.cacheProgressValue.textContent = `${pct}%`;
  el.cacheProgress.title =
    `${cache.rebuilt ?? "?"} of ${cache.stages ?? "?"} stages rebuilt · ` +
    `session: ${cache.hits ?? 0} hits / ${cache.misses ?? 0} misses`;
}

// --- websocket ---------------------------------------------------------

function handleMessage(msg) {
  if (msg.v !== PROTOCOL_VERSION) return;
  switch (msg.type) {
    case "init":
      state.parts = msg.parts;
      state.selected = msg.selected;
      renderPartSelector();
      renderPanel();
      break;
    case "model_update":
      showShapes(msg.shapes);
      break;
    case "build_status":
      showStatus(msg);
      break;
  }
}

function connect() {
  const ws = new WebSocket(`ws://${location.host}/ws`);
  state.ws = ws;
  ws.addEventListener("message", (event) => {
    handleMessage(JSON.parse(event.data));
  });
  ws.addEventListener("close", () => {
    setTimeout(connect, 1000);
  });
}

connect();
