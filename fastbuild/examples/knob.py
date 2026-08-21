"""Breadth litmus for the framework: a control knob that is conceptually
trivial but touches a wide slice of build123d and of the parameter model.

CAD features: revolve, loft, sweep, polar patterns, text engraving, boolean
add/subtract, chamfer. Parameter model: every widget kind — float and int
sliders, a bare number input, a checkbox, Literal and Enum dropdowns, an
unsupported (read-only) string — organized into Group() panel sections.
"""

import enum
from typing import Annotated, Literal

from build123d import (
    Align,
    Axis,
    Box,
    Circle,
    Compound,
    Cylinder,
    Part,
    Plane,
    PolarLocations,
    Polyline,
    Pos,
    Text,
    chamfer,
    extrude,
    loft,
    make_face,
    revolve,
    sweep,
)

from fastbuild import App, Desc, Group, Range

app = App()


class Grip(enum.Enum):
    SMOOTH = enum.auto()
    RIBBED = enum.auto()
    FLUTED = enum.auto()


def knob_body(diameter: float, height: float, skirt: float, taper: float) -> Part:
    """Revolved silhouette: cylindrical skirt below, tapered cone above."""
    r = diameter / 2
    outline = Polyline(
        (0, 0), (r, 0), (r, skirt), (r * taper, height), (0, height), close=True
    )
    return revolve(Plane.XZ * make_face(outline), Axis.Z)


def chamfered_base(body: Part, length: float) -> Part:
    return chamfer(body.edges().group_by(Axis.Z)[0], length=length)


def gripped(
    body: Part, grip: Grip, count: int, rib_dia: float, diameter: float, skirt: float
) -> Part:
    ribs = [
        loc * Cylinder(rib_dia / 2, skirt, align=(Align.CENTER, Align.CENTER, Align.MIN))
        for loc in PolarLocations(diameter / 2, count)
    ]
    match grip:
        case Grip.SMOOTH:
            return body
        case Grip.RIBBED:
            return body + ribs
        case Grip.FLUTED:
            return body - ribs


def bead_ring(diameter: float, z: float) -> Part:
    """Decorative bead swept around the skirt lip."""
    path = Pos(0, 0, z) * Circle(diameter / 2).edge()
    return sweep((path ^ 0) * Circle(0.8), path)


def shaft_bore(bore_dia: float, clearance: float, d_flat: bool, depth: float) -> Part:
    r = bore_dia / 2 + clearance
    bore = Cylinder(r, depth, align=(Align.CENTER, Align.CENTER, Align.MIN))
    if d_flat:
        flat = 0.75 * r
        bore -= Pos(flat + r, 0, depth / 2) * Box(2 * r, 2 * r, depth)
    return bore


def lofted_cap(radius: float, cap_height: float) -> Part:
    return loft([Circle(radius), Pos(0, 0, cap_height) * Circle(radius * 0.6)])


def engraved(cap: Part, label: str, radius: float, cap_height: float) -> Part:
    if not label.strip():
        return cap
    txt = Text(label, font_size=radius * 0.35)
    return cap - extrude(Pos(0, 0, cap_height) * txt, amount=-0.6)


def pointed(
    cap: Part, pointer: Literal["none", "line", "dot"], radius: float, cap_height: float
) -> Part:
    at_edge = Pos(radius * 0.75, 0, cap_height)
    match pointer:
        case "none":
            return cap
        case "line":
            return cap - at_edge * Box(radius * 0.5, 1.2, 1.2)
        case "dot":
            return cap - at_edge * Cylinder(0.9, 1.2)


@app.part()
def knob(
    diameter: Annotated[float, Range(20, 60, step=1, unit="mm")] = 32,
    height: Annotated[float, Range(10, 40, step=1, unit="mm")] = 18,
    skirt_height: Annotated[float, Range(4, 20, step=0.5, unit="mm"), Group("Body")] = 8,
    taper: Annotated[
        float,
        Range(0.5, 1.0, step=0.05),
        Group("Body"),
        Desc("Top diameter as a fraction of the base diameter"),
    ] = 0.75,
    grip: Annotated[Grip, Group("Grip")] = Grip.RIBBED,
    rib_count: Annotated[int, Range(8, 40), Group("Grip")] = 18,
    rib_dia: Annotated[float, Range(1, 4, step=0.25, unit="mm"), Group("Grip")] = 2,
    bore_dia: Annotated[float, Range(3, 10, step=0.5, unit="mm"), Group("Shaft")] = 6,
    shaft_clearance: Annotated[
        float, Group("Shaft"), Desc("Extra bore radius so the knob slips onto the shaft")
    ] = 0.1,
    d_flat: Annotated[bool, Group("Shaft"), Desc("Match a D-shaped potentiometer shaft")] = True,
    cap_height: Annotated[float, Range(2, 8, step=0.5, unit="mm"), Group("Top")] = 4,
    pointer: Annotated[Literal["none", "line", "dot"], Group("Top")] = "line",
    label: Annotated[str, Group("Top"), Desc("Text engraved on the cap")] = "VOL",
) -> Compound:
    skirt = min(skirt_height, height - 2)
    body = gripped(
        chamfered_base(knob_body(diameter, height, skirt, taper), 0.8),
        grip,
        rib_count,
        rib_dia,
        diameter,
        skirt,
    )
    body = body + bead_ring(diameter, skirt)
    body = body - shaft_bore(bore_dia, shaft_clearance, d_flat, height - 3)

    cap_radius = (diameter / 2) * taper * 0.95
    cap = pointed(
        engraved(lofted_cap(cap_radius, cap_height), label, cap_radius, cap_height),
        pointer,
        cap_radius,
        cap_height,
    )

    body.label = "body"
    cap = Pos(0, 0, height) * cap
    cap.label = "cap"
    return Compound(children=[body, cap])
