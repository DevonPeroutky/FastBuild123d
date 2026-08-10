"""Smoke tests for the caching reference model (SPEC-caching Phase 0).

Built at deliberately cheap parameters — the point here is that every stage
still runs, not how long it takes. Timing lives in `scripts/bench_model.py`.
"""

from pathlib import Path

from fastbuild.builder import BuildOutput, build_part
from fastbuild.harness import LoadedModel, load_model
from fastbuild.params import default_value

MODEL = Path(__file__).resolve().parents[1] / "fastbuild" / "examples" / "finned_enclosure.py"

STAGES = {
    "finned_profile",
    "extruded_body",
    "hollowed",
    "rounded_fins",
    "perforated_panel",
    "chamfered",
    "mounting_bosses",
}

CHEAP = {"fin_count": 4, "vent_cols": 3, "vent_rows": 2, "cols": 3, "rows": 2}


def _build(name: str) -> BuildOutput | object:
    loaded = load_model(MODEL)
    assert isinstance(loaded, LoadedModel)
    part = loaded.app.parts[name]
    values = {spec.name: default_value(spec) for spec in part.params}
    return build_part(part, {**values, **{k: v for k, v in CHEAP.items() if k in values}})


def test_reference_model_is_decomposed_into_stages():
    loaded = load_model(MODEL)
    assert isinstance(loaded, LoadedModel)
    assert set(loaded.app.parts) == {"vent_grille", "enclosure"}
    assert STAGES <= set(loaded.function_hashes)


def test_reference_parts_build():
    assert isinstance(_build("vent_grille"), BuildOutput)
    assert isinstance(_build("enclosure"), BuildOutput)
