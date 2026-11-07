"""The process-composition tests: what the unit suite could not see.

The 2026-11-05 live tests caught three regressions the 864-test suite
let through — each a COMPOSITION failure, not a unit failure:

1. the enrollment push (a background loop with no importers read as
   "dead code" and was deleted — the dashboard's roster went empty);
2. the provider's dataset (the tests were REWRITTEN to the new shape
   while the runtime broke — the civs sync crashed on a missing file);
3. a boot crash (a class defined after the __main__ call that uses it
   — only the deploy health gate caught it).

These tests hold the invariants the unit suite structurally cannot.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

SRC = Path(__file__).resolve().parents[2] / "src" / "kingdoms"


def test_the_provider_boots_on_its_real_dataset() -> None:
    """The ext-aoe2techtree provider serves FROM its dataset files.

    The 'no vendored data' rule applies to the game and the core — the
    DATASET is the provider's own data. A deletion of the files (the
    2026-11-05 regression) must fail HERE, loudly.
    """
    from kingdoms.ext_aoe2techtree.source import DEFAULT_DATASET_DIR, DatasetContentSource

    assert (DEFAULT_DATASET_DIR / "data.json").exists(), (
        f"the provider's dataset is gone: {DEFAULT_DATASET_DIR}/data.json — "
        "the aoe2techtree provider cannot serve the civs"
    )
    source = DatasetContentSource(DEFAULT_DATASET_DIR)
    factions = source.faction_keys()
    assert len(factions) >= 50, "the dataset must carry the full civ catalog"


def test_the_core_pushes_the_enrollments() -> None:  # noqa: C901
    """The core's background loop pushes the bindings to the game service.

    The dashboard's roster depends on it: the loop has no importers (a
    background task never does) — deleting it as "dead code" emptied
    the roster. This test IS the loop's runtime coverage.
    """
    import asyncio

    import kingdoms.core_process.server as core_server

    pushed: list[dict[str, Any]] = []

    class _FakeCursor:
        def __init__(self, docs: list[dict[str, Any]]) -> None:
            self._docs = docs

        async def __aiter__(self) -> Any:
            for doc in self._docs:
                yield doc

    class _FakeDB:
        def __getitem__(self, name: str) -> Any:
            assert name == "profile_bindings"

            def find(query: dict[str, Any]) -> _FakeCursor:
                assert query == {"game_key": "aoe2"}
                return _FakeCursor(
                    [
                        {"profile_id": "111", "user_id": "u1"},
                        {"profile_id": "222", "user_id": "u2"},
                    ]
                )

            return type("Col", (), {"find": staticmethod(find)})()

    class _FakeClient:
        def __init__(self, uri: str) -> None:
            del uri

        async def update_enrollments(
            self, game_key: str, profile_ids: list[str], user_ids: list[str]
        ) -> bool:
            pushed.append(
                {"game_key": game_key, "profile_ids": profile_ids, "user_ids": user_ids}
            )
            return True

    async def _one_round() -> None:
        """Run the loop's body once (the interval sleep is patched out)."""
        import kingdoms.core.models.db as db_module

        original_db = db_module.get_async_database
        original_client = core_server._GameDataClient
        original_sleep = asyncio.sleep
        db_module.get_async_database = lambda: _FakeDB()  # type: ignore[assignment]
        core_server._GameDataClient = _FakeClient  # type: ignore[assignment]
        asyncio.sleep = _fake_sleep_one_round  # type: ignore[assignment]
        try:
            await core_server._sync_game_enrollments()
        except _RoundDoneError:
            pass
        finally:
            db_module.get_async_database = original_db  # type: ignore[assignment]
            core_server._GameDataClient = original_client  # type: ignore[assignment]
            asyncio.sleep = original_sleep  # type: ignore[assignment]

    class _RoundDoneError(Exception):
        """Stop the loop after its first interval sleep."""

    async def _fake_sleep_one_round(seconds: float) -> None:
        raise _RoundDoneError

    import os

    os.environ["GAME_AOE2_URI"] = "kingdoms-game-aoe2:50071"
    try:
        asyncio.run(_one_round())
    finally:
        os.environ.pop("GAME_AOE2_URI", "")
    assert pushed and pushed[0]["profile_ids"] == ["111", "222"], (
        "the enrollment push did not reach the game service — the "
        "dashboard's roster would go empty"
    )


def _later_definition_of(tree: ast.Module, name: str, guard_line: int) -> bool:
    """Whether NAME is defined in the module AFTER the __main__ guard."""
    for later in tree.body:
        if isinstance(later, (ast.ClassDef, ast.AsyncFunctionDef, ast.FunctionDef)):
            if later.name == name and later.lineno > guard_line:
                return True
        if isinstance(later, ast.Assign) and later.lineno > guard_line:
            if any(isinstance(t, ast.Name) and t.id == name for t in later.targets):
                return True
    return False


def test_every_process_boots_its_definitions_in_order() -> None:  # noqa: C901
    """A name used at boot must be defined BEFORE the __main__ call.

    The catalog gateway's class landed after the __main__ block that
    runs it — the process crashed at boot (only the deploy health gate
    caught it). This AST guard holds for every process entrypoint.
    """
    entrypoints = [
        "game_aoe2/server.py",
        "core_process/server.py",
        "ext_liquipedia/server.py",
        "ext_aoe2lobby/server.py",
        "ext_librematch/server.py",
        "ext_aoe2techtree/server.py",
        "discord/bot/main.py",
        "discord/bot/server.py",
    ]
    for rel in entrypoints:
        path = SRC / rel
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # the module-level definitions BEFORE the __main__ guard
        defined: set[str] = set()
        main_guard_line: int | None = None
        for node in tree.body:
            if isinstance(node, ast.If):
                main_guard_line = node.lineno
                break
            if isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef, ast.FunctionDef)):
                defined.add(node.name)
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        defined.add(target.id)
        assert main_guard_line is not None, f"{rel}: no __main__ guard found"
        # every name the module-level code references must be defined
        # above the guard — walk the nodes BEFORE it
        for node in tree.body:
            if isinstance(node, ast.If) and node.lineno == main_guard_line:
                break
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                    if sub.id in defined:
                        continue
                    # a built-in/imported name is fine: only flag names
                    # that ARE defined in the module but AFTER the guard
                    if _later_definition_of(tree, sub.id, main_guard_line):
                        raise AssertionError(
                            f"{rel}: {sub.id!r} is used before the __main__ guard "
                            f"but defined at a later line — the process crashes at boot"
                        )
