"""Measure what a model file actually costs to build.

SPEC-caching Phase 0: the caching targets are stated against a measured
reference model, not a guess. Run from the repo root:

    python scripts/bench_model.py fastbuild/examples/finned_enclosure.py
    python scripts/bench_model.py fastbuild/examples/finned_enclosure.py --sweep

Stage times come from temporarily wrapping every function the model file
defines, so they are inclusive — a stage that calls another contains it.
"""

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from types import FunctionType
from typing import Any, Callable, Mapping, Sequence, Union

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastbuild.builder import BuildFailure, BuildOutput, build_part
from fastbuild.errors import Error
from fastbuild.harness import load_model
from fastbuild.params import JsonValue, Slider, coerce, default_value
from fastbuild.registry import PartDef


@dataclass(frozen=True)
class Case:
    part: str
    label: str
    values: Mapping[str, JsonValue]


@dataclass(frozen=True)
class StageCost:
    name: str
    calls: int
    total_ms: float


@dataclass(frozen=True)
class Timing:
    case: Case
    build_ms: float
    tessellate_ms: float
    stages: tuple[StageCost, ...]


@dataclass(frozen=True)
class Failed:
    case: Case
    traceback: str


Result = Union[Timing, Failed]


def defaults(part: PartDef) -> Mapping[str, JsonValue]:
    return {spec.name: default_value(spec) for spec in part.params}


def default_cases(parts: Mapping[str, PartDef]) -> tuple[Case, ...]:
    return tuple(Case(name, "defaults", defaults(part)) for name, part in parts.items())


def sweep_cases(parts: Mapping[str, PartDef]) -> tuple[Case, ...]:
    """Every slider driven to each end, one at a time — the cheapest check that
    no parameter extreme hands OCC a shape it refuses to build."""
    cases = []
    for name, part in parts.items():
        base = defaults(part)
        for spec in part.params:
            if not isinstance(spec, Slider):
                continue
            for raw in (spec.min, spec.max):
                value = coerce(spec, raw)
                if isinstance(value, Error):
                    continue
                cases.append(
                    Case(name, f"{spec.name}={value}", {**base, spec.name: value})
                )
    return tuple(cases)


@dataclass
class _Counter:
    calls: int = 0
    total: float = 0.0


@dataclass
class _Instrumentation:
    counters: dict[str, _Counter] = field(default_factory=dict)
    restore: Callable[[], None] = lambda: None


def _timed(fn: Callable[..., Any], counter: _Counter) -> Callable[..., Any]:
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        start = perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            counter.calls += 1
            counter.total += perf_counter() - start

    return wrapper


def _instrument(namespace: dict[str, Any]) -> _Instrumentation:
    """Wrap the model file's own functions in place. Call-time global lookup is
    what makes this work — the same seam Phase 1's wrappers will use."""
    originals = {
        name: value
        for name, value in namespace.items()
        if isinstance(value, FunctionType) and value.__globals__ is namespace
    }
    counters = {name: _Counter() for name in originals}
    for name, fn in originals.items():
        namespace[name] = _timed(fn, counters[name])

    def restore() -> None:
        namespace.update(originals)

    return _Instrumentation(counters=counters, restore=restore)


def measure(part: PartDef, case: Case, namespace: dict[str, Any]) -> Result:
    instrumentation = _instrument(namespace)
    try:
        outcome = build_part(part, case.values)
    finally:
        instrumentation.restore()

    match outcome:
        case BuildFailure(traceback=tb):
            return Failed(case=case, traceback=tb)
        case BuildOutput(timings=timings):
            stages = tuple(
                StageCost(name, c.calls, round(c.total * 1000, 1))
                for name, c in instrumentation.counters.items()
                if c.calls
            )
            return Timing(
                case=case,
                build_ms=timings["build_ms"],
                tessellate_ms=timings["tessellate_ms"],
                stages=stages,
            )


def format_stages(timing: Timing) -> str:
    ordered = sorted(timing.stages, key=lambda s: s.total_ms, reverse=True)
    lines = [
        f"  {stage.name:<20} {stage.total_ms:>9.1f} ms  x{stage.calls}"
        for stage in ordered
    ]
    return "\n".join(lines)


def format_result(result: Result) -> str:
    match result:
        case Failed(case=case, traceback=tb):
            return f"{case.part}  {case.label:<24} FAILED\n{tb.rstrip()}"
        case Timing(case=case):
            total = result.build_ms + result.tessellate_ms
            return (
                f"{case.part}  {case.label:<24} "
                f"build {result.build_ms:>8.1f} ms  "
                f"tessellate {result.tessellate_ms:>8.1f} ms  "
                f"total {total:>8.1f} ms"
            )


def run(
    parts: Mapping[str, PartDef],
    cases: Sequence[Case],
    repeat: int,
    show_stages: bool,
    namespace: dict[str, Any],
) -> int:
    failures = 0
    for case in cases:
        part = parts[case.part]
        for _ in range(repeat):
            result = measure(part, case, namespace)
            print(format_result(result))
            if isinstance(result, Failed):
                failures += 1
            elif show_stages and result.stages:
                print(format_stages(result))
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench_model")
    parser.add_argument("file")
    parser.add_argument("--part", action="append", default=[])
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument(
        "--sweep",
        action="store_true",
        help="also build every slider at both ends (slow; exits non-zero on failure)",
    )
    parser.add_argument("--no-stages", action="store_true")
    args = parser.parse_args(argv)

    path = Path(args.file).resolve()
    loaded = load_model(path)
    if isinstance(loaded, Error):
        print(loaded.message, file=sys.stderr)
        return 1

    parts = loaded.app.parts
    if args.part:
        missing = [name for name in args.part if name not in parts]
        if missing:
            print(f"no part {missing} in {path.name}", file=sys.stderr)
            return 1
        parts = {name: parts[name] for name in args.part}

    cases = default_cases(parts) + (sweep_cases(parts) if args.sweep else ())
    namespace = loaded.namespace
    assert isinstance(namespace, dict)  # the live exec dict, never a copy
    return run(parts, cases, args.repeat, not args.no_stages, namespace)


if __name__ == "__main__":
    raise SystemExit(main())
