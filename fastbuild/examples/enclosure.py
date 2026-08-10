from typing import Annotated, Literal

from build123d import (
    Axis,
    Box,
    Part,
    Plane,
    Pos,
    chamfer,
    fillet,
    offset,
)

from fastbuild import App, Range

app = App()


@app.part()
def enclosure(
    width: Annotated[float, Range(20, 200, step=1, unit="mm")] = 80,
    depth: Annotated[float, Range(20, 160, step=1, unit="mm")] = 50,
    height: Annotated[float, Range(10, 80, step=1, unit="mm")] = 30,
    wall: Annotated[float, Range(0.8, 5, step=0.1, unit="mm")] = 2,
    corner_radius: Annotated[float, Range(0, 30, step=0.5, unit="mm")] = 8,
    ribs: Annotated[int, Range(0, 8)] = 4,
    vented: bool = False,
    style: Literal["rounded", "chamfered"] = "rounded",
) -> Part:
    shell = Box(width, depth, height)
    edges = shell.edges().filter_by(Axis.Z)
    # Two corners share each side, and OCC needs the walls to survive the cut.
    corner = min(corner_radius, min(width, depth) / 2 - wall)
    if corner > 0:
        if style == "rounded":
            shell = fillet(edges, radius=corner)
        else:
            shell = chamfer(edges, length=corner)
    shell = offset(shell, -wall, openings=shell.faces().sort_by(Axis.Z)[-1])

    for i in range(ribs):
        x = -width / 2 + (i + 1) * width / (ribs + 1)
        rib = Pos(x, 0, -height / 2 + wall) * Box(
            wall, depth - 2 * wall, height / 4, align=(None, None, None)
        )
        shell += rib

    if vented:
        slot_count = max(3, int(width // 15))
        for i in range(slot_count):
            x = -width / 2 + (i + 1) * width / (slot_count + 1)
            shell -= Pos(x, 0, 0) * Box(3, depth + 2 * wall, height / 2)

    return shell


@app.part()
def lid(
    width: Annotated[float, Range(20, 200, step=1, unit="mm")] = 80,
    depth: Annotated[float, Range(20, 160, step=1, unit="mm")] = 50,
    thickness: Annotated[float, Range(1, 6, step=0.5, unit="mm")] = 2,
) -> Part:
    plate = Box(width, depth, thickness)
    lip = Plane.XY.offset(-thickness) * Box(width - 5, depth - 5, thickness)
    return plate + lip
