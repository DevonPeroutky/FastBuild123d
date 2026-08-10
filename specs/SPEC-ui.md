# FastBuild123 Parametric UI — MVP Spec

## 0. Decision record

**Build our own thin web host on `three-cad-viewer` (Option B)** rather than piggybacking on ocp-vscode's standalone viewer (Option A, bdbox's approach) or forking YACV/CQ-editor.

Rationale:
- The mesh cache (SPEC-caching.md §3.4) requires owning the tessellation call site; behind ocp-vscode's `show()` we can't inject cached meshes or do per-part scene updates.
- The slider loop's latency semantics (debounce, last-good model, status overlays) need direct control of the viewer protocol; an iframe over ocp_vscode's undocumented internals is fork-level fragility without fork-level control.
- `three-cad-viewer` (MIT, npm) provides the hard 80% — rendering, nav tree, clipping, measurement, camera presets — as an embeddable component whose input format is exactly what `ocp_tessellate` (Apache-2.0) emits. We write only the shell: slider panel, websocket, layout.

Hedge: our Python side produces standard `ocp_tessellate` output, so an optional "send to ocp-vscode" adapter later is small.

## 1. Goal

A local web app: `fastbuild view model.py` opens a browser page with auto-generated parameter controls on the left and a live 3D view on the right. Dragging a slider or saving the file updates the model in place — no page reload, no camera reset.

**Non-goals for MVP:** multi-user, remote serving, in-browser Python, assembly mating UI, export UI, part-library browsing.

## 2. Success criteria

1. `fastbuild view fastbuild/examples/enclosure.py` → browser page with sliders derived from the `@part` signature; no per-model UI code.
2. Slider drag on the reference model (SPEC-caching §2) re-renders within cache-predicted time + ≤150ms UI/transport overhead; previously-seen values render in ≤150ms end-to-end.
3. Editing the file and saving updates the view without page reload; camera pose, tree state, and clipping planes survive every update.
4. A model that raises keeps the last-good geometry on screen with a visible error banner + traceback; fixing the file recovers without restart.

## 3. Architecture

```
┌─ browser ──────────────────────────────────────────┐
│  param panel (vanilla JS)   │  three-cad-viewer    │
│  sliders/checkboxes/selects │  (vendored npm build)│
└──────────────┬──────────────┴──────────▲───────────┘
               │ websocket (JSON)        │
┌─ fastbuild server (one process) ───────┴───────────┐
│ FastAPI + uvicorn: static page + /ws               │
│ harness: exec model file, registry, AST index      │
│ rebuild loop: watchfiles + param events → build    │
│ caches: part cache + mesh cache (SPEC-caching)     │
│ ocp_tessellate: Part → viewer shape JSON           │
└────────────────────────────────────────────────────┘
```

One warm process. `build123d`/OCP imported once at startup; per rebuild only the user's file is re-executed (`compile` + `exec` into a fresh namespace — same harness that feeds the caching layer its AST/source segments). In-process builds for MVP (subprocess/forkserver isolation punted, consistent with SPEC-caching §4).

## 4. Decorator & parameter model

```python
from fastbuild import App, Range
from typing import Annotated, Literal
from build123d import Part

app = App()

@app.part()
def enclosure(
    width:  Annotated[float, Range(20, 200, step=1, unit="mm")] = 80,
    wall:   Annotated[float, Range(0.8, 5, step=0.1, unit="mm")] = 2,
    ribs:   Annotated[int, Range(0, 8)] = 4,
    vented: bool = False,
    style:  Literal["rounded", "chamfered"] = "rounded",
) -> Part: ...
```

- `@app.part()` = registration + signature introspection + cache wiring (one decorator, shared with SPEC-caching). The function stays an ordinary callable.
- Widget mapping (MVP): `float`/`int` + `Range` → slider (`step` defaults: int → 1, float → (max−min)/100); `float`/`int` without `Range` → free-spinning number input; `bool` → checkbox; `Literal[...]`/`Enum` → dropdown. Anything else → shown read-only at default with a "unsupported parameter type" note (never a crash).
- `Group("Label")` in `Annotated` metadata places a parameter under a named collapsible section. Panel order is derived server-side (`panel_order`): ungrouped params first in signature order, then each group's members contiguously, groups sequenced by first appearance. Empty/blank labels are a decoration-time error.
- Every parameter must have a default (error at decoration time otherwise) — defaults are the initial build.
- `Range.step` is the slider quantum; the UI only ever emits quantized values, which is what keeps float cache keys stable (SPEC-caching §3.2.2).
- Multiple `@part` functions in one file: all registered; a part selector dropdown sits above the param panel; one part displayed at a time (its params only). First-registered is the initial selection.

## 5. Rebuild loop

Triggers, coalesced through a single build queue (at most one build running, one pending; newest pending wins):

1. **Param change** (websocket): rebuild on slider *release*; while dragging, rebuild at most every 200ms (trailing-edge debounce). Checkbox/dropdown: immediate.
2. **File save** (`watchfiles`, 100ms debounce): re-parse AST → per-function source segments re-hashed → only functions whose segments changed get new cache keys (SPEC-caching §3.5). Then rebuild the selected part with current UI param values. Params panel re-derives from the new signature: added params appear at defaults, removed ones drop, unchanged ones **keep their current UI values**.

Build execution: call the selected part function with current param values → cache does its work → tessellate result via `ocp_tessellate` (mesh cache keyed by provenance, SPEC-caching §3.4) → push to client.

Failure: exception → `build_status: error` with traceback string; viewer keeps last-good scene. Success → `model_update` + `build_status: ok` with timing breakdown (build ms, tessellate ms, cache hits/misses — the instrumentation from SPEC-caching §3.3, surfaced in a small stats line under the panel).

## 6. Websocket protocol (JSON, versioned envelope)

All messages: `{"v": 1, "type": ..., ...}`.

Server → client:
- `init {parts: [{name, params: [{name, kind, group, min, max, step, unit, options, default, value}]}], selected}` — on connect and after file save changes signatures. `params` arrives in panel display order.
- `model_update {part, shapes}` — `shapes` is the `ocp_tessellate` output tree, with each shape node carrying its provenance key as `id`. MVP pushes the full tree each build; the per-shape keys are the hook for differential push later (punted).
- `build_status {state: building|ok|error, error?, timings?, cache?}`.

Client → server:
- `set_param {part, name, value, final: bool}` — `final: false` while dragging (debounced), `true` on release.
- `select_part {part}`.

Rules: client never reloads the page; viewer instance is created once and updated via its dynamic-update API; camera/selection/clipping/tree state live client-side and are never touched by updates.

## 7. Frontend

- **No framework.** One static HTML page, one hand-written JS module for the panel + websocket + status banner, one CSS file. Layout: fixed-width left panel, viewer fills the rest.
- **`three-cad-viewer` vendored**: exact npm version pinned, built bundle committed under `fastbuild/static/vendor/` (no node toolchain required to install or run fastbuild). Its own toolbar/tree/clipping UI used as-is.
- Reference implementations for the init/update glue: ocp-vscode's standalone viewer page and jupyter-cadquery (both drive the same component).
- Status banner states: building (subtle spinner, viewer stays interactive), error (red banner, expandable traceback, last-good scene shown), ok (auto-hide; stats line updates).

## 8. CLI

- `fastbuild view <file.py> [--port 8123] [--no-open]` — start server, open browser.
- `fastbuild export <file.py> <part> [--param name=value ...] -o out.step|stl` — same harness/registry, no server; validates values against `Range`. (One command, because it falls out of the registry for free and makes the tool useful headless.)

## 9. MVP / punt split

| In MVP | Punted (with hook left in place) |
|---|---|
| One-file model, all `@part`s registered, single-part display + selector | Multi-part composite scenes; Collections/`include()` (registry API already collection-shaped) |
| Sliders/checkbox/dropdown from signatures | Presets, param persistence across sessions, URL-encoded param state |
| Full-tree `model_update` per build | Differential per-shape push (shape `id` = provenance key already in protocol); binary frames |
| Warm in-process rebuilds | forkserver isolation + hung-build cancellation (queue API already async) |
| Save-triggered reload + per-function invalidation | Draft/LOD mode during drag; progressive per-part streaming |
| Last-good model + error banner | Inline "which function failed" source mapping |
| `export` CLI | Export buttons in UI; STEP/STL options panel |
| — | Joints visualization, mating UI, VS Code adapter, static-site sharing |

## 10. Test plan

- **Introspection**: each annotation form → expected widget schema; missing default → decoration error; unsupported type → read-only, no crash.
- **Protocol**: golden-file JSON for `init`/`model_update`/`build_status`; envelope version present everywhere.
- **Loop semantics** (pytest, headless — drive the server via a websocket test client, no browser): drag storm coalesces to ≤1 build per 200ms window with newest value winning; `final:true` always builds; save during build queues exactly one rebuild.
- **Failure/recovery**: raising model → error status + last-good retained (server-side scene unchanged); fixed file → ok.
- **Signature migration**: add/remove/rename param on save → panel schema correct, surviving params keep values.
- **E2E smoke** (Playwright, one test): open page, move a slider, assert viewer canvas updates and no page reload occurred.
- **Perf**: reference model asserts §2 criterion 2 numbers with generous CI margins.

## 11. Risks & open questions

1. **three-cad-viewer API is documented-by-example, not spec'd.** Mitigation: pin + vendor; budget the first milestone purely for a spike that renders one static `ocp_tessellate` payload; crib from ocp-vscode standalone page.
2. **Viewer-state preservation across `model_update`** needs verification — if the component resets camera on scene replacement, we save/restore pose around updates (its API exposes camera state).
3. **`ocp_tessellate` output shape/version coupling** to the vendored viewer version — pin both together; golden-file test catches drift on upgrade.

## 12. Build order

1. Spike: static page + vendored viewer renders a hand-tessellated Box (kills risk #1).
2. Harness + `@part` registry + introspection → `init` message.
3. FastAPI/websocket + full rebuild on `set_param` (no cache yet) → first end-to-end slider.
4. watchfiles + re-exec + signature migration.
5. Integrate SPEC-caching (part + mesh caches) → hit §2 latency targets.
6. Error handling, status banner, stats line, `export` CLI, tests.
