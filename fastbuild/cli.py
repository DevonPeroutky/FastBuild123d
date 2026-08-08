"""CLI: `fastbuild view model.py` and `fastbuild export model.py part -o out.step`."""

import argparse
import json
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any, Sequence

from fastbuild.errors import Error
from fastbuild.harness import LoadedModel, load_model
from fastbuild.params import to_python, validate_bounds
from fastbuild.registry import PartDef


def _parse_param_arg(arg: str) -> tuple[str, Any] | Error:
    if "=" not in arg:
        return Error(f"--param expects name=value, got '{arg}'")
    name, raw = arg.split("=", 1)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = raw
    return (name, value)


def resolve_export_values(
    part: PartDef, param_args: Sequence[str]
) -> dict[str, Any] | Error:
    """Merge --param overrides over defaults, validated strictly against Range."""
    specs = {spec.name: spec for spec in part.params}
    values: dict[str, Any] = {}
    for arg in param_args:
        pair = _parse_param_arg(arg)
        if isinstance(pair, Error):
            return pair
        name, raw = pair
        if name not in specs:
            return Error(f"part '{part.name}' has no parameter '{name}'")
        value = validate_bounds(specs[name], raw)
        if isinstance(value, Error):
            return value
        values[name] = to_python(specs[name], value)
    return values


def _load_or_exit(path: Path) -> LoadedModel:
    loaded = load_model(path)
    if isinstance(loaded, Error):
        print(loaded.message, file=sys.stderr)
        raise SystemExit(1)
    return loaded


def _cmd_view(args: argparse.Namespace) -> None:
    import uvicorn

    from fastbuild.server import create_app

    path = Path(args.file).resolve()
    _load_or_exit(path)
    app = create_app(path)
    url = f"http://127.0.0.1:{args.port}"
    if not args.no_open:
        threading.Timer(0.8, webbrowser.open, args=[url]).start()
    print(f"fastbuild: serving {path.name} at {url}")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


def _cmd_export(args: argparse.Namespace) -> None:
    from build123d import export_step, export_stl

    path = Path(args.file).resolve()
    loaded = _load_or_exit(path)
    part = loaded.app.parts.get(args.part)
    if part is None:
        names = ", ".join(loaded.app.parts)
        print(f"no part '{args.part}' in {path.name} (available: {names})", file=sys.stderr)
        raise SystemExit(1)
    values = resolve_export_values(part, args.param)
    if isinstance(values, Error):
        print(values.message, file=sys.stderr)
        raise SystemExit(1)
    result = part.fn(**values)
    out = Path(args.output)
    suffix = out.suffix.lower()
    if suffix in (".step", ".stp"):
        export_step(result, str(out))
    elif suffix == ".stl":
        export_stl(result, str(out))
    else:
        print(f"unsupported output format '{suffix}' (use .step or .stl)", file=sys.stderr)
        raise SystemExit(1)
    print(f"exported {args.part} -> {out}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fastbuild")
    sub = parser.add_subparsers(dest="command", required=True)

    view = sub.add_parser("view", help="serve a model file with a live parametric UI")
    view.add_argument("file")
    view.add_argument("--port", type=int, default=8123)
    view.add_argument("--no-open", action="store_true")
    view.set_defaults(func=_cmd_view)

    export = sub.add_parser("export", help="build a part headless and write STEP/STL")
    export.add_argument("file")
    export.add_argument("part")
    export.add_argument("--param", action="append", default=[], metavar="name=value")
    export.add_argument("-o", "--output", required=True)
    export.set_defaults(func=_cmd_export)

    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)
