"""Map ``inspect.Parameter`` → ``typer.Option``/``typer.Argument`` annotations.

The decorator captures the original function. The builder needs to walk that
function's signature and produce a Typer-compatible signature for the
synthesized CLI command. This module owns that translation.

Supported types (verified by ``tests/unit/test_dual_mode_params.py``):

* ``str`` / ``int`` / ``float`` / ``bool`` — direct Typer options.
* ``pathlib.Path`` — Typer Path option with no existence check.
* ``Optional[T]`` / ``T | None`` — required when no default, optional with
  ``None`` default otherwise.
* ``list[T]`` — multi-value ``--name foo --name bar`` option.
* ``list[Literal[...]]`` — multi-value choice: each repeated value is coerced
  to the literal's homogeneous scalar and validated against the choice set
  (Typer cannot render ``list[Literal[...]]`` natively).
* ``Literal["a", "b"]`` — Typer choice via Click.
* ``dict`` / ``dict[K, V]`` — a top-level dict can't be a flat Typer option, so
  it surfaces as a single ``--<name>-json '<json>'`` blob parsed with
  ``json.loads`` (mirrors the per-field Pydantic blob).
* ``pydantic.BaseModel`` — flattened to individual options when the model
  has ≤ :data:`PYDANTIC_FLATTEN_THRESHOLD` fields, else accepts a
  ``--params '<json>'`` blob that's parsed back into a model instance.
* ``Annotated[T, typer.Argument(...)]`` — mapped to a **positional** CLI
  argument (``cmd VALUE``) instead of a ``--flag``, so the primary
  identifier reads naturally. ``Annotated[T, typer.Option(...)]`` keeps the
  flag behavior. Either way the MCP tool's input schema is unaffected:
  FastMCP ignores the Typer marker when building the schema, so ``T`` stays
  a normal field there (required/optional per its default).
* ``fastmcp.Context`` — not exposed; replaced by a ``CliContext`` shim by
  the builder at call time.

Other annotations pass through best-effort to Typer; Typer raises at
``app()`` time if it cannot map them.
"""

from __future__ import annotations

import inspect
import json
import types
import typing
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, get_args, get_origin

import typer

__all__ = [
    "PYDANTIC_FLATTEN_THRESHOLD",
    "PYDANTIC_PARAMS_OPTION_NAME",
    "_JsonParam",
    "_PydanticFlatten",
    "iter_typer_params",
    "validate_supported_annotation",
]

PYDANTIC_FLATTEN_THRESHOLD: int = 6
"""Pydantic models with ≤ this many fields are flattened to individual options.

Models with more fields fall back to accepting a single ``--params '<json>'``
option whose value is parsed into a model instance at call time.
"""

PYDANTIC_PARAMS_OPTION_NAME: str = "params"
"""Synthetic CLI option name used for the ``--params <json>`` escape hatch."""


def iter_typer_params(
    fn: Callable[..., Any],
) -> tuple[list[inspect.Parameter], list[inspect.Parameter], list[str]]:
    """Return the Typer-mapped signature for ``fn``.

    The returned tuple is ``(typer_params, original_params, context_params)``:

    * ``typer_params`` — the new :class:`inspect.Parameter` list the
      synthesized Typer command should advertise (Pydantic models flattened
      or replaced by ``--params``, Context params removed).
    * ``original_params`` — the original parameter list from ``fn``,
      preserved so the builder can rebuild call kwargs.
    * ``context_params`` — names of parameters whose annotation resolved to
      :class:`fastmcp.Context`; the builder injects a :class:`CliContext`
      for each.

    Annotations are resolved via :func:`typing.get_type_hints` with
    ``include_extras=True``. ``inspect.signature(fn, eval_str=True)``
    cannot be used here: when ``fastmcp.tool()`` (or any other decorator)
    caches a stringified signature on ``fn.__signature__``,
    ``inspect.signature`` returns the cached object verbatim — the
    ``eval_str=True`` flag never runs against the raw annotations and
    types come back as :class:`typing.ForwardRef` instances, which Typer
    rejects with ``RuntimeError: Type not yet supported``.
    :func:`typing.get_type_hints` always re-evaluates string annotations
    against ``fn.__globals__`` regardless of the cached signature state;
    ``include_extras=True`` preserves any ``Annotated[...]`` metadata so
    user-supplied :class:`typer.Option` decls survive the round-trip.
    """
    sig = inspect.signature(fn)
    resolved_hints = _resolve_hints(fn)
    original_params = [
        param.replace(annotation=resolved_hints.get(param.name, param.annotation))
        for param in sig.parameters.values()
    ]
    typer_params: list[inspect.Parameter] = []
    context_params: list[str] = []

    for param in original_params:
        if _is_context_annotation(param.annotation):
            context_params.append(param.name)
            continue

        flatten_info = _PydanticFlatten.from_parameter(param)
        if flatten_info is not None:
            typer_params.extend(flatten_info.synthesize_params())
            continue

        json_param = _JsonParam.from_parameter(param)
        if json_param is not None:
            typer_params.append(json_param.synthesize_param())
            continue

        typer_params.append(_to_typer_parameter(param))

    return typer_params, original_params, context_params


def _resolve_hints(fn: Callable[..., Any]) -> dict[str, Any]:
    """Return ``fn``'s annotations resolved against its module globals.

    Uses :func:`typing.get_type_hints` with ``include_extras=True`` so
    PEP 563-stringified annotations (``from __future__ import
    annotations``) are evaluated at decoration time and any
    :class:`typing.Annotated` metadata is preserved for the Typer
    passthrough path. Falls back to the raw ``__annotations__`` dict
    when resolution fails (e.g. a forward reference to a private type)
    so the caller still sees the strings rather than raising.
    """
    try:
        return typing.get_type_hints(fn, include_extras=True)
    except Exception:
        return dict(getattr(fn, "__annotations__", {}))


def validate_supported_annotation(annotation: Any, *, param_name: str, fn_name: str) -> None:
    """Raise ``TypeError`` for parameter annotations the framework can't map.

    Called once per non-Context parameter at ``@dual_mode_tool``
    decoration time so unsupported types fail fast with the offending
    parameter name in the message — the alternative is a confusing
    Typer "Type not yet supported" / "Union types not supported"
    runtime error at first CLI invocation. Currently rejects:

    * ``set[T]`` / ``frozenset[T]`` — Typer cannot render them as
      multi-value options. Use ``list[T]`` instead.
    * Non-``Optional`` ``Union[T, U]`` — Typer rejects unions outright.
      Only ``Optional[T]`` (``T | None``) is supported because that
      maps to "may be None at the call site" rather than "may be one
      of multiple types".

    All other annotations pass through; the per-type fallbacks in
    :func:`_to_typer_parameter` and :class:`_PydanticFlatten` handle
    them, and exotic-but-Typer-compatible types (e.g. user-supplied
    ``Annotated[T, typer.Option(...)]``) keep working.

    One exception (#110): a user-supplied ``typer.Argument(...)`` marker turns
    the parameter into a **positional** CLI argument, and Click can only render
    positionals for types that map to a single scalar token. A Pydantic model
    or a non-``str`` ``Literal`` positional builds fine but crashes at
    *invoke* time — including ``--help`` — with a raw framework traceback (the
    int-``Literal`` coercion at ``_to_typer_parameter`` and the
    ``_PydanticFlatten`` path only run on the option branch, never the Argument
    branch). Those are rejected here with an actionable message instead.
    """
    if annotation is inspect.Parameter.empty:
        return
    if _has_typer_metadata(annotation):
        # A user-supplied ``typer.Argument(...)`` → positional CLI argument.
        # ``typer.Option(...)`` and every other marker stays authoritative and
        # passes through untouched (only positionals have the scalar-token
        # restriction). ``ArgumentInfo`` is a ``ParameterInfo`` subtype, so it is
        # also matched by ``_has_typer_metadata`` above.
        if _has_typer_argument_metadata(annotation):
            _reject_unsupported_positional(annotation, param_name=param_name, fn_name=fn_name)
        return
    if _is_context_annotation(annotation):
        return

    inner, is_optional = _unwrap_optional(annotation)
    if is_optional:
        validate_supported_annotation(inner, param_name=param_name, fn_name=fn_name)
        return

    origin = get_origin(inner)
    if origin in (set, frozenset):
        raise TypeError(
            f"dual_mode_tool: parameter {param_name!r} on {fn_name!r} is annotated "
            f"as {_annotation_name(annotation)} ({annotation!r}); set/frozenset "
            f"types are not supported because Typer cannot render them as a CLI "
            f"option. Use list[...] instead."
        )
    if origin is types.UnionType or origin is _typing_union():
        raise TypeError(
            f"dual_mode_tool: parameter {param_name!r} on {fn_name!r} is annotated "
            f"as {_annotation_name(annotation)} ({annotation!r}); non-Optional "
            f"unions are not supported by Typer. Only Optional[T] (T | None) is "
            f"allowed — pick one concrete type for the CLI surface."
        )


def _reject_unsupported_positional(annotation: Any, *, param_name: str, fn_name: str) -> None:
    """Reject a positional (``typer.Argument``) annotation Click can't render (#110).

    A positional CLI argument is parsed from a single bare token, so Click can
    only build one for a scalar-token type (``str`` / ``int`` / ``float`` /
    ``bool``, ``pathlib.Path``, an ``Enum``, or an all-``str`` ``Literal``). Two
    shapes build without error but blow up at invoke time — including on
    ``--help`` — so they are caught here at decoration time instead:

    * a Pydantic model (``Annotated[SomeModel, typer.Argument()]``) — the
      :class:`_PydanticFlatten` path is skipped for ``Annotated[...]`` (it is not
      a ``type``), so the raw model annotation reaches Typer and raises
      ``RuntimeError: Type not yet supported``;
    * a non-``str`` ``Literal`` (``Annotated[Literal[1, 2, 3], typer.Argument()]``)
      — the int-``Literal`` scalar coercion only runs on the option branch, so
      Click tries to build a ``Choice`` over the raw ints and raises
      ``TypeError: sequence item 0: expected str instance, int found``.

    ``str``-``Literal`` and ``Enum`` positionals are supported and pass through.
    """
    inner = getattr(annotation, "__origin__", annotation)
    inner, _ = _unwrap_optional(inner)
    if _is_pydantic_model(inner):
        raise TypeError(
            f"dual_mode_tool: positional parameter {param_name!r} on {fn_name!r} is a "
            f"Pydantic model ({_annotation_name(inner)}) behind typer.Argument(); a model "
            f"cannot be a CLI positional. Use it as an option instead — drop the Argument "
            f"marker (the model is then flattened to per-field options or a --params JSON "
            f"blob) or use typer.Option(...)."
        )
    if _is_literal(inner):
        scalar = _literal_homogeneous_scalar(get_args(inner))
        if scalar is not str:
            raise TypeError(
                f"dual_mode_tool: positional parameter {param_name!r} on {fn_name!r} is "
                f"annotated as {inner!r} behind typer.Argument(); only an all-str Literal "
                f"can be a CLI positional (Click parses a positional token as a string "
                f"before the choice check). Use it as an option (typer.Option(...)), or use "
                f"a str Literal / Enum."
            )


# ---------------------------------------------------------------------------
# Per-parameter conversion
# ---------------------------------------------------------------------------


def _to_typer_parameter(
    param: inspect.Parameter,
    *,
    help_override: str | None = None,
) -> inspect.Parameter:
    """Build a Typer-friendly ``inspect.Parameter`` for one regular argument.

    ``help_override`` is used by the Pydantic flattening path to surface
    ``Field(description=...)`` text on the synthesized CLI option;
    callers that want the auto-generated type-hint help (the regular
    decoration path) leave it ``None``.
    """
    annotation = param.annotation
    default = param.default
    has_default = default is not inspect.Parameter.empty

    # A user-supplied Typer marker in the Annotated metadata is authoritative.
    # We preserve the parameter verbatim — Typer renders the right Click
    # parameter from the marker subtype carried in the metadata. We only force
    # KEYWORD_ONLY so the synthesized signature stays valid once the builder
    # appends the shared ``--json`` flag; Click still renders Arguments
    # positionally regardless of the Python-level parameter kind.
    #
    # In every case the MCP tool's input schema is left untouched: FastMCP
    # ignores the Typer marker when building the tool schema, so the parameter
    # remains a normal field there (required/optional per its default). This
    # is the critical invariant for positional args — see
    # ``tests/unit/test_dual_mode_builder.py::TestPositionalArgument``.
    if _has_typer_metadata(annotation):
        # A user-supplied Typer marker is authoritative — preserve the parameter
        # verbatim. ``typer.Argument(...)`` (``ArgumentInfo``) renders a positional
        # ``cmd VALUE``; ``typer.Option(...)`` (or any other ``ParameterInfo``)
        # renders a ``--flag``. ``ArgumentInfo``/``OptionInfo`` are both
        # ``ParameterInfo`` subtypes matched by ``_has_typer_metadata``, and both
        # map to a single KEYWORD_ONLY Python parameter (Click reads the marker
        # subtype to decide positional-vs-flag, independent of the Python kind).
        # The dedicated ``_has_typer_argument_metadata`` predicate is still used by
        # the decoration-time positional-type guard in ``validate_supported_annotation``.
        return param.replace(kind=inspect.Parameter.KEYWORD_ONLY)

    inner_type, is_optional = _unwrap_optional(annotation)

    # ``Optional[T]`` without an explicit ``= None`` default is still required
    # at the CLI (the annotation declares "may be None"; the caller chose not
    # to make that the default). ``Optional[T] = None`` is what makes the
    # option optional.
    resolved_default: Any
    if has_default:
        resolved_default = default
    else:
        resolved_default = ...

    if _is_literal(inner_type):
        choices = tuple(get_args(inner_type))
        choice_help = help_override or f"One of: {', '.join(repr(c) for c in choices)}."
        scalar_type = _literal_homogeneous_scalar(choices)
        if scalar_type is not None and scalar_type is not str:
            # Typer's native ``Literal[1, 2, 3]`` handling builds a
            # ``click.Choice`` over the raw int values, but Click parses
            # the user-supplied flag as a string and compares it to the
            # ints — so every valid input is rejected. Replace the
            # annotation with the homogeneous scalar type (so Click
            # coerces the input) plus a callback that validates the
            # coerced value is one of the literal choices.
            ann_type = scalar_type | None if is_optional else scalar_type
            return _build_kw_only(
                param.name,
                _make_option(
                    param.name,
                    help=choice_help,
                    callback=_make_literal_validator(choices),
                ),
                ann_type,
                default=resolved_default,
            )
        return _build_kw_only(
            param.name,
            _make_option(param.name, help=choice_help),
            inner_type if not is_optional else inner_type | None,
            default=resolved_default,
        )

    if _is_list_type(inner_type):
        item_type = _list_item_type(inner_type)
        list_default = default if has_default else []
        if _is_literal(item_type):
            # ``list[Literal[...]]`` (#111). Typer cannot render it natively
            # (``AssertionError: List types with complex sub-types``), so mirror
            # the scalar-``Literal`` trick: surface the list item type as the
            # homogeneous scalar (so Click coerces each token) and attach a
            # callback that validates every coerced item is one of the choices.
            return _build_list_literal_param(
                param.name,
                item_type,
                help_override=help_override,
                default=list_default,
            )
        option = _make_option(
            param.name,
            help=help_override
            or f"Multi-value option ({_annotation_name(item_type)}); repeat for each entry.",
        )
        # Lists default to ``[]`` when not provided so users don't need to
        # pass an empty multi-value flag to invoke a tool with no entries.
        return _build_kw_only(
            param.name,
            option,
            list[item_type],  # type: ignore[valid-type]
            default=list_default,
        )

    if inner_type is Path:
        return _build_kw_only(
            param.name,
            _make_option(param.name, help=help_override or "Filesystem path."),
            Path | None if is_optional else Path,
            default=resolved_default,
        )

    if inner_type is bool:
        # Bool default falls back to ``False`` (rather than required) so the
        # flag has the usual on/off semantics every CLI agent expects.
        return _build_kw_only(
            param.name,
            _make_option(param.name, help=help_override or "Boolean flag."),
            bool,
            default=default if has_default else False,
        )

    if inner_type in (str, int, float):
        return _build_kw_only(
            param.name,
            _make_option(param.name, help=help_override or f"{inner_type.__name__} option."),
            inner_type | None if is_optional else inner_type,
            default=resolved_default,
        )

    # Fallback: pass annotation through to Typer; Typer will raise at app() time
    # if it cannot handle it. Default value (if any) is preserved. The decorator
    # validates supported annotations up front (see ``_validate_supported_annotation``)
    # so this branch is only reached for user-supplied custom types.
    return _build_kw_only(
        param.name,
        _make_option(param.name, help=help_override or f"{_annotation_name(annotation)} option."),
        annotation,
        default=resolved_default,
    )


def _make_option(
    name: str,
    *,
    help: str,
    callback: Callable[[Any], Any] | None = None,
) -> typer.models.OptionInfo:
    """Build an ``OptionInfo`` with the shared CLI naming convention.

    ``foo_bar`` becomes ``--foo-bar``. We construct ``OptionInfo``
    directly with ``default=...`` (required sentinel) and
    ``param_decls=(long_flag,)`` so the resulting object is in the same
    shape Typer normalizes ``Annotated[T, Option(default, *decls)]`` to
    at parse time. The function-signature default — set by
    :func:`_build_kw_only` — overrides the sentinel.

    Directly using :class:`typer.models.OptionInfo` (instead of the
    :func:`typer.Option` factory) avoids the legacy
    ``Option(default, *decls)`` positional-arg ambiguity where
    ``Option("--foo")`` would store ``"--foo"`` as the default value.

    ``callback`` is forwarded as the ``OptionInfo.callback`` so caller-
    supplied validation (e.g. ``Literal[int]`` membership) runs after
    Click coerces the raw input.
    """
    long_flag = f"--{name.replace('_', '-')}"
    return typer.models.OptionInfo(
        default=...,
        param_decls=(long_flag,),
        help=help,
        callback=callback,
    )


def _build_kw_only(
    name: str,
    option: typer.models.OptionInfo,
    annotation: Any,
    default: Any,
) -> inspect.Parameter:
    """Wrap ``annotation`` in ``Annotated[..., option]`` and return a Parameter.

    ``default`` is the function-side default; ``...`` (Ellipsis) marks the
    option as required so Typer raises a usage error when it's missing
    rather than silently passing ``None``.
    """
    return inspect.Parameter(
        name=name,
        kind=inspect.Parameter.KEYWORD_ONLY,
        default=default,
        annotation=Annotated[annotation, option],
    )


# ---------------------------------------------------------------------------
# Type predicates
# ---------------------------------------------------------------------------


def _unwrap_optional(annotation: Any) -> tuple[Any, bool]:
    """Split ``T | None`` / ``Optional[T]`` into ``(T, True)``.

    Plain types pass through as ``(T, False)``. Union types with more than
    one non-``None`` member are returned as-is (we don't reduce them).
    """
    origin = get_origin(annotation)
    if origin is types.UnionType or origin is _typing_union():
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0], True
    return annotation, False


def _typing_union() -> Any:
    """Return ``typing.Union`` lazily to avoid importing it at module top."""
    import typing

    return typing.Union


def _is_literal(annotation: Any) -> bool:
    return get_origin(annotation) is Literal


def _literal_homogeneous_scalar(choices: tuple[Any, ...]) -> type | None:
    """Return the homogeneous scalar type of a ``Literal[...]`` if any.

    Returns ``int``, ``float``, ``bool``, or ``str`` when every choice
    has the same primitive type; ``None`` for empty or mixed literals.
    Used to detect ``Literal[1, 2, 3]`` so the framework can coerce
    user-supplied flag values to the literal's type before Click
    compares them to the choice list.
    """
    if not choices:
        return None
    types = {type(c) for c in choices}
    if len(types) != 1:
        return None
    scalar = next(iter(types))
    if scalar in (int, float, bool, str):
        return scalar
    return None


def _make_literal_validator(choices: tuple[Any, ...]) -> Callable[[Any], Any]:
    """Build a Typer ``callback`` that validates membership in ``choices``.

    Click coerces the raw flag value to the parameter's annotated
    type first; the callback runs on the coerced value, so a user
    typing ``--level 4`` against ``Literal[1, 2, 3]`` sees
    ``"4 is not one of 1, 2, 3"`` rather than a generic Click error.
    Returns ``None`` unchanged so optional parameters keep their
    "no value" semantics.
    """
    choices_repr = ", ".join(repr(c) for c in choices)

    def _validate(value: Any) -> Any:
        if value is None:
            return value
        if value not in choices:
            raise typer.BadParameter(f"{value!r} is not one of {choices_repr}")
        return value

    return _validate


def _build_list_literal_param(
    name: str,
    item_literal: Any,
    *,
    help_override: str | None,
    default: Any,
) -> inspect.Parameter:
    """Build a multi-value ``--name`` option for a ``list[Literal[...]]`` param (#111).

    Typer rejects ``list[Literal[...]]`` outright (``AssertionError: List types
    with complex sub-types``), so the synthesized option uses the literal's
    homogeneous scalar type as the list item type (``list[str]`` / ``list[int]``
    / ``list[float]``) — letting Click coerce each repeated token — plus a
    callback that validates every coerced item is one of the literal's choices.
    A mixed-type Literal (e.g. ``Literal["a", 1]``) has no homogeneous scalar and
    is rejected with a clear error.
    """
    choices = tuple(get_args(item_literal))
    scalar_type = _literal_homogeneous_scalar(choices)
    if scalar_type is None:
        raise TypeError(
            f"dual_mode_tool: parameter {name!r} is annotated as a list of a "
            f"mixed-type Literal ({item_literal!r}); only a homogeneous-scalar "
            f"Literal (all str, all int, or all float) can be a multi-value CLI "
            f"choice. Use one consistent scalar type for the literal members."
        )
    choices_help = ", ".join(repr(c) for c in choices)
    option = _make_option(
        name,
        help=help_override or f"Multi-value choice; repeat for each entry. One of: {choices_help}.",
        callback=_make_list_literal_validator(choices),
    )
    return _build_kw_only(
        name,
        option,
        list[scalar_type],  # type: ignore[valid-type]
        default=default,
    )


def _make_list_literal_validator(choices: tuple[Any, ...]) -> Callable[[Any], Any]:
    """Build a Typer ``callback`` validating every item of a multi-value option.

    Click coerces each repeated token to the option's scalar item type and then
    invokes the callback once with the full sequence, so this rejects the first
    out-of-set item with ``"<item> is not one of <choices>"`` — the multi-value
    analogue of :func:`_make_literal_validator`. ``None`` / empty sequences pass
    through unchanged so optional / unspecified lists keep their semantics.
    """
    choices_repr = ", ".join(repr(c) for c in choices)

    def _validate(value: Any) -> Any:
        if value is None:
            return value
        for item in value:
            if item not in choices:
                raise typer.BadParameter(f"{item!r} is not one of {choices_repr}")
        return value

    return _validate


def _is_list_type(annotation: Any) -> bool:
    origin = get_origin(annotation)
    return origin in (list, tuple)


def _list_item_type(annotation: Any) -> Any:
    args = get_args(annotation)
    if args:
        return args[0]
    return str


def _is_context_annotation(annotation: Any) -> bool:
    """True iff ``annotation`` resolves to ``fastmcp.Context``."""
    if annotation is inspect.Parameter.empty:
        return False
    try:
        from fastmcp import Context
    except Exception:
        return False
    if annotation is Context:
        return True
    inner, _ = _unwrap_optional(annotation)
    return inner is Context


def _has_typer_metadata(annotation: Any) -> bool:
    """True iff the user already supplied a ``typer.Option`` / ``typer.Argument``."""
    metadata = getattr(annotation, "__metadata__", None)
    if not metadata:
        return False
    return any(
        isinstance(
            m, typer.models.ParameterInfo | typer.models.OptionInfo | typer.models.ArgumentInfo
        )
        for m in metadata
    )


def _has_typer_argument_metadata(annotation: Any) -> bool:
    """True iff the user supplied a ``typer.Argument`` in the Annotated metadata.

    ``typer.Argument(...)`` produces a :class:`typer.models.ArgumentInfo`.
    Detecting it lets the CLI projection route the parameter to a **positional**
    Typer argument (``cmd VALUE``) rather than a ``--flag`` option, so the
    primary identifier reads naturally on the command line. The MCP tool's
    input schema is unaffected — FastMCP ignores the Typer marker when building
    the schema, leaving the parameter a normal field there.
    """
    metadata = getattr(annotation, "__metadata__", None)
    if not metadata:
        return False
    return any(isinstance(m, typer.models.ArgumentInfo) for m in metadata)


def _annotation_name(annotation: Any) -> str:
    return getattr(annotation, "__name__", str(annotation))


# ---------------------------------------------------------------------------
# Top-level ``dict`` → ``--<name>-json`` escape hatch (#111)
# ---------------------------------------------------------------------------

_JSON_PARAM_DEFAULT_SENTINEL = "__use_param_default__"
"""Sentinel default for an OPTIONAL top-level dict param's ``--<name>-json`` option.

Lets the rehydration path distinguish "user did not pass the flag" (apply the
function's own default) from "user passed an explicit JSON value" — mirroring
:data:`_COMPLEX_FIELD_DEFAULT_SENTINEL` for the per-field Pydantic blob.
"""


@dataclass(slots=True)
class _JsonParam:
    """A top-level ``dict`` parameter surfaced as a single ``--<name>-json`` blob (#111).

    Typer rejects a bare ``dict`` annotation (``RuntimeError: Type not yet
    supported``), and the existing ``--<field>-json`` fallback only applies to
    Pydantic model *fields*, not a top-level ``dict`` parameter. This mirrors
    that fallback for a top-level ``dict``: the CLI exposes ``--<name>-json
    '<json>'``, parsed with ``json.loads`` at call time, so a tool with a
    ``dict`` filter param (e.g. awx-mcp's ``awx_list_resources(filters: dict)``)
    becomes synthesizable. The MCP tool's input schema is unaffected — FastMCP
    maps ``dict`` natively.
    """

    param_name: str
    required: bool

    @classmethod
    def from_parameter(cls, param: inspect.Parameter) -> _JsonParam | None:
        """Return a descriptor for ``param`` if it is a (bare or parameterized) dict.

        Returns ``None`` for non-dict annotations so the caller falls through to
        the regular Typer mapping. ``Optional[dict]`` is detected too (the
        wrapped ``dict`` is what matters for the CLI surface).
        """
        inner, _ = _unwrap_optional(param.annotation)
        if inner is dict or get_origin(inner) is dict:
            return cls(
                param_name=param.name,
                required=param.default is inspect.Parameter.empty,
            )
        return None

    def option_name(self) -> str:
        return f"{self.param_name}_json"

    def _flag(self) -> str:
        return f"--{self.option_name().replace('_', '-')}"

    def synthesize_param(self) -> inspect.Parameter:
        """Return the single ``--<name>-json`` Typer option this dict expands to."""
        option = _make_option(
            self.option_name(),
            help=(
                f"JSON object for the {self.param_name!r} dict parameter — pass the value "
                f"as a single JSON blob (e.g. {self._flag()} '{{...}}')."
            ),
        )
        return inspect.Parameter(
            name=self.option_name(),
            kind=inspect.Parameter.KEYWORD_ONLY,
            default=... if self.required else _JSON_PARAM_DEFAULT_SENTINEL,
            annotation=Annotated[str, option],
        )

    def build_from_typer_kwargs(self, typer_kwargs: dict[str, Any]) -> tuple[bool, Any]:
        """Parse the ``--<name>-json`` blob back into a dict for the call kwargs.

        Returns ``(present, value)``. ``present=False`` means the user did not
        pass ``--<name>-json`` for an OPTIONAL dict param, so the builder skips
        the kwarg and the function's own default applies. Otherwise ``value`` is
        the parsed dict (or ``None`` for an explicit ``null``). Invalid JSON or a
        non-object payload raises :class:`typer.BadParameter` so the user sees a
        Click-style error rather than a stack trace.
        """
        raw = typer_kwargs.pop(self.option_name(), _JSON_PARAM_DEFAULT_SENTINEL)
        if raw is _JSON_PARAM_DEFAULT_SENTINEL:
            return False, None
        if raw is None:
            return True, None
        flag = self._flag()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise typer.BadParameter(
                f"{flag} must be valid JSON: {exc}",
                param_hint=flag,
            ) from exc
        if not isinstance(parsed, dict):
            raise typer.BadParameter(
                f"{flag} must be a JSON object (dict); got {type(parsed).__name__}.",
                param_hint=flag,
            )
        return True, parsed


# ---------------------------------------------------------------------------
# Pydantic model flattening / params blob
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _PydanticFlatten:
    """Describes how a Pydantic model parameter is exposed on the CLI.

    Two strategies:

    * ``flatten=True`` — each model field becomes its own Typer option with
      ``--<prefix>-<field>``. ``build_from_typer_kwargs`` re-bundles them
      back into a model instance.
    * ``flatten=False`` — a single ``--<prefix>-params`` option accepts a
      JSON blob that's parsed into the model.
    """

    param_name: str
    model_cls: type
    flatten: bool

    @classmethod
    def from_parameter(cls, param: inspect.Parameter) -> _PydanticFlatten | None:
        """Return a flatten descriptor for ``param`` if it's a Pydantic model.

        Returns ``None`` for non-model annotations so the caller falls
        through to the regular Typer mapping.
        """
        annotation, _ = _unwrap_optional(param.annotation)
        if not _is_pydantic_model(annotation):
            return None
        flatten = _model_field_count(annotation) <= PYDANTIC_FLATTEN_THRESHOLD
        return cls(param_name=param.name, model_cls=annotation, flatten=flatten)

    def synthesize_params(self) -> list[inspect.Parameter]:
        """Return the Typer-side parameters this descriptor expands to."""
        if not self.flatten:
            option = _make_option(
                self._params_option_name(),
                help=(
                    f"JSON object for {self.model_cls.__name__}; pass the "
                    f"model fields as a single JSON blob."
                ),
            )
            return [
                inspect.Parameter(
                    name=self._params_option_name(),
                    kind=inspect.Parameter.KEYWORD_ONLY,
                    default="{}",
                    annotation=Annotated[str, option],
                )
            ]

        params: list[inspect.Parameter] = []
        for field_name, field_info in _iter_model_fields(self.model_cls):
            cli_field_name = f"{self.param_name}_{field_name}"
            field_default = _field_default(field_info)
            field_required = _field_required(field_info)
            annotation = _field_annotation(field_info)
            description = _field_description(field_info)
            if _is_complex_field_type(annotation):
                # Nested Pydantic / list[Pydantic] / dict can't flatten to
                # primitive Typer options without crashing; fall back to a
                # per-field ``--<field>-json`` blob so sibling primitive
                # fields still flatten cleanly.
                params.append(
                    self._synthesize_complex_field_param(
                        cli_field_name=cli_field_name,
                        field_name=field_name,
                        annotation=annotation,
                        required=field_required,
                        description=description,
                    )
                )
                continue
            param = self._synthesize_field_param(
                cli_field_name=cli_field_name,
                annotation=annotation,
                default=field_default,
                required=field_required,
                description=description,
            )
            params.append(param)
        return params

    def build_from_typer_kwargs(self, typer_kwargs: dict[str, Any]) -> Any:
        """Re-bundle Typer kwargs into a Pydantic model instance."""
        if not self.flatten:
            blob = typer_kwargs.pop(self._params_option_name(), "{}") or "{}"
            try:
                parsed = json.loads(blob)
            except json.JSONDecodeError as exc:
                raise typer.BadParameter(
                    f"--{self._params_option_name().replace('_', '-')} must be valid JSON: {exc}",
                    param_hint=f"--{self._params_option_name().replace('_', '-')}",
                ) from exc
            return self.model_cls(**parsed)

        kwargs: dict[str, Any] = {}
        for field_name, field_info in _iter_model_fields(self.model_cls):
            cli_field_name = f"{self.param_name}_{field_name}"
            annotation = _field_annotation(field_info)
            if _is_complex_field_type(annotation):
                json_option_name = self._complex_field_option_name(cli_field_name)
                if json_option_name in typer_kwargs:
                    raw = typer_kwargs.pop(json_option_name)
                    parsed = self._parse_complex_field(
                        json_option_name=json_option_name,
                        field_name=field_name,
                        annotation=annotation,
                        raw=raw,
                    )
                    if parsed is not _COMPLEX_FIELD_USE_DEFAULT:
                        kwargs[field_name] = parsed
                continue
            if cli_field_name in typer_kwargs:
                kwargs[field_name] = typer_kwargs.pop(cli_field_name)
        return self.model_cls(**kwargs)

    def _params_option_name(self) -> str:
        return f"{self.param_name}_{PYDANTIC_PARAMS_OPTION_NAME}"

    @staticmethod
    def _complex_field_option_name(cli_field_name: str) -> str:
        return f"{cli_field_name}_json"

    def _synthesize_field_param(
        self,
        *,
        cli_field_name: str,
        annotation: Any,
        default: Any,
        required: bool,
        description: str | None = None,
    ) -> inspect.Parameter:
        """Build one flattened Typer option for a Pydantic field."""
        synthetic = inspect.Parameter(
            name=cli_field_name,
            kind=inspect.Parameter.KEYWORD_ONLY,
            default=default if not required else inspect.Parameter.empty,
            annotation=annotation,
        )
        return _to_typer_parameter(synthetic, help_override=description)

    def _synthesize_complex_field_param(
        self,
        *,
        cli_field_name: str,
        field_name: str,
        annotation: Any,
        required: bool,
        description: str | None,
    ) -> inspect.Parameter:
        """Build the per-field ``--<field>-json`` fallback option."""
        json_option_name = self._complex_field_option_name(cli_field_name)
        help_text = description or (
            f"JSON value for {self.model_cls.__name__}.{field_name} "
            f"({_annotation_name(annotation)})."
        )
        option = _make_option(json_option_name, help=help_text)
        return inspect.Parameter(
            name=json_option_name,
            kind=inspect.Parameter.KEYWORD_ONLY,
            default=inspect.Parameter.empty if required else _COMPLEX_FIELD_DEFAULT_SENTINEL,
            annotation=Annotated[str, option],
        )

    def _parse_complex_field(
        self,
        *,
        json_option_name: str,
        field_name: str,
        annotation: Any,
        raw: Any,
    ) -> Any:
        """Parse a ``--<field>-json`` blob and validate it against the field type."""
        if raw is _COMPLEX_FIELD_DEFAULT_SENTINEL:
            return _COMPLEX_FIELD_USE_DEFAULT
        if raw is None:
            return None
        flag = f"--{json_option_name.replace('_', '-')}"
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise typer.BadParameter(
                f"{flag} must be valid JSON: {exc}",
                param_hint=flag,
            ) from exc
        return _validate_complex_value(annotation, parsed, field_name=field_name, flag=flag)


def _is_pydantic_model(annotation: Any) -> bool:
    """True iff ``annotation`` is a Pydantic v2 ``BaseModel`` subclass."""
    try:
        from pydantic import BaseModel
    except Exception:
        return False
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


_COMPLEX_FIELD_DEFAULT_SENTINEL = "__use_field_default__"
"""Sentinel default for optional complex Pydantic fields.

Stored on the synthesized ``--<field>-json`` option so the rehydration
path can distinguish "user did not pass the flag" (use the model's
default) from "user passed an explicit value". Treated as the
"absent" marker by :meth:`_PydanticFlatten._parse_complex_field`.
"""

_COMPLEX_FIELD_USE_DEFAULT: Any = object()
"""Marker the parser returns to tell ``build_from_typer_kwargs`` to skip
populating the field — its model default applies."""


def _is_complex_field_type(annotation: Any) -> bool:
    """True iff a Pydantic field annotation needs the ``--<field>-json`` blob.

    The flatten path calls ``_to_typer_parameter`` which only knows how
    to map primitive scalars, ``list[primitive]``, ``Path``, ``Literal``,
    and ``Optional`` thereof. Anything else (nested Pydantic models,
    ``list[Pydantic]``, ``dict``, etc.) blows up Typer at app-build
    time when the synthesized signature reaches it. Detect those cases
    here and route them through the JSON fallback so sibling primitive
    fields keep flattening cleanly.
    """
    inner, _ = _unwrap_optional(annotation)
    if _is_pydantic_model(inner):
        return True
    origin = get_origin(inner)
    if origin in (list, tuple, set, frozenset):
        args = get_args(inner)
        if not args:
            return True
        item_inner, _ = _unwrap_optional(args[0])
        return item_inner not in (str, int, float, bool, Path)
    return origin is dict


def _validate_complex_value(annotation: Any, value: Any, *, field_name: str, flag: str) -> Any:
    """Validate ``value`` (already JSON-parsed) against a Pydantic field type.

    Direct Pydantic models use ``model_validate``; everything else
    (``list[Pydantic]``, ``dict[...]``, etc.) goes through Pydantic's
    ``TypeAdapter``. Validation errors are surfaced as
    :class:`typer.BadParameter` so the user sees a Click-style error
    instead of a Pydantic stack trace.
    """
    inner, _ = _unwrap_optional(annotation)
    try:
        if _is_pydantic_model(inner):
            return inner.model_validate(value)
        from pydantic import TypeAdapter

        return TypeAdapter(annotation).validate_python(value)
    except Exception as exc:
        raise typer.BadParameter(
            f"{flag} failed validation for field {field_name!r}: {exc}",
            param_hint=flag,
        ) from exc


def _model_field_count(model_cls: type) -> int:
    fields = getattr(model_cls, "model_fields", None)
    if fields is None:
        return 0
    return len(fields)


def _iter_model_fields(model_cls: type) -> list[tuple[str, Any]]:
    fields = getattr(model_cls, "model_fields", None)
    if fields is None:
        return []
    return list(fields.items())


def _field_default(field_info: Any) -> Any:
    """Return the field's default value (or ``...`` for required fields)."""
    if getattr(field_info, "is_required", lambda: False)():
        return inspect.Parameter.empty
    default = getattr(field_info, "default", inspect.Parameter.empty)
    if default is _pydantic_undefined():
        return inspect.Parameter.empty
    return default


def _field_required(field_info: Any) -> bool:
    is_required = getattr(field_info, "is_required", None)
    if callable(is_required):
        return bool(is_required())
    default = getattr(field_info, "default", inspect.Parameter.empty)
    return default is _pydantic_undefined()


def _field_annotation(field_info: Any) -> Any:
    return getattr(field_info, "annotation", Any)


def _field_description(field_info: Any) -> str | None:
    """Return ``field_info.description`` if present (Pydantic v2 / v1 compat).

    Used by the flattening path to surface ``Field(description="...")``
    text on the synthesized CLI option's ``--help`` instead of the
    generic ``"<type> option."`` placeholder.
    """
    description = getattr(field_info, "description", None)
    if description is None and hasattr(field_info, "field_info"):
        description = getattr(field_info.field_info, "description", None)
    if description:
        return str(description)
    return None


def _pydantic_undefined() -> Any:
    try:
        from pydantic_core import PydanticUndefined

        return PydanticUndefined
    except Exception:
        return object()
