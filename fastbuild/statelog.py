"""Redux-logger-style rendering of SessionState transitions for `view --debug`.

Pure — the server formats a transition here and performs the logging side
effect at its boundary.
"""

from fastbuild.session import SessionState

HEADER_WIDTH = 60
_INDENT = " " * 9


def state_lines(state: SessionState) -> tuple[str, ...]:
    width = max(len(name) for name in state.parts)
    return (
        f"selected={state.selected!r}",
        *(
            f"{name.ljust(width)}  " + " ".join(f"{k}={v!r}" for k, v in vals.items())
            for name, vals in state.values.items()
        ),
    )


def state_diff(prev: SessionState | None, new: SessionState) -> tuple[str, ...]:
    if prev is None:
        return ("(initial state)",)
    lines = []
    if prev.selected != new.selected:
        lines.append(f"selected: {prev.selected!r} -> {new.selected!r}")
    lines.extend(f"-part {part!r}" for part in prev.parts if part not in new.parts)
    lines.extend(f"+part {part!r}" for part in new.parts if part not in prev.parts)
    for part, vals in new.values.items():
        old = prev.values.get(part)
        if old is None:
            continue
        for name, value in vals.items():
            if name not in old:
                lines.append(f"+{part}.{name} = {value!r}")
            elif old[name] != value:
                lines.append(f"{part}.{name}: {old[name]!r} -> {value!r}")
        lines.extend(f"-{part}.{name}" for name in old if name not in vals)
    return tuple(lines) if lines else ("(no change)",)


def _labeled(label: str, lines: tuple[str, ...]) -> tuple[str, ...]:
    first = f"  {label.ljust(5)}  {lines[0]}"
    return (first, *(_INDENT + line for line in lines[1:]))


def format_transition(action: str, prev: SessionState | None, new: SessionState) -> str:
    return "\n".join(
        (
            "",
            f"── {action} ".ljust(HEADER_WIDTH, "─"),
            *_labeled("diff", state_diff(prev, new)),
            *_labeled("state", state_lines(new)),
        )
    )
