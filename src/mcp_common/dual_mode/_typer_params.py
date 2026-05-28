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
* ``Literal["a", "b"]`` — Typer choice via Click.
* ``pydantic.BaseModel`` — flattened to individual options when the model
  has ≤ :data:`PYDANTIC_FLATTEN_THRESHOLD` fields, else accepts a
  ``--params '<json>'`` blob that's parsed back into a model instance.
* ``fastmcp.Context`` — not exposed; replaced by a ``CliContext`` shim by
  the builder at call time.

Other annotations pass through best-effort to Typer; Typer raises at
``app()`` time if it cannot map them.
"""

from __future__ import annotations

import inspect
import json
import types
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, get_args, get_origin

import typer

__all__ = [
    "CONTEXT_PARAM_SENTINEL",
    "PYDANTIC_FLATTEN_THRESHOLD",
    "PYDANTIC_PARAMS_OPTION_NAME",
    "_PydanticFlatten",
    "iter_typer_params",
]

PYDANTIC_FLATTEN_THRESHOLD: int = 6
"""Pydantic models with ≤ this many fields are flattened to individual options.

Models with more fields fall back to accepting a single ``--params '<json>'``
option whose value is parsed into a model instance at call time.
"""

PYDANTIC_PARAMS_OPTION_NAME: str = "params"
"""Synthetic CLI option name used for the ``--params <json>`` escape hatch."""

CONTEXT_PARAM_SENTINEL: str = "__cli_context__"
"""Reserved sentinel used internally to mark Context-shimmed parameters.

Real function parameter names are tracked alongside; this sentinel only
appears in the ``context_params`` list when a tool has no explicit Context
parameter but the builder should still inject one (currently unused — kept
as an extension point).
"""


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

    Annotations are evaluated with ``eval_str=True`` so callers using
    ``from __future__ import annotations`` (PEP 563) get the actual
    runtime types, not their string repr.
    """
    sig = inspect.signature(fn, eval_str=True)
    original_params = list(sig.parameters.values())
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

        typer_params.append(_to_typer_parameter(param))

    return typer_params, original_params, context_params


# ---------------------------------------------------------------------------
# Per-parameter conversion
# ---------------------------------------------------------------------------


def _to_typer_parameter(param: inspect.Parameter) -> inspect.Parameter:
    """Build a Typer-friendly ``inspect.Parameter`` for one regular argument."""
    annotation = param.annotation
    default = param.default
    has_default = default is not inspect.Parameter.empty

    # Already a typer-annotated parameter? respect it as-is.
    if _has_typer_metadata(annotation):
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
        # Typer natively expands ``Literal["a","b"]`` into a Click choice
        # type; pass the annotation straight through.
        choices = list(get_args(inner_type))
        return _build_kw_only(
            param.name,
            _make_option(
                param.name,
                help=f"One of: {', '.join(repr(c) for c in choices)}.",
            ),
            inner_type if not is_optional else inner_type | None,
            default=resolved_default,
        )

    if _is_list_type(inner_type):
        item_type = _list_item_type(inner_type)
        option = _make_option(
            param.name,
            help=f"Multi-value option ({_annotation_name(item_type)}); repeat for each entry.",
        )
        # Lists default to ``[]`` when not provided so users don't need to
        # pass an empty multi-value flag to invoke a tool with no entries.
        return _build_kw_only(
            param.name,
            option,
            list[item_type],  # type: ignore[valid-type]
            default=default if has_default else [],
        )

    if inner_type is Path:
        return _build_kw_only(
            param.name,
            _make_option(param.name, help="Filesystem path."),
            Path | None if is_optional else Path,
            default=resolved_default,
        )

    if inner_type is bool:
        # Bool default falls back to ``False`` (rather than required) so the
        # flag has the usual on/off semantics every CLI agent expects.
        return _build_kw_only(
            param.name,
            _make_option(param.name, help="Boolean flag."),
            bool,
            default=default if has_default else False,
        )

    if inner_type in (str, int, float):
        return _build_kw_only(
            param.name,
            _make_option(param.name, help=f"{inner_type.__name__} option."),
            inner_type | None if is_optional else inner_type,
            default=resolved_default,
        )

    # Fallback: pass annotation through to Typer; Typer will raise at app() time
    # if it cannot handle it. Default value (if any) is preserved.
    return _build_kw_only(
        param.name,
        _make_option(param.name, help=f"{_annotation_name(annotation)} option."),
        annotation,
        default=resolved_default,
    )


def _make_option(
    name: str,
    *,
    help: str,
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
    """
    long_flag = f"--{name.replace('_', '-')}"
    return typer.models.OptionInfo(
        default=...,
        param_decls=(long_flag,),
        help=help,
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


def _annotation_name(annotation: Any) -> str:
    return getattr(annotation, "__name__", str(annotation))


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

    @classmethod
    def for_param(cls, param: inspect.Parameter) -> _PydanticFlatten | None:
        """Re-derive the flatten descriptor at builder call time.

        Kept separate from :meth:`from_parameter` so the builder can call
        it on the same ``inspect.Parameter`` instance during call-kwarg
        rehydration without re-running annotation analysis twice.
        """
        return cls.from_parameter(param)

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
            param = self._synthesize_field_param(
                cli_field_name=cli_field_name,
                annotation=annotation,
                default=field_default,
                required=field_required,
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
        for field_name, _ in _iter_model_fields(self.model_cls):
            cli_field_name = f"{self.param_name}_{field_name}"
            if cli_field_name in typer_kwargs:
                kwargs[field_name] = typer_kwargs.pop(cli_field_name)
        return self.model_cls(**kwargs)

    def _params_option_name(self) -> str:
        return f"{self.param_name}_{PYDANTIC_PARAMS_OPTION_NAME}"

    def _synthesize_field_param(
        self,
        *,
        cli_field_name: str,
        annotation: Any,
        default: Any,
        required: bool,
    ) -> inspect.Parameter:
        """Build one flattened Typer option for a Pydantic field."""
        synthetic = inspect.Parameter(
            name=cli_field_name,
            kind=inspect.Parameter.KEYWORD_ONLY,
            default=default if not required else inspect.Parameter.empty,
            annotation=annotation,
        )
        return _to_typer_parameter(synthetic)


def _is_pydantic_model(annotation: Any) -> bool:
    """True iff ``annotation`` is a Pydantic v2 ``BaseModel`` subclass."""
    try:
        from pydantic import BaseModel
    except Exception:
        return False
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


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


def _pydantic_undefined() -> Any:
    try:
        from pydantic_core import PydanticUndefined

        return PydanticUndefined
    except Exception:
        return object()
