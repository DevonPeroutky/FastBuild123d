"""Reference model for the caching subsystem (SPEC-caching Phase 0).

A finned, vented instrument enclosure that takes seconds to build, written as a
composition of small functions instead of one fused chain — so a slider feeding
only one stage can, once caching lands, skip the others.

Run `python scripts/bench_model.py examples/finned_enclosure.py` for the
per-stage cost of the current defaults.
"""

from typing import Annotated

from build123d import (
    Align,
    Axis,
    Box,
    Compound,
    Cylinder,
    GeomType,
    Part,
    Pos,
    Rectangle,
    RectangleRounded,
    ShapeList,
    Sketch,
    chamfer,
    extrude,
    fillet,
    offset,
)

from fastbuild import App, Range

app = App()


def finned_profile(
    width: float,
    depth: float,
    corner_radius: float,
    fin_count: int,
    fin_width: float,
    fin_depth: float,
) -> Sketch:
    """Rounded outline with cooling fins along the two long sides.

    Fins that touch fuse into a slab, so the count is capped by the span.
    """
    corner = min(corner_radius, min(width, depth) / 2 - 1)
    core = RectangleRounded(width, depth, corner)
    span = width - 2 * corner
    count = max(2, min(fin_count, int(span // (2 * fin_width))))
    pitch = span / (count - 1)
    fins = [
        Pos(-span / 2 + i * pitch, y) * Rectangle(fin_width, 2 * fin_depth)
        for i in range(count)
        for y in (-depth / 2, depth / 2)
    ]
    return core + fins


def extruded_body(profile: Sketch, height: float) -> Part:
    return extrude(profile, amount=height)


def hollowed(body: Part, wall: float) -> Part:
    """Shell the body with the top left open. Every fin face is another surface
    OCC has to offset, so the cost tracks the fin count."""
    return offset(body, -wall, openings=body.faces().sort_by(Axis.Z)[-1])


def rounded_fins(shell: Part, fin_width: float, radius: float) -> Part:
    """Round the fin tips — the outermost vertical edges on either flank."""
    safe = min(radius, 0.4 * fin_width)
    if safe <= 0:
        return shell
    flanks = shell.edges().filter_by(Axis.Z).group_by(Axis.Y)
    return fillet(ShapeList([*flanks[0], *flanks[-1]]), radius=safe)


def perforated_panel(
    width: float,
    depth: float,
    thickness: float,
    cols: int,
    rows: int,
    hole_dia: float,
) -> Part:
    plate = Box(width, depth, thickness)
    if cols < 1 or rows < 1:
        return plate
    pitch_x = width / (cols + 1)
    pitch_y = depth / (rows + 1)
    dia = min(hole_dia, 0.7 * min(pitch_x, pitch_y))
    holes = [
        Pos(-width / 2 + (i + 1) * pitch_x, -depth / 2 + (j + 1) * pitch_y, 0)
        * Cylinder(dia / 2, 3 * thickness)
        for i in range(cols)
        for j in range(rows)
    ]
    return plate - holes


def chamfered(panel: Part, length: float) -> Part:
    """Break the edge of every bore. OCC gives up past about a third of the bore
    radius or of the plate thickness, so clamp to both."""
    bores = panel.edges().filter_by(GeomType.CIRCLE)
    if not bores:
        return panel
    safe = min(
        length,
        min(edge.radius for edge in bores) / 3,
        panel.bounding_box().size.Z / 3,
    )
    if safe <= 0:
        return panel
    return chamfer(bores, length=safe)


def mounting_bosses(
    width: float,
    depth: float,
    height: float,
    wall: float,
    boss_dia: float,
) -> Part:
    inset = boss_dia
    post = Cylinder(
        boss_dia / 2, height - 2 * wall, align=(Align.CENTER, Align.CENTER, Align.MIN)
    ) - Cylinder(
        0.2 * boss_dia, height, align=(Align.CENTER, Align.CENTER, Align.MIN)
    )
    return Part() + [
        Pos(x, y, -height / 2 + wall) * post
        for x in (-(width / 2 - inset), width / 2 - inset)
        for y in (-(depth / 2 - inset), depth / 2 - inset)
    ]


@app.part()
def vent_grille(
    width: Annotated[float, Range(40, 220, step=1, unit="mm")] = 136,
    depth: Annotated[float, Range(30, 160, step=1, unit="mm")] = 86,
    thickness: Annotated[float, Range(1, 6, step=0.5, unit="mm")] = 3,
    cols: Annotated[int, Range(0, 30)] = 16,
    rows: Annotated[int, Range(0, 20)] = 10,
    hole_dia: Annotated[float, Range(1, 8, step=0.1, unit="mm")] = 3,
    deburr: Annotated[float, Range(0, 1.5, step=0.1, unit="mm")] = 0.5,
) -> Part:
    return chamfered(
        perforated_panel(width, depth, thickness, cols, rows, hole_dia), deburr
    )


@app.part()
def enclosure(
    width: Annotated[float, Range(80, 220, step=1, unit="mm")] = 140,
    depth: Annotated[float, Range(60, 160, step=1, unit="mm")] = 90,
    height: Annotated[float, Range(30, 120, step=1, unit="mm")] = 55,
    corner_radius: Annotated[float, Range(2, 30, step=0.5, unit="mm")] = 10,
    wall: Annotated[float, Range(1.2, 4, step=0.1, unit="mm")] = 2,
    fin_count: Annotated[int, Range(2, 40)] = 20,
    fin_width: Annotated[float, Range(1, 6, step=0.5, unit="mm")] = 2.5,
    fin_depth: Annotated[float, Range(0.5, 8, step=0.5, unit="mm")] = 3,
    fin_radius: Annotated[float, Range(0, 2, step=0.1, unit="mm")] = 0.5,
    panel_thickness: Annotated[float, Range(1, 6, step=0.5, unit="mm")] = 3,
    vent_cols: Annotated[int, Range(0, 30)] = 16,
    vent_rows: Annotated[int, Range(0, 20)] = 10,
    vent_dia: Annotated[float, Range(1, 8, step=0.1, unit="mm")] = 3,
    deburr: Annotated[float, Range(0, 1.5, step=0.1, unit="mm")] = 0.5,
    boss_dia: Annotated[float, Range(4, 16, step=0.5, unit="mm")] = 9,
) -> Part:
    profile = finned_profile(width, depth, corner_radius, fin_count, fin_width, fin_depth)
    shell = rounded_fins(
        hollowed(extruded_body(profile, height), wall), fin_width, fin_radius
    )
    grille = vent_grille(
        width - 2 * wall,
        depth - 2 * wall,
        panel_thickness,
        vent_cols,
        vent_rows,
        vent_dia,
        deburr,
    )
    bosses = mounting_bosses(width, depth, height, wall, boss_dia)

    shell.label = "shell"
    lid = Pos(0, 0, height / 2 - panel_thickness / 2) * grille
    lid.label = "grille"
    bosses.label = "bosses"
    return Compound(children=[shell, lid, bosses])
