## Overview

FastBuild123 is a Python framework that turns any build123d model into a live, interactive web UI: annotate your part function's parameters with a FastAPI-style @part() decorator, and it auto-generates sliders in the browser with instant 3D preview — no packaging, no manifest files, the code's type-annotated signature is the UI definition.

Its two key differentiators over existing tools (marimo-cad, PartCAD, bdbox): zero-overhead adoption via signature introspection, and a provenance-keyed caching layer that memoizes both geometry and display meshes at function granularity — so dragging a slider only recomputes the parts of the model that parameter actually affects, delivering Grasshopper-like responsiveness from ordinary Python CAD code.

## Coding Standards

@PYTHON_CODE_STANDARDS.md

The standards imported above are binding for **all** Python work in this repo:

- **Designing plans** — a plan must satisfy the decision checklist before you
  present it. Call out where a design makes illegal states unrepresentable
  rather than validating them at runtime.
- **Writing or modifying code** — run the decision checklist before you finish.
- **Reviewing code** — review against these standards, not generic Python style.
  Every fallback, default, or defensive check in a diff is a finding unless the
  invalid state genuinely cannot be encoded in the type.

When you delegate to a subagent for planning, implementation, or review, include
the instruction to read `PYTHON_CODE_STANDARDS.md` in its prompt.

Contributor guidance, including the code-comment policy, lives in CONTRIBUTING.md.
