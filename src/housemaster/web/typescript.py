"""TypeScript for the API's response shapes, generated from `web.api`.

Its own module, not part of `api`: `views` imports `api`, so running `api` as
`-m` would load it twice.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import (
    Any,
    Literal,
    Union,
    get_args,
    get_origin,
    get_type_hints,
    is_typeddict,
)

from housemaster.web.api import ALIASES, EXPORTED

_SCALARS: dict[object, str] = {
    int: "number",
    float: "number",
    str: "string",
    bool: "boolean",
    type(None): "null",
    Any: "unknown",
}


def _ts(tp: object) -> str:
    for name, alias in ALIASES.items():
        if tp == alias:
            return name
    if tp in _SCALARS:
        return _SCALARS[tp]
    if isinstance(tp, type) and is_typeddict(tp):
        return tp.__name__
    origin = get_origin(tp)
    args = get_args(tp)
    if origin is Literal:
        return " | ".join(f'"{arg}"' for arg in args)
    if origin in (Union, types.UnionType):
        return " | ".join(_ts(arg) for arg in args)
    if origin is list:
        inner = _ts(args[0])
        return f"({inner})[]" if " | " in inner else f"{inner}[]"
    if origin is dict and args[0] is str:
        return f"Record<string, {_ts(args[1])}>"
    raise TypeError(f"no TypeScript for {tp!r}; extend typescript._ts")


def _literal_union(alias: object, indent: str = "  ") -> str:
    values = get_args(alias)
    return "\n" + "\n".join(f'{indent}| "{value}"' for value in values)


def typescript() -> str:
    """The generated module's whole text."""
    out = [
        "// Generated from src/housemaster/web/api.py -- do not edit.",
        "// Regenerate: uv run python -m housemaster.web.typescript "
        "frontend/src/lib/api.gen.ts",
        "",
    ]
    for name, alias in ALIASES.items():
        out.append(f"export type {name} ={_literal_union(alias)}")
        out.append("")
    for typed in EXPORTED:
        out.append(f"export type {typed.__name__} = {{")
        for field, tp in get_type_hints(typed).items():
            out.append(f"  {field}: {_ts(tp)}")
        out.append("}")
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        sys.stderr.write("usage: python -m housemaster.web.typescript <out.ts>\n")
        return 2
    Path(argv[0]).write_text(typescript(), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
