---
name: fastbuild123
description: Build parametric CAD web UIs from build123d model functions with FastBuild123's @app.part() decorator. Use when writing, editing, caching, or exporting FastBuild123/build123d parts, or wiring parameter sliders, dropdowns, and groups.
---

# FastBuild123

FastBuild123 turns [build123d](https://github.com/gumyr/build123d) model functions
into live, interactive parametric web UIs. Decorate a typed function with
`@app.part()` and run `fastbuild view model.py`: the type-annotated signature *is*
the UI definition — no manifest, no schema files. A provenance-keyed cache memoizes
geometry and meshes per function, so dragging a slider only recomputes the parts of
the model that parameter actually affects.

This doc covers the FastBuild123 API and its rules. For build123d itself (algebra
operations, selectors, `Part`/`Compound`), use build123d's own documentation.

## Minimal model file

```python
from typing import Annotated, Literal
from build123d import Box, Part
from fastbuild import App, Range

app = App()

@app.part()
def enclosure(
    width:  Annotated[float, Range(20, 200, step=1, unit="mm")] = 80,
    wall:   Annotated[float, Range(0.8, 5, step=0.1, unit="mm")] = 2,
    vented: bool = False,
    style:  Literal["rounded", "chamfered"] = "rounded",
) -> Part:
    ...
```

Rules:

- Instantiate `App()` at module level; the viewer discovers the first `App` in the file.
- Every parameter must have a default — defaults define the initial build.
- Return a build123d `Part` or `Compound`.
- Multiple `@app.part()` functions in one file get a part selector in the UI.
- Run with `fastbuild view model.py`; saving the file hot-reloads the model while
  keeping current slider values, camera pose, and tree state.

## Parameter → widget mapping

| Annotation | Widget |
| --- | --- |
| `Annotated[float \| int, Range(min, max, step=None, unit=None)]` | Slider |
| `Annotated[float, Field(ge=..., le=...)]` (pydantic) | Slider |
| Bare `int` / `float` | Number input |
| `bool` | Checkbox |
| `Literal[...]` | Dropdown |
| `enum.Enum` subclass | Dropdown (options keyed by member *name*) |
| Anything else (e.g. `str`) | Read-only display; always passed its default |

- `Range` requires `min < max`. `step=None` defaults to 1 for `int` and
  `(max - min) / 100` for `float`. `unit` is a display-only suffix.
- Slider values from the UI are clamped to bounds and snapped to the step grid.
- Add `Group("Label")` inside `Annotated[...]` to place a parameter under a named
  collapsible section: `Annotated[float, Range(0, 10), Group("Fins")] = 3`.
  Ungrouped parameters render first in signature order; groups follow in order of
  first appearance, each group's members contiguous.
- Add `Desc("...")` inside `Annotated[...]` to show an info icon beside the
  parameter's label that reveals the description as a tooltip on hover:
  `Annotated[bool, Desc("Match a D-shaped shaft")] = True`. Keep it to a sentence —
  long text covers the widgets below it while hovered.

## Decoration-time errors

`@app.part()` raises `ParamError` (`from fastbuild import ParamError`) immediately —
not at build time — for signatures the UI cannot represent:

- A parameter without a default value.
- Exclusive bounds (`Field(gt=...)` / `Field(lt=...)`) — only inclusive `ge`/`le` work.
- One-sided bounds (`ge` without `le`, or vice versa) — a slider needs both ends.
- `Range` with `min >= max`, or `step <= 0`.
- A default that doesn't match the annotation (wrong type, or not among the
  `Literal`/`Enum` options).
- A blank `Group("")` label.
- A blank `Desc("")` description.
- Two `@app.part()` functions with the same name.

## Writing cache-friendly parts

The cache memoizes **synchronous top-level functions in the model and loaded local
helper modules**, keyed by defining source path, qualified name, the whole source
revision, and bound arguments. Passing a cached result into another function carries provenance, so
unchanged stages hit even when a downstream parameter changes.

Structure that makes caching work:

- **Compose the part from small top-level helper functions.** A single function that
  chains boolean fusions (`shell += rib; shell -= slot`) has no cacheable
  substructure — nothing can be skipped when one parameter changes.
- **Use algebra mode, not Builder mode.** Calls inside `with BuildPart():` bypass
  the cache entirely (a warning is logged once; silence it with `@no_cache`).
- **Use module-level imports.** Direct imports, aliases, relative imports,
  module-qualified calls, and transitive re-exports of local functions are supported.
  Only modules actually loaded from the snapshot are wrapped; unused files are not imported.
  Calling through a local variable holding a
  function (`op = make_fin; op(...)`) makes the call target unresolvable and forces
  a bypass.
- **Pass only encodable arguments** between cached functions: numbers, strings,
  bools, enums, tuples/lists/dicts of those, frozen dataclasses with encodable
  fields, or results of other cached calls
  (provenance). build123d value types like `Vector` or `Location` are not encodable —
  such calls simply run uncached.
- **For mesh-level cache hits, label every child and return a flat `Compound`:**

  ```python
  shell.label, lid.label = "shell", "lid"
  return Compound(children=[shell, lid])
  ```

  Only a flat, name-matching set of labeled children can have their display meshes
  reused; any other structure falls back to full re-tessellation hashing.

Things that disable caching (each cascades to every caller of the function):

- Referencing an impure name in the body: `open`, `random`, `time`, `environ`,
  `input`, `import_step`, `import_svg`.
- `@no_cache` on the function propagates to recognized callers across local modules.
  Other helpers it calls can still cache independently.

`@app.part(cache=False)` disables geometry lookup, insertion, and provenance tagging
throughout that execution, including shared helpers. Enabled parts can still cache
those helpers; existing entries are retained. Function-local imports and recognized
dynamic imports also disable caching in their callers and nested execution. The
independent tessellation cache remains enabled.

Methods, nested functions, callback containers, external modules, and custom
decorator machinery are not independently memoized. Analysis is conservative,
but it is not a comprehensive purity checker.

Code edits invalidate every function's cache key. FastBuild snapshots all `.py`
files beneath the model's directory (or its enclosing Python package), including
constants, properties, and local helpers, and watches that tree for changes.
Ordinary local imports are loaded again from the snapshot, including transitive
imports. Hidden directories, environments, and `node_modules` are excluded.
Parameter changes still reuse functions whose inputs are unchanged. Reloading
identical source or reverting an edit can reuse entries still in memory.

Code outside that source tree, dynamic loading via custom loaders or
`importlib.import_module`, installed dependencies, and external data files are
outside this automatic reload contract. Restart after editing external code;
use `@no_cache` for functions reading changing external data.

Also good to know: results faster than ~10 ms aren't stored (rebuilding is cheaper
than copying), failures are never cached, and the store is in-memory per process
(128 entries, LRU).

Verify caching behavior with `fastbuild view model.py --debug` — it prints a
per-call trace of `HIT` / `MISS` / `BYPASS <reason>` with short key digests.

## CLI reference

```bash
fastbuild view model.py [--port 8123] [--no-open] [--debug]
fastbuild demo {enclosure,finned_enclosure,knob} [--port ...] [--no-open] [--debug]
fastbuild export model.py PART [--param name=value]... -o out.step
fastbuild agent-docs [--install]
```

- `view` serves the live UI for a model file; `demo` serves a bundled example
  (`knob` shows every widget kind and grouping; `finned_enclosure` shows
  cache-friendly composition).
- `export` builds one part headlessly. `--param` values are parsed as JSON, falling
  back to raw string. Values are validated **strictly** against each parameter's
  bounds — out-of-range errors rather than clamps. Output format follows the
  extension: `.step`/`.stp` or `.stl`.
- `agent-docs` prints this document; `--install` writes it into
  `.claude/skills/fastbuild123/` and `.agents/skills/fastbuild123/` in the current
  directory (re-run after upgrading the package).

For embedding in an existing ASGI setup, `fastbuild.server.create_app(model_path)`
returns the FastAPI app.

## Environment

Python 3.10–3.13 on macOS, Linux (glibc 2.31+), or 64-bit Windows. Expect a ~500 MB
install (the OpenCASCADE kernel is most of it). Alpine/musl is not supported.
Install with `uv add fastbuild123` or `pip install fastbuild123`.
