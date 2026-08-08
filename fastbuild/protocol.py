"""Websocket protocol v1: frozen pydantic models ↔ JSON wire dicts.

Every message travels inside the versioned envelope {"v": 1, "type": ...}.
Client messages are untrusted input: a discriminated union validates them in
strict mode, with failures returned as values rather than raised.
"""

from typing import Annotated, Any, Literal, Mapping, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from fastbuild.errors import Error

PROTOCOL_VERSION = 1


class _Message(BaseModel):
    model_config = ConfigDict(frozen=True)
    v: Literal[1] = PROTOCOL_VERSION


class PartSchema(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    params: tuple[dict[str, Any], ...]


class Init(_Message):
    type: Literal["init"] = "init"
    parts: tuple[PartSchema, ...]
    selected: str


class ModelUpdate(_Message):
    type: Literal["model_update"] = "model_update"
    part: str
    shapes: Mapping[str, Any]


class Building(_Message):
    type: Literal["build_status"] = "build_status"
    state: Literal["building"] = "building"


class BuildOk(_Message):
    type: Literal["build_status"] = "build_status"
    state: Literal["ok"] = "ok"
    timings: Mapping[str, float]
    cache: Mapping[str, Any] | None = None


class BuildError(_Message):
    type: Literal["build_status"] = "build_status"
    state: Literal["error"] = "error"
    error: str


ServerMessage = Union[Init, ModelUpdate, Building, BuildOk, BuildError]


class SetParam(_Message):
    type: Literal["set_param"] = "set_param"
    part: str
    name: str
    value: Any
    final: bool


class SelectPart(_Message):
    type: Literal["select_part"] = "select_part"
    part: str


ClientMessage = Union[SetParam, SelectPart]

_client_messages: TypeAdapter[ClientMessage] = TypeAdapter(
    Annotated[ClientMessage, Field(discriminator="type")]
)


def encode(msg: ServerMessage) -> dict[str, Any]:
    return msg.model_dump(mode="json")


def _first_error(exc: ValidationError) -> str:
    detail = exc.errors()[0]
    location = ".".join(str(part) for part in detail["loc"]) or "message"
    return f"{location}: {detail['msg']}"


def parse_client(data: Any) -> ClientMessage | Error:
    try:
        return _client_messages.validate_python(data, strict=True)
    except ValidationError as exc:
        return Error(_first_error(exc))
