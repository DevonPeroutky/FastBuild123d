"""Cache keys: provenance-chained encoding of call arguments. Pure.

A key is `sha256(qualname, source_closure_hash, encoded bound-args)`. Arguments
encode into a closed union — `Provenance | Primitive | Composite` — and
anything outside it is an `Error`, which callers treat as "run uncached".
"""

import hashlib
from collections.abc import Mapping as MappingABC
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Union

from fastbuild.errors import Error

TAG_ATTR = "_fb_key"

_MAX_DEPTH = 32


@dataclass(frozen=True)
class CacheKey:
    qualname: str
    digest: str

    @property
    def short(self) -> str:
        return self.digest[:10]


@dataclass(frozen=True)
class Provenance:
    digest: str
    short: str


@dataclass(frozen=True)
class Primitive:
    text: str


@dataclass(frozen=True)
class Composite:
    tag: str
    items: tuple["Encoded", ...]


Encoded = Union[Provenance, Primitive, Composite]

EncodedArgs = tuple[tuple[str, Encoded], ...]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def encode_arg(obj: object, _depth: int = 0) -> Encoded | Error:
    if _depth > _MAX_DEPTH:
        return Error("nesting exceeds depth limit (self-referential container?)")
    tag = getattr(obj, TAG_ATTR, None)
    if isinstance(tag, CacheKey):
        return Provenance(digest=tag.digest, short=tag.short)
    if isinstance(obj, Enum):
        return Primitive(f"{type(obj).__qualname__}.{obj.name}")
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return Primitive(repr(obj))
    if isinstance(obj, (tuple, list)):
        items = []
        for element in obj:
            encoded = encode_arg(element, _depth + 1)
            if isinstance(encoded, Error):
                return encoded
            items.append(encoded)
        kind = "tuple" if isinstance(obj, tuple) else "list"
        return Composite(kind, tuple(items))
    if isinstance(obj, MappingABC):
        pairs = []
        for key, value in obj.items():
            encoded_key = encode_arg(key, _depth + 1)
            if isinstance(encoded_key, Error):
                return encoded_key
            encoded_value = encode_arg(value, _depth + 1)
            if isinstance(encoded_value, Error):
                return encoded_value
            pairs.append(Composite("pair", (encoded_key, encoded_value)))
        pairs.sort(key=fingerprint)
        return Composite("dict", tuple(pairs))
    return Error(f"un-encodable argument of type {type(obj).__qualname__}")


def fingerprint(encoded: Encoded) -> str:
    match encoded:
        case Provenance(digest=digest):
            return _sha(f"prov|{digest}")
        case Primitive(text=text):
            return _sha(f"prim|{text}")
        case Composite(tag=tag, items=items):
            return _sha("|".join((tag, *(fingerprint(item) for item in items))))


def display(encoded: Encoded) -> str:
    """Compact rendering for the debug trace: provenance args as ◆short-key."""
    match encoded:
        case Provenance(short=short):
            return f"◆{short}"
        case Primitive(text=text):
            return text
        case Composite(tag="pair", items=(key, value)):
            return f"{display(key)}: {display(value)}"
        case Composite(tag=tag, items=items):
            inner = ", ".join(display(item) for item in items)
            match tag:
                case "tuple":
                    return f"({inner})"
                case "list":
                    return f"[{inner}]"
                case _:
                    return f"{{{inner}}}"


def encode_arguments(arguments: Mapping[str, object]) -> EncodedArgs | Error:
    """Encode signature-bound, defaults-applied arguments in signature order."""
    encoded = []
    for name, value in arguments.items():
        result = encode_arg(value)
        if isinstance(result, Error):
            return Error(f"arg '{name}': {result.message}")
        encoded.append((name, result))
    return tuple(encoded)


def key_of(qualname: str, closure_hash: str, encoded: EncodedArgs) -> CacheKey:
    lines = [qualname, closure_hash]
    lines.extend(f"{name}={fingerprint(value)}" for name, value in encoded)
    return CacheKey(qualname=qualname, digest=_sha("\n".join(lines)))
