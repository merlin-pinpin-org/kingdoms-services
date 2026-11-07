"""The caller-to-remote seam contracts — every issued RPC is served (#322).

The 2026-11-06 lessons, generalized: a remote that takes kwargs behind
positional callers, a renamed RPC, a missing method — all invisible to
flow tests built on fake services (a fake accepts anything). These
contracts parse the CLIENTS' real source (never a hand-copied list) and
compare what they issue against what the SERVICERS implement, so drift
fails at commit time.
"""

from __future__ import annotations

import ast
import types
from pathlib import Path

import kingdoms.core.rpc.content_client as content_client
import kingdoms.game_aoe2.client as game_client
import kingdoms.game_aoe2.remote_catalog as remote_catalog
from kingdoms.core.rpc.content import ContentServicer
from kingdoms.core.services.faction_content import FactionContentService
from kingdoms.core.services.game_data import GameDataService
from kingdoms.game_aoe2.rpc import _CATALOG_OPS, GameDataServicer

# The gateway's composed surface (server._CatalogGateway): the catalog
# ops dispatch to GameDataService, the content ops to FactionContentService.
_CONTENT_OPS = {"content_store": "store", "content_get": "get"}


def _gateway_surface() -> set[str]:
    """The real method surface behind the GameData.Call gateway."""
    surface = {name for name in vars(GameDataService) if not name.startswith("_")}
    for op, method in _CONTENT_OPS.items():
        if hasattr(FactionContentService, method):
            surface.add(op)
    return surface

SEAMS: tuple[tuple[types.ModuleType, type], ...] = (
    (content_client, ContentServicer),
    (game_client, GameDataServicer),
    (remote_catalog, GameDataServicer),
)


def _stub_rpcs(module: types.ModuleType) -> dict[str, set[str]]:
    """Map stub class name -> RPC method names, parsed from the module source."""
    tree = ast.parse(Path(module.__file__).read_text())
    stub_vars: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            if isinstance(func, ast.Attribute) and func.attr.endswith("Stub"):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        stub_vars[target.id] = func.attr
    rpcs: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            value = node.func.value
            if isinstance(value, ast.Name) and value.id in stub_vars:
                rpcs.setdefault(stub_vars[value.id], set()).add(node.func.attr)
    return rpcs


def test_every_issued_rpc_is_served_by_the_real_servicer() -> None:
    """Each stub call in a client module has a matching servicer method."""
    for module, servicer in SEAMS:
        issued = _stub_rpcs(module)
        assert issued, f"{module.__name__} issues no RPC — the seam pair drifted"
        for stub_name, rpcs in issued.items():
            expected_servicer = stub_name[: -len("Stub")] + "Servicer"
            assert servicer.__name__ == expected_servicer, (
                f"{module.__name__} talks {stub_name} but is paired with "
                f"{servicer.__name__} — the seam pair is wired wrong"
            )
            missing = sorted(rpc for rpc in rpcs if not hasattr(servicer, rpc))
            assert not missing, (
                f"{module.__name__} issues {missing} on {stub_name} but "
                f"{servicer.__name__} does not implement them — the remote "
                "would crash in production while the flow tests stay green"
            )


def test_the_catalog_ops_allowlist_matches_the_real_service() -> None:
    """Every allowlisted GameData.Call op is a public GameDataService method."""
    real = _gateway_surface()
    drifted = sorted(op for op in _CATALOG_OPS if op not in real)
    assert not drifted, (
        "the GameData.Call allowlist drifted ahead of the real service: "
        f"{drifted} — the gateway would getattr them into AttributeError"
    )


def test_every_remote_catalog_op_is_allowlisted() -> None:
    """Every op RemoteGameDataService issues is allowlisted and real."""
    tree = ast.parse(Path(remote_catalog.__file__).read_text())
    issued: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_call"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            issued.add(node.args[0].value)
    assert issued, "RemoteGameDataService issues no op — the parser drifted"
    unknown = sorted(issued - set(_CATALOG_OPS))
    assert not unknown, (
        f"RemoteGameDataService issues ops the gateway refuses: {unknown} — "
        "every call would abort INVALID_ARGUMENT in production"
    )
    real = _gateway_surface()
    unreal = sorted(op for op in issued if op not in real)
    assert not unreal, f"ops issued with no real service method behind them: {unreal}"
