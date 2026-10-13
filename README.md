# FastBuild123

A browser UI for [build123d](https://github.com/gumyr/build123d) models that is generated directly from the code and has two way caching (both of the UI inputs and the code itself) to enable live, responsive updates of the parameters.

https://github.com/user-attachments/assets/ab291672-6d0f-4e3c-a8ef-c5fe7fe363e9


## Quick start

Try a bundled example with [uv](https://docs.astral.sh/uv/):

```bash
uvx fastbuild123 demo enclosure
```

Install in your project:

```bash
uv add fastbuild123      # or: pip install fastbuild123
```

Save this as `my_model.py`:

```python
from typing import Annotated
from build123d import Box, Part
from fastbuild import App, Range

app = App()

@app.part()
def block(
    width: Annotated[float, Range(20, 200, step=1, unit="mm")] = 80,
    depth: float = 50,
    height: float = 30,
) -> Part:
    return Box(width, depth, height)
```

Open it in the browser:

```bash
fastbuild view my_model.py
```

The viewer preserves your camera when rebuilding. If a build fails, it shows the
error and keeps the last working model.

## Parameters

Every parameter needs a type annotation and a default value.

| Type | Control |
| --- | --- |
| `float` / `int` with `Range(...)` | Slider |
| `float` / `int` | Number input |
| `bool` | Checkbox |
| `Literal` / `Enum` | Dropdown |

Use `Group("Label")` and `Desc("Help text")` from `fastbuild` inside `Annotated`
to group controls and add tooltips. Multiple `@app.part()` functions get a part selector.

## Export files for printing/milling/etc

```bash
fastbuild export my_model.py block --param width=120 -o block.step
```

Values are validated against each parameter's `Range`; output format follows the
extension (`.step`/`.stp`/`.stl`).

# AI

## AI coding agents

Install the bundled skill with API usage and caching guidance:

```bash
fastbuild agent-docs --install
```

Writes to `.claude/skills/fastbuild123/` and `.agents/skills/fastbuild123/`.
Re-run after upgrading. Omit `--install` to print the docs.

## Requirements

Python 3.10–3.13 on macOS, Linux (glibc 2.31+, x86-64 or aarch64), or 64-bit Windows.
The install is roughly 500 MB, mostly OpenCASCADE. Alpine/musl is not supported.

## Contributing and releases

Use Conventional Commit titles for squash merges. See [automated releases](docs/releases.md)
for version rules, the local dry run, Trusted Publishing setup, and recovery.

## License

MIT — see [LICENSE](LICENSE). Bundles three-cad-viewer (MIT), whose license is
included at `fastbuild/static/vendor/LICENSE-three-cad-viewer`.
