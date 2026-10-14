# FastBuild123

A browser-based UI for [build123d](https://github.com/gumyr/build123d), parametric models, generated directly from your code. Two-way caching of both code and UI inputs keeps parameter changes and model previews fast and responsive. This means you (aka your AI agent) can write code to create your parameterized CAD models and get...

- **Agentic CAD modeling**: [Preloaded skills](#AI) let your agent inspect the model from any angle, giving it the visual context it needs to help refine your design.
- **Manufacturing ready CAD**: Generate B-Rep and NURBS geometry and export precision CAD formats like STEP
- **Live CAD UI previews**: Modify parameters and see the model update in real time.

...and there's **No CAD knowledge required**. It definitely helps to know what you're doing, but if you're capable of describing what you want to an LLM, you can get pretty far.

https://github.com/user-attachments/assets/ab291672-6d0f-4e3c-a8ef-c5fe7fe363e9


## Demo

Try a bundled example with [uv](https://docs.astral.sh/uv/):

```bash
uvx fastbuild123 demo enclosure
```

## Quick Start

```bash
# Install package
uv add fastbuild123      # or: pip install fastbuild123
```

### 1. Annotate
```python
from typing import Annotated
from build123d import Box, Part
from fastbuild import App, Range

app = App()

@app.part()
def example_box(
    width: Annotated[float, Range(20, 200, step=1, unit="mm")] = 80,
    depth: float = 50,
    height: float = 30,
) -> Part:
    return Box(width, depth, height)
```

### 2. View your geometry

```bash
uv run fastbuild view my_model.py
```

The viewer preserves your camera when rebuilding. If a build fails, it shows the
error and keeps the last working model.

## Parameters

Each parameter you annotate generates a control in the UI  (and can have defaults)

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

Obviously, this is the section everyone will care about. The repo’s real value is that it lets you rapidly iterate on CAD models using AI agents. The agents modify the code, which generates an interactive UI you can use to explore and refine the design. You can then iterate by chatting with the agent, which already has the full context of your model.

## AI coding agents

Install the bundled skill with API usage and caching guidance:

```bash
fastbuild agent-docs --install
```

Writes to `.claude/skills/fastbuild123/` and `.agents/skills/fastbuild123/`.
Re-run after upgrading. Omit `--install` to print the docs.

## Requirements

Python 3.10–3.13 on macOS, Linux (glibc 2.31+, x86-64 or aarch64), or 64-bit Windows.

## Contributing and releases

Use Conventional Commit titles for squash merges. See [automated releases](docs/releases.md)
for version rules, the local dry run, Trusted Publishing setup, and recovery.

## License

MIT — see [LICENSE](LICENSE).
