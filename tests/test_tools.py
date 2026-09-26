from pathlib import Path

import pytest

import app.tools as tools
from app.tools import ToolContext, ToolRegistry, resolve_safe_path

PARAMS = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}


async def _double(args, ctx):
    return str(args["x"] * 2)


async def _boom(args, ctx):
    raise RuntimeError("kaboom")


def make_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register("double", "Doubles x.", PARAMS, _double)
    return reg


# ---- schema ---------------------------------------------------------------

def test_flat_schema():
    assert make_registry().interactions_tools_schema("flat") == [
        {"type": "function", "name": "double", "description": "Doubles x.", "parameters": PARAMS}
    ]


def test_nested_schema():
    assert make_registry().interactions_tools_schema("nested") == [
        {"type": "function", "function": {"name": "double", "description": "Doubles x.", "parameters": PARAMS}}
    ]


def test_default_style_follows_module_constant(monkeypatch):
    monkeypatch.setattr(tools, "SCHEMA_STYLE", "nested")
    assert "function" in make_registry().interactions_tools_schema()[0]
    monkeypatch.setattr(tools, "SCHEMA_STYLE", "flat")
    assert make_registry().interactions_tools_schema()[0]["name"] == "double"


def test_unknown_style_raises():
    with pytest.raises(ValueError):
        make_registry().interactions_tools_schema("weird")


def test_schema_preserves_registration_order():
    reg = make_registry()
    reg.register("triple", "Triples x.", PARAMS, _double)
    assert reg.names() == ["double", "triple"]
    assert [t["name"] for t in reg.interactions_tools_schema("flat")] == ["double", "triple"]


# ---- registration ---------------------------------------------------------

def test_duplicate_registration_raises():
    reg = make_registry()
    with pytest.raises(ValueError):
        reg.register("double", "again", PARAMS, _double)


@pytest.mark.parametrize("bad_name", ["", "   ", None, 5])
def test_bad_name_raises(bad_name):
    with pytest.raises(ValueError):
        ToolRegistry().register(bad_name, "d", PARAMS, _double)


@pytest.mark.parametrize("bad_params", [None, [], {"type": "string"}, {}])
def test_non_object_parameters_raise(bad_params):
    with pytest.raises(ValueError):
        ToolRegistry().register("t", "d", bad_params, _double)


# ---- dispatch: never raises ----------------------------------------------

CTX = ToolContext(workspace_root=Path("."))


async def test_call_dispatches_dict_args():
    assert await make_registry().call("double", {"x": 4}, CTX) == "8"


async def test_call_accepts_json_string_args():
    assert await make_registry().call("double", '{"x": 5}', CTX) == "10"


async def test_call_empty_string_args_means_empty_dict():
    reg = ToolRegistry()

    async def echo(args, ctx):
        return f"got {args}"

    reg.register("echo", "e", {"type": "object", "properties": {}}, echo)
    assert await reg.call("echo", "  ", CTX) == "got {}"


async def test_call_malformed_json_is_error_string():
    out = await make_registry().call("double", "{not json", CTX)
    assert out.startswith("error: could not parse arguments as JSON")


async def test_call_non_object_args_is_error_string():
    out = await make_registry().call("double", [1, 2], CTX)
    assert out == "error: arguments must be a JSON object, got list"


async def test_call_unknown_tool_is_error_string():
    assert await make_registry().call("nope", {}, CTX) == "error: unknown tool 'nope'"


async def test_call_handler_exception_is_error_string():
    reg = ToolRegistry()
    reg.register("boom", "b", {"type": "object", "properties": {}}, _boom)
    out = await reg.call("boom", {}, CTX)
    assert out == "error: tool 'boom' failed: kaboom"


# ---- resolve_safe_path ----------------------------------------------------

def test_resolve_safe_path_accepts_inside(tmp_path):
    ctx = ToolContext(workspace_root=tmp_path)
    assert resolve_safe_path(ctx, "a/b.txt") == (tmp_path / "a" / "b.txt").resolve()
    assert resolve_safe_path(ctx, "a/../b.txt") == (tmp_path / "b.txt").resolve()


@pytest.mark.parametrize(
    "bad",
    ["../evil", "a/../../evil", "/etc/passwd", "//server/share", "C:/Windows", "C:\\Windows", "", "   ", "a\x00b", None, 5],
)
def test_resolve_safe_path_rejects(tmp_path, bad):
    assert resolve_safe_path(ToolContext(workspace_root=tmp_path), bad) is None
