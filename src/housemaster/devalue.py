"""Decoder for the devalue payload Nuxt embeds in `<script id="__NUXT_DATA__">`.

Nuxt 3 serialises the SSR state as a *flat array*: slot 0 is the root, and any
integer appearing inside a container is an index into that same array rather
than a literal number. Literals (strings, numbers, booleans) sit in their own
slots. Repeated objects are therefore shared, and cycles are possible.

A list whose first element is a string is a tagged value (`["Date", "..."]`,
`["Ref", 12]`, ...) -- a plain data array would hold integer references, never a
raw string, so the check is unambiguous.
"""

from __future__ import annotations

import json
from typing import Any

# Negative slots are sentinels rather than indices.
SENTINELS: dict[int, Any] = {
    -1: None,  # hole in a sparse array
    -2: None,  # undefined
    -3: float("nan"),
    -4: float("inf"),
    -5: float("-inf"),
    -6: -0.0,
}

# Nuxt reactivity wrappers: transparent, unwrap to the referenced slot.
_UNWRAP = {"Ref", "Reactive", "ShallowRef", "ShallowReactive", "Shallow"}
# Standard devalue tags carrying a single literal payload.
_LITERAL = {"Date", "BigInt", "URL", "Object"}


def parse(payload: str | list) -> Any:
    """Decode a __NUXT_DATA__ payload (JSON text or already-parsed list)."""
    slots = json.loads(payload) if isinstance(payload, str) else payload

    def resolve(index: Any, seen: frozenset[int]) -> Any:
        if isinstance(index, int) and index in SENTINELS:
            return SENTINELS[index]
        if index in seen:
            return None  # cycle: the parent already holds this value
        value = slots[index]
        nested = seen | {index}

        if isinstance(value, list):
            if value and isinstance(value[0], str):
                tag, rest = value[0], value[1:]
                if tag in _UNWRAP:
                    return resolve(rest[0], nested)
                if tag == "EmptyRef":
                    return None
                if tag in _LITERAL:
                    return rest[0] if rest else None
                if tag == "RegExp":
                    return rest[0]
                if tag == "Set":
                    return [resolve(i, nested) for i in rest]
                if tag == "Map":
                    items = [resolve(i, nested) for i in rest]
                    # strict=False: a trailing key with no value means a
                    # malformed payload, and dropping it beats raising here.
                    return dict(zip(items[::2], items[1::2], strict=False))
                # Unknown tag: keep it visible rather than silently dropping it.
                return {f"__{tag}": [resolve(i, nested) for i in rest]}
            return [resolve(i, nested) for i in value]

        if isinstance(value, dict):
            return {k: resolve(i, nested) for k, i in value.items()}

        return value

    return resolve(0, frozenset())
