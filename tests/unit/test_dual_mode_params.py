"""Tests for the Python signature → Typer parameter mapping.

These tests deliberately do *not* use ``from __future__ import annotations``
because they define functions inline inside test bodies — a closure-local
type referenced via PEP 563 stringification can't be resolved from
``fn.__globals__`` at all (it lives only in the enclosing function's
locals). :func:`iter_typer_params` resolves annotations via
:func:`typing.get_type_hints` with ``include_extras=True``, which
re-evaluates strings against ``fn.__globals__`` regardless of any
cached ``__signature__``; the PEP 563 regression is covered explicitly
by ``tests/integration/test_dual_mode_e2e_future_annotations.py`` for
module-level callables (the realistic production case).
"""

import inspect
from pathlib import Path
from typing import Annotated, Literal

import pydantic
import pytest
import typer
from fastmcp import Context

from mcp_common.dual_mode._typer_params import (
    PYDANTIC_FLATTEN_THRESHOLD,
    _PydanticFlatten,
    iter_typer_params,
)


def _get_option(param: inspect.Parameter) -> typer.models.OptionInfo:
    """Extract the OptionInfo metadata from a Typer-mapped parameter."""
    metadata = getattr(param.annotation, "__metadata__", ())
    for m in metadata:
        if isinstance(m, typer.models.OptionInfo):
            return m
    raise AssertionError(f"No OptionInfo on {param.name}: {param.annotation!r}")


def _annotated_type(param: inspect.Parameter) -> type:
    """Return the underlying type wrapped by ``Annotated[T, ...]``."""
    return param.annotation.__origin__


class _SmallModel(pydantic.BaseModel):
    """Module-level test fixture so PEP 563 / forward-ref resolution works."""

    name: str
    count: int = 0


def _make_big_model() -> type[pydantic.BaseModel]:
    """Build a Pydantic model exceeding :data:`PYDANTIC_FLATTEN_THRESHOLD`."""
    attrs = {f"f{i}": (str, ...) for i in range(PYDANTIC_FLATTEN_THRESHOLD + 1)}
    return pydantic.create_model("_BigModel", **attrs)  # type: ignore[call-overload]


_BigModel = _make_big_model()


def _make_boundary_model() -> type[pydantic.BaseModel]:
    attrs = {f"f{i}": (str, ...) for i in range(PYDANTIC_FLATTEN_THRESHOLD)}
    return pydantic.create_model("_BoundaryModel", **attrs)  # type: ignore[call-overload]


_BoundaryModel = _make_boundary_model()


class TestPrimitives:
    def test_str_with_default(self) -> None:
        def fn(x: str = "alice") -> None: ...

        typer_params, _, _ = iter_typer_params(fn)
        assert typer_params[0].name == "x"
        assert typer_params[0].default == "alice"
        assert typer_params[0].kind is inspect.Parameter.KEYWORD_ONLY

    def test_int_with_default(self) -> None:
        def fn(x: int = 7) -> None: ...

        typer_params, _, _ = iter_typer_params(fn)
        assert typer_params[0].default == 7

    def test_float_with_default(self) -> None:
        def fn(x: float = 1.5) -> None: ...

        typer_params, _, _ = iter_typer_params(fn)
        assert typer_params[0].default == 1.5

    @pytest.mark.parametrize("default", [True, False])
    def test_bool_with_default(self, default: bool) -> None:
        def fn(x: bool = default) -> None: ...

        typer_params, _, _ = iter_typer_params(fn)
        assert typer_params[0].default == default

    @pytest.mark.parametrize("annotation", [str, int, float])
    def test_primitive_required(self, annotation: type) -> None:
        def fn(x: annotation) -> None: ...  # type: ignore[valid-type]

        typer_params, _, _ = iter_typer_params(fn)
        assert typer_params[0].default is ...

    def test_long_flag_uses_kebab(self) -> None:
        def fn(include_interfaces: bool = False) -> None: ...

        typer_params, _, _ = iter_typer_params(fn)
        opt = _get_option(typer_params[0])
        assert "--include-interfaces" in opt.param_decls


class TestOptional:
    def test_optional_str_with_default(self) -> None:
        def fn(name: str | None = None) -> None: ...

        typer_params, _, _ = iter_typer_params(fn)
        assert typer_params[0].default is None

    def test_optional_int_with_default(self) -> None:
        def fn(count: int | None = None) -> None: ...

        typer_params, _, _ = iter_typer_params(fn)
        assert typer_params[0].default is None

    def test_required_optional_without_default(self) -> None:
        """``Optional[T]`` without ``= None`` is still required at the CLI."""

        def fn(name: str | None) -> None: ...

        typer_params, _, _ = iter_typer_params(fn)
        assert typer_params[0].default is ...


class TestLiteral:
    def test_literal_passes_annotation_through(self) -> None:
        def fn(mode: Literal["fast", "slow"]) -> None: ...

        typer_params, _, _ = iter_typer_params(fn)
        param = typer_params[0]
        assert _annotated_type(param) == Literal["fast", "slow"]

    def test_literal_with_default(self) -> None:
        def fn(mode: Literal["fast", "slow"] = "fast") -> None: ...

        typer_params, _, _ = iter_typer_params(fn)
        assert typer_params[0].default == "fast"


class TestList:
    def test_list_default_is_empty(self) -> None:
        def fn(tags: list[str]) -> None: ...

        typer_params, _, _ = iter_typer_params(fn)
        assert typer_params[0].default == []

    def test_list_of_str_annotation(self) -> None:
        def fn(tags: list[str]) -> None: ...

        typer_params, _, _ = iter_typer_params(fn)
        ann = _annotated_type(typer_params[0])
        assert ann.__origin__ is list
        assert ann.__args__ == (str,)

    def test_list_of_int_annotation(self) -> None:
        def fn(ids: list[int]) -> None: ...

        typer_params, _, _ = iter_typer_params(fn)
        ann = _annotated_type(typer_params[0])
        assert ann.__origin__ is list
        assert ann.__args__ == (int,)

    def test_list_with_explicit_default(self) -> None:
        def fn(tags: list[str] = ["a", "b"]) -> None: ...  # noqa: B006

        typer_params, _, _ = iter_typer_params(fn)
        assert typer_params[0].default == ["a", "b"]


class TestPath:
    def test_required_path(self) -> None:
        def fn(p: Path) -> None: ...

        typer_params, _, _ = iter_typer_params(fn)
        assert typer_params[0].default is ...
        assert _annotated_type(typer_params[0]) is Path

    def test_optional_path(self) -> None:
        def fn(p: Path | None = None) -> None: ...

        typer_params, _, _ = iter_typer_params(fn)
        assert typer_params[0].default is None

    def test_path_with_default(self) -> None:
        def fn(p: Path = Path("/tmp")) -> None: ...

        typer_params, _, _ = iter_typer_params(fn)
        assert typer_params[0].default == Path("/tmp")


class TestContext:
    def test_context_param_is_excluded(self) -> None:
        def fn(ctx: Context, hostname: str) -> None: ...

        typer_params, original_params, context_params = iter_typer_params(fn)
        assert [p.name for p in typer_params] == ["hostname"]
        assert context_params == ["ctx"]
        assert [p.name for p in original_params] == ["ctx", "hostname"]


class TestPydanticFlatten:
    def test_small_model_is_flattened(self) -> None:
        def fn(payload: _SmallModel) -> None: ...

        typer_params, _, _ = iter_typer_params(fn)
        names = [p.name for p in typer_params]
        assert "payload_name" in names
        assert "payload_count" in names

    def test_large_model_uses_params_blob(self) -> None:
        def fn(payload: _BigModel) -> None: ...  # type: ignore[valid-type]

        typer_params, _, _ = iter_typer_params(fn)
        names = [p.name for p in typer_params]
        assert names == ["payload_params"]
        assert typer_params[0].default == "{}"

    def test_flatten_threshold_boundary(self) -> None:
        def fn(payload: _BoundaryModel) -> None: ...  # type: ignore[valid-type]

        typer_params, _, _ = iter_typer_params(fn)
        names = [p.name for p in typer_params]
        # Exactly threshold-many fields → still flattened.
        assert len(names) == PYDANTIC_FLATTEN_THRESHOLD
        assert all(n.startswith("payload_") for n in names)

    def test_flattened_required_field_marks_required(self) -> None:
        def fn(payload: _SmallModel) -> None: ...

        typer_params, _, _ = iter_typer_params(fn)
        # name field is required
        name_param = next(p for p in typer_params if p.name == "payload_name")
        assert name_param.default is ...

    def test_flattened_optional_field_uses_default(self) -> None:
        def fn(payload: _SmallModel) -> None: ...

        typer_params, _, _ = iter_typer_params(fn)
        # count has default 0
        count_param = next(p for p in typer_params if p.name == "payload_count")
        assert count_param.default == 0

    def test_flatten_descriptor_rebuilds_model(self) -> None:
        def fn(payload: _SmallModel) -> None: ...

        sig = inspect.signature(fn, eval_str=True)
        info = _PydanticFlatten.from_parameter(sig.parameters["payload"])
        assert info is not None
        assert info.flatten is True
        instance = info.build_from_typer_kwargs({"payload_name": "alice", "payload_count": 3})
        assert isinstance(instance, _SmallModel)
        assert instance.model_dump() == {"name": "alice", "count": 3}

    def test_params_blob_descriptor_parses_json(self) -> None:
        def fn(payload: _BigModel) -> None: ...  # type: ignore[valid-type]

        sig = inspect.signature(fn, eval_str=True)
        info = _PydanticFlatten.from_parameter(sig.parameters["payload"])
        assert info is not None
        assert info.flatten is False

        import json as _json

        instance = info.build_from_typer_kwargs(
            {
                "payload_params": _json.dumps(
                    {f"f{i}": str(i) for i in range(PYDANTIC_FLATTEN_THRESHOLD + 1)}
                )
            }
        )
        assert instance.f0 == "0"

    def test_params_blob_invalid_json_raises_bad_parameter(self) -> None:
        def fn(payload: _BigModel) -> None: ...  # type: ignore[valid-type]

        sig = inspect.signature(fn, eval_str=True)
        info = _PydanticFlatten.from_parameter(sig.parameters["payload"])
        assert info is not None

        with pytest.raises(typer.BadParameter):
            info.build_from_typer_kwargs({"payload_params": "{not valid json"})


class TestExistingTyperAnnotation:
    """If the user already supplied ``Annotated[T, typer.Option(...)]``, respect it."""

    def test_user_supplied_option_passes_through(self) -> None:
        def fn(
            hostname: Annotated[str, typer.Option("--host", "-h", help="Custom host")],
        ) -> None: ...

        typer_params, _, _ = iter_typer_params(fn)
        opt = _get_option(typer_params[0])
        # User-supplied legacy ``Option("--host", "-h")`` puts "--host" in
        # ``default`` and "-h" in ``param_decls``; Typer normalizes both
        # into param_decls when it parses the Annotated form at runtime.
        decls_or_default = (opt.default, *opt.param_decls)
        assert "--host" in decls_or_default
        assert "-h" in decls_or_default
