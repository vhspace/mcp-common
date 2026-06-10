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

from mcpanvil.dual_mode._typer_params import (
    _JSON_PARAM_DEFAULT_SENTINEL,
    PYDANTIC_FLATTEN_THRESHOLD,
    _has_typer_argument_metadata,
    _JsonParam,
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

    def test_literal_int_uses_scalar_type_for_coercion(self) -> None:
        """``Literal[1, 2, 3]`` must surface as ``int`` so Click can coerce."""

        def fn(level: Literal[1, 2, 3]) -> None: ...

        typer_params, _, _ = iter_typer_params(fn)
        # Replaced by underlying scalar type so Click coerces ``--level 2``
        # to ``int`` before the membership callback validates it.
        assert _annotated_type(typer_params[0]) is int

    def test_literal_int_callback_rejects_non_member(self) -> None:
        """The synthesized callback must reject values outside the literal set."""
        import typer as _typer

        def fn(level: Literal[1, 2, 3]) -> None: ...

        typer_params, _, _ = iter_typer_params(fn)
        opt = _get_option(typer_params[0])
        assert opt.callback is not None
        # Membership accepted unchanged.
        assert opt.callback(2) == 2
        # Out-of-set value raises BadParameter.
        with pytest.raises(_typer.BadParameter):
            opt.callback(99)

    def test_literal_float_uses_scalar_type(self) -> None:
        def fn(rate: Literal[1.0, 2.0]) -> None: ...

        typer_params, _, _ = iter_typer_params(fn)
        assert _annotated_type(typer_params[0]) is float
        opt = _get_option(typer_params[0])
        assert opt.callback is not None

    def test_literal_str_keeps_native_handling(self) -> None:
        """All-string literals stay as ``Literal[...]`` so Typer renders the
        Click choice natively (no callback indirection)."""

        def fn(mode: Literal["a", "b"]) -> None: ...

        typer_params, _, _ = iter_typer_params(fn)
        assert _annotated_type(typer_params[0]) == Literal["a", "b"]
        opt = _get_option(typer_params[0])
        assert opt.callback is None


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


class TestPositionalArgument:
    """Issue #102: ``Annotated[T, typer.Argument(...)]`` → positional CLI arg.

    The function-signature → Typer-parameter mapping preserves the
    ``ArgumentInfo`` marker so Typer renders a positional argument; the MCP
    side is unaffected (covered end-to-end in ``test_dual_mode_builder.py``).
    """

    def test_argument_metadata_detected(self) -> None:
        def fn(hostname: Annotated[str, typer.Argument(help="h")]) -> None: ...

        annotation = next(iter(inspect.signature(fn, eval_str=True).parameters.values())).annotation
        assert _has_typer_argument_metadata(annotation) is True

    def test_option_and_bare_not_detected_as_argument(self) -> None:
        def fn(
            opt: Annotated[str, typer.Option("--opt")],
            bare: str,
        ) -> None: ...

        params = inspect.signature(fn, eval_str=True).parameters
        assert _has_typer_argument_metadata(params["opt"].annotation) is False
        assert _has_typer_argument_metadata(params["bare"].annotation) is False

    def test_argument_marker_preserved_in_typer_params(self) -> None:
        def fn(hostname: Annotated[str, typer.Argument(help="Device hostname.")]) -> None: ...

        typer_params, _, _ = iter_typer_params(fn)
        metadata = getattr(typer_params[0].annotation, "__metadata__", ())
        assert any(isinstance(m, typer.models.ArgumentInfo) for m in metadata)

    def test_required_positional_has_no_default(self) -> None:
        def fn(hostname: Annotated[str, typer.Argument()]) -> None: ...

        typer_params, _, _ = iter_typer_params(fn)
        assert typer_params[0].name == "hostname"
        assert typer_params[0].default is inspect.Parameter.empty

    def test_optional_positional_via_python_default(self) -> None:
        def fn(query: Annotated[str, typer.Argument()] = "") -> None: ...

        typer_params, _, _ = iter_typer_params(fn)
        assert typer_params[0].default == ""

    def test_positional_mixed_with_option(self) -> None:
        def fn(
            hostname: Annotated[str, typer.Argument()],
            include_interfaces: bool = False,
        ) -> None: ...

        typer_params, _, _ = iter_typer_params(fn)
        names = [p.name for p in typer_params]
        assert names == ["hostname", "include_interfaces"]
        # hostname keeps its Argument marker; include_interfaces becomes an Option.
        host_meta = getattr(typer_params[0].annotation, "__metadata__", ())
        assert any(isinstance(m, typer.models.ArgumentInfo) for m in host_meta)
        assert _has_typer_argument_metadata(typer_params[1].annotation) is False


class TestListLiteral:
    """#111: ``list[Literal[...]]`` maps to ``list[scalar]`` + a membership callback."""

    def test_str_literal_list_maps_to_list_str_with_callback(self) -> None:
        def fn(sections: list[Literal["a", "b", "c"]]) -> None: ...

        typer_params, _, _ = iter_typer_params(fn)
        ann = _annotated_type(typer_params[0])
        assert ann.__origin__ is list
        assert ann.__args__ == (str,)
        opt = _get_option(typer_params[0])
        assert opt.callback is not None
        # Every item validated against the choice set.
        assert opt.callback(["a", "b"]) == ["a", "b"]
        with pytest.raises(typer.BadParameter):
            opt.callback(["a", "zzz"])

    def test_int_literal_list_maps_to_list_int(self) -> None:
        def fn(levels: list[Literal[1, 2, 3]]) -> None: ...

        typer_params, _, _ = iter_typer_params(fn)
        ann = _annotated_type(typer_params[0])
        assert ann.__origin__ is list
        assert ann.__args__ == (int,)
        opt = _get_option(typer_params[0])
        assert opt.callback is not None
        assert opt.callback([1, 3]) == [1, 3]
        with pytest.raises(typer.BadParameter):
            opt.callback([9])

    def test_list_literal_default_is_empty(self) -> None:
        def fn(sections: list[Literal["a", "b"]]) -> None: ...

        typer_params, _, _ = iter_typer_params(fn)
        assert typer_params[0].default == []

    def test_mixed_type_literal_list_raises(self) -> None:
        def fn(x: list[Literal["a", 1]]) -> None: ...

        with pytest.raises(TypeError, match="mixed-type Literal"):
            iter_typer_params(fn)


class TestTopLevelDict:
    """#111: a top-level ``dict`` param maps to a single ``--<name>-json`` option."""

    def test_required_dict_maps_to_json_option(self) -> None:
        def fn(filters: dict) -> None: ...

        typer_params, _, _ = iter_typer_params(fn)
        assert [p.name for p in typer_params] == ["filters_json"]
        # Required (no default) → required option (Ellipsis sentinel default).
        assert typer_params[0].default is ...

    def test_parameterized_dict_maps_to_json_option(self) -> None:
        def fn(values: dict[str, int]) -> None: ...

        typer_params, _, _ = iter_typer_params(fn)
        assert [p.name for p in typer_params] == ["values_json"]

    def test_optional_dict_uses_sentinel_default(self) -> None:
        def fn(filters: dict | None = None) -> None: ...

        typer_params, _, _ = iter_typer_params(fn)
        assert typer_params[0].name == "filters_json"
        assert typer_params[0].default == _JSON_PARAM_DEFAULT_SENTINEL

    def test_json_param_round_trips_dict(self) -> None:
        def fn(filters: dict) -> None: ...

        info = _JsonParam.from_parameter(inspect.signature(fn, eval_str=True).parameters["filters"])
        assert info is not None
        present, value = info.build_from_typer_kwargs({"filters_json": '{"a": 1, "b": "x"}'})
        assert present is True
        assert value == {"a": 1, "b": "x"}

    def test_json_param_omitted_uses_default(self) -> None:
        def fn(filters: dict | None = None) -> None: ...

        info = _JsonParam.from_parameter(inspect.signature(fn, eval_str=True).parameters["filters"])
        assert info is not None
        present, _value = info.build_from_typer_kwargs({})
        assert present is False

    def test_json_param_invalid_json_raises(self) -> None:
        def fn(filters: dict) -> None: ...

        info = _JsonParam.from_parameter(inspect.signature(fn, eval_str=True).parameters["filters"])
        assert info is not None
        with pytest.raises(typer.BadParameter):
            info.build_from_typer_kwargs({"filters_json": "{not valid json"})

    def test_json_param_non_object_raises(self) -> None:
        def fn(filters: dict) -> None: ...

        info = _JsonParam.from_parameter(inspect.signature(fn, eval_str=True).parameters["filters"])
        assert info is not None
        with pytest.raises(typer.BadParameter):
            info.build_from_typer_kwargs({"filters_json": "[1, 2]"})

    def test_non_dict_param_not_detected(self) -> None:
        def fn(name: str) -> None: ...

        info = _JsonParam.from_parameter(inspect.signature(fn, eval_str=True).parameters["name"])
        assert info is None
