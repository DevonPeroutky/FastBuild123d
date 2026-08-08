# FastBuild123

Turn any [build123d](https://github.com/gumyr/build123d) model into a live, interactive
parametric web UI. Annotate your part function's parameters with a FastAPI-style
`@app.part()` decorator and get auto-generated sliders in the browser with instant
3D preview — the type-annotated signature *is* the UI definition.

## Quick start

```bash
uv sync
uv run fastbuild view examples/enclosure.py
```

A browser page opens with parameter controls on the left and a live 3D view on the
right. Drag a slider or save the file — the model updates in place, keeping your
camera pose, tree state, and clipping planes. A model that raises keeps the last-good
geometry on screen with an error banner; fix the file and it recovers.

## Defining parts

```python
from typing import Annotated, Literal
from build123d import Part, Box
from fastbuild import App, Range

app = App()

@app.part()
def enclosure(
    width:  Annotated[float, Range(20, 200, step=1, unit="mm")] = 80,
    wall:   Annotated[float, Range(0.8, 5, step=0.1, unit="mm")] = 2,
    ribs:   Annotated[int, Range(0, 8)] = 4,
    vented: bool = False,
    style:  Literal["rounded", "chamfered"] = "rounded",
) -> Part:
    ...
```

Widget mapping: `float`/`int` + `Range` → slider, bare numbers → number input,
`bool` → checkbox, `Literal`/`Enum` → dropdown. Every parameter needs a default —
defaults are the initial build. Multiple `@app.part()` functions in one file get a
part selector.

## Headless export

```bash
uv run fastbuild export examples/enclosure.py enclosure --param width=120 -o enclosure.step
```

Values are validated against each parameter's `Range`; output format follows the
extension (`.step`/`.stp`/`.stl`).

## Development

```bash
uv run pytest
```

Specs live in `specs/`. The 3D component is a vendored, pinned build of
[three-cad-viewer](https://github.com/bernhard-42/three-cad-viewer)
(`fastbuild/static/vendor/`, see `VERSION` there) fed by
[ocp-tessellate](https://github.com/bernhard-42/ocp-tessellate) output over a
versioned websocket protocol.
