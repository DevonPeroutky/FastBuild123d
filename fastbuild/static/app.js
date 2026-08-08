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
  stats: document.getElementById("stats"),
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

function viewerSize() {
  const w = el.viewerHost.clientWidth;
  const h = el.viewerHost.clientHeight;
  return { cadWidth: Math.max(w, 400), height: Math.max(h, 300) };
}

function viewerOptions() {
  return { up: "Z", control: "trackball" };
}

function ensureViewer() {
  if (state.viewer) return;
  const { cadWidth, height } = viewerSize();
  state.display = new Display(el.cadView, {
    cadWidth,
    height,
    theme: "dark",
    glass: true,
    tools: true,
    pinning: false,
  });
  state.viewer = new Viewer(state.display, viewerOptions(), () => {});
  window.addEventListener("resize", () => {
    const { cadWidth: w, height: h } = viewerSize();
    state.viewer.resizeCadView(w, 240, h, true);
  });
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

function sliderWidget(param) {
  const wrap = document.createElement("div");
  wrap.className = "param";
  const label = document.createElement("div");
  label.className = "param-label";
  const nameSpan = document.createElement("span");
  nameSpan.textContent = param.unit ? `${param.name} (${param.unit})` : param.name;
  const valueSpan = document.createElement("span");
  valueSpan.className = "value";
  valueSpan.textContent = formatNumber(param.value);
  label.append(nameSpan, valueSpan);

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
  label.textContent = param.unit ? `${param.name} (${param.unit})` : param.name;
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
  const span = document.createElement("span");
  span.textContent = param.name;
  wrap.append(input, span);
  return wrap;
}

function dropdownWidget(param) {
  const wrap = document.createElement("div");
  wrap.className = "param";
  const label = document.createElement("div");
  label.className = "param-label";
  label.textContent = param.name;
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
  label.textContent = param.name;
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

function renderPanel() {
  const part = state.parts.find((p) => p.name === state.selected);
  el.params.replaceChildren();
  if (!part) return;
  for (const param of part.params) {
    el.params.append(widgetFor(param));
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
      const pieces = [`build ${t.build_ms}ms`, `tessellate ${t.tessellate_ms}ms`];
      if (msg.cache) {
        pieces.push(`cache ${msg.cache.hits ?? 0} hits / ${msg.cache.misses ?? 0} misses`);
      }
      el.stats.textContent = pieces.join(" · ");
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
