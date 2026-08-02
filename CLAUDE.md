## Overview

FastBuild123 is a Python framework that turns any build123d model into a live, interactive web UI: annotate your part function's parameters with a FastAPI-style @part() decorator, and it auto-generates sliders in the browser with instant 3D preview — no packaging, no manifest files, the code's type-annotated signature is the UI definition.

Its two key differentiators over existing tools (marimo-cad, PartCAD, bdbox): zero-overhead adoption via signature introspection, and a provenance-keyed caching layer that memoizes both geometry and display meshes at function granularity — so dragging a slider only recomputes the parts of the model that parameter actually affects, delivering Grasshopper-like responsiveness from ordinary Python CAD code.

## Coding Standards

General Python and functional-design standards for the whole repo live at repo-root PYTHON_CODE_STANDARDS.md and should be loaded whenever Claude reads a .py file.

Contributor guidance, including the code-comment policy, lives in CONTRIBUTING.md.
