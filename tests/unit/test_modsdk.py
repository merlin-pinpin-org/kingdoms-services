"""Unit tests for the modder SDK runtime — the declarative pipeline.

These pin the whole contract the modder relies on: ids, storage,
cards, the Admin rule, the state gating, the confirm/prompt modals
and the handler pipeline — with an in-memory database and the mock
Discord client, no real Discord or Mongo anywhere.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from kingdoms.modsdk.models import (
    ActionResult,
    ActionSpec,
    EntityPanelSpec,
    EntitySpec,
    Field,
)
from kingdoms.modsdk.runtime import (
    AdminActionButton,
    AdminOpenButton,
    ModBinding,
    bind_mod,
    run_action,
)
from kingdoms.modsdk.store import EntityStore


class FakeDatabase:
    """In-memory stand-in for the async MongoDB adapter."""

    def __init__(self) -> None:
        self.collections: dict[str, dict[str, dict[str, Any]]] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        return _FakeCollection(self, name)


class _FakeCollection:
    def __init__(self, db: FakeDatabase, name: str) -> None:
        self._db = db
        self._name = name

    def _docs(self) -> dict[str, dict[str, Any]]:
        return self._db.collections.setdefault(self._name, {})

    async def replace_one(self, filt: dict[str, Any], doc: dict[str, Any], upsert: bool = False) -> None:
        del filt, upsert
        self._docs()[doc["_id"]] = doc

    async def find_one(self, filt: dict[str, Any]) -> dict[str, Any] | None:
        for doc in self._docs().values():
            if all(doc.get(k) == v for k, v in filt.items()):
                return doc
        return None

    def find(self, filt: dict[str, Any]) -> _FakeCursor:
        return _FakeCursor([d for d in self._docs().values() if all(d.get(k) == v for k, v in filt.items())])


class _FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def sort(self, key: str, direction: int = 1) -> _FakeCursor:
        del direction
        self._docs = sorted(self._docs, key=lambda d: d.get(key, 0))
        return self

    def __aiter__(self) -> _FakeCursor:
        self._iter = iter(self._docs)
        return self

    async def __anext__(self) -> dict[str, Any]:
        try:
            return next(self._iter)
        except StopIteration as err:
            raise StopAsyncIteration from err


TERRITORY = EntitySpec(
    key="territory",
    id_prefix="territory",
    label_key="modsdk.entity.territory",
    states=("draft", "published", "closed"),
    initial_state="draft",
    fields=(
        Field(
            key="map_type",
            label_key="modsdk.field.map_type",
            kind="select",
            choices=(("land", "land"), ("water", "water")),
        ),
        Field(key="nomad", label_key="modsdk.field.nomad", kind="bool"),
        Field(key="description", label_key="modsdk.field.description", kind="long_text"),
    ),
    title_field="name",
)

PANEL = EntityPanelSpec(
    entity="territory",
    channel="territories",
    admin_actions=(
        ActionSpec(key="publish", label_key="modsdk.action.publish", from_states=("draft",)),
        ActionSpec(key="close", label_key="modsdk.action.close", from_states=("published",)),
        ActionSpec(
            key="rename",
            label_key="modsdk.action.rename",
            prompt=(Field(key="name", label_key="modsdk.field.name", required=True),),
        ),
        ActionSpec(key="burn", label_key="modsdk.action.burn", confirm=True),
    ),
)


def _binding(db: FakeDatabase | None = None, **kwargs: Any) -> ModBinding:
    defaults: dict[str, Any] = {
        "database": db or FakeDatabase(),
        "entity_specs": (TERRITORY,),
        "panels": (PANEL,),
    }
    defaults.update(kwargs)
    return bind_mod("testmod", **defaults)


def _interaction() -> Any:
    from tests.mocks.discord_mock import MockClient, MockInteraction, MockUser

    user = MockUser(name="Designer")
    return MockInteraction(user=user, client=MockClient(), custom_id="modsdk:admin:territory:x")


class TestStoreIds:
    def test_entity_ids_carry_the_prefix_and_scope(self) -> None:
        store = EntityStore(FakeDatabase(), TERRITORY)
        assert store.entity_id("Buda Hills", "public") == "territory:public:buda_hills"
        assert store.entity_id("Buda Hills", "155") == "territory:155:buda_hills"

    @pytest.mark.asyncio
    async def test_create_then_get_round_trips(self) -> None:
        store = EntityStore(FakeDatabase(), TERRITORY)
        created = await store.create("public", "Buda Hills", {"map_type": "land"})
        fetched = await store.get(created["_id"])
        assert fetched is not None
        assert fetched["state"] == "draft"
        assert fetched["map_type"] == "land"

    @pytest.mark.asyncio
    async def test_list_scopes_and_filters_archived(self) -> None:
        store = EntityStore(FakeDatabase(), TERRITORY)
        first = await store.create("public", "A")
        await store.create("public", "B")
        assert len(await store.list("public")) == 2
        await store.archive(first["_id"])
        assert len(await store.list("public")) == 1
        assert len(await store.list("public", include_archived=True)) == 2

    @pytest.mark.asyncio
    async def test_update_moves_the_state(self) -> None:
        store = EntityStore(FakeDatabase(), TERRITORY)
        created = await store.create("public", "A")
        updated = await store.update(created["_id"], {"nomad": True}, new_state="published")
        assert updated is not None
        assert updated["state"] == "published"
        assert updated["nomad"] is True


class TestTemplates:
    def test_admin_button_custom_id_matches_its_template(self) -> None:
        button = AdminOpenButton("territory", "territory:public:a", binding=_binding())
        custom_id = str(button.item.custom_id)
        match = re.fullmatch(r"modsdk:admin:(?P<entity_kind>[a-z0-9_]+):(?P<entity_id>.+)", custom_id)
        assert match is not None
        assert match.group("entity_kind") == "territory"
        assert match.group("entity_id") == "territory:public:a"

    def test_action_button_custom_id_matches_its_template(self) -> None:
        button = AdminActionButton("territory", "territory:public:a", "publish", binding=_binding())
        custom_id = str(button.item.custom_id)
        match = re.fullmatch(
            r"modsdk:action:(?P<entity_kind>[a-z0-9_]+):(?P<action_key>[a-z0-9_]+):(?P<entity_id>.+)",
            custom_id,
        )
        assert match is not None
        assert match.group("action_key") == "publish"

    def test_custom_ids_stay_under_the_wire_limit(self) -> None:
        binding = _binding()
        long_id = "territory:public:" + "x" * 120
        admin = AdminOpenButton("territory", long_id, binding=binding)
        action = AdminActionButton("territory", long_id, "publish", binding=binding)
        assert len(str(admin.item.custom_id)) <= 100
        assert len(str(action.item.custom_id)) <= 100


class TestCards:
    def test_card_renders_props_one_per_line(self) -> None:
        binding = _binding()
        entity = {
            "_id": "territory:public:a",
            "kind": "territory",
            "name": "Buda Hills",
            "state": "draft",
            "map_type": "land",
            "nomad": True,
            "description": "A hilly land.",
        }
        view = binding.render_card(TERRITORY, entity, "")
        texts = _flat_texts(view)
        assert any("Buda Hills" in t for t in texts)
        assert any("**map_type** : land" in t for t in texts)
        assert any("**nomad** : yes" in t for t in texts)
        assert any("A hilly land." in t for t in texts)
        assert any("territory:public:a" in t for t in texts)

    def test_admin_ephemeral_greys_by_state(self) -> None:
        binding = _binding()
        draft = {"_id": "territory:public:a", "kind": "territory", "name": "A", "state": "draft"}
        view = binding.admin_ephemeral(TERRITORY, draft, "")
        buttons = _flat_buttons(view)
        states = {str(b.item.label): bool(b.item.disabled) for b in buttons}
        assert states.get("publish") is False
        assert states.get("close") is True
        published = dict(draft, state="published")
        view2 = binding.admin_ephemeral(TERRITORY, published, "")
        states2 = {str(b.item.label): bool(b.item.disabled) for b in _flat_buttons(view2)}
        assert states2.get("publish") is True
        assert states2.get("close") is False


class TestPipeline:
    @pytest.mark.asyncio
    async def test_run_action_persists_the_result_and_resyncs(self) -> None:
        db = FakeDatabase()
        synced: list[tuple[str, str]] = []

        async def sync(entity_key: str, entity_id: str, bot: Any) -> None:
            synced.append((entity_key, entity_id))

        async def publish(entity: dict[str, Any], actor: Any, values: dict[str, Any]) -> ActionResult:
            del entity, actor, values
            return ActionResult(new_state="published", message="Published!")

        panels = (
            EntityPanelSpec(
                entity="territory",
                channel="territories",
                admin_actions=(ActionSpec(key="publish", label_key="m.a.publish", handler=publish),),
            ),
        )
        binding = _binding(db, panels=panels, sync=sync)
        created = await binding.store("territory").create("public", "A")
        interaction = _interaction()
        await run_action(interaction, binding, "territory", created["_id"], "publish", created, {})
        stored = await binding.store("territory").get(created["_id"])
        assert stored is not None
        assert stored["state"] == "published"
        assert synced == [("territory", created["_id"])]
        assert interaction.response.message is not None
        assert "Published!" in str(interaction.response.message.content)

    @pytest.mark.asyncio
    async def test_run_action_merges_entity_changes(self) -> None:
        async def rename(entity: dict[str, Any], actor: Any, values: dict[str, Any]) -> ActionResult:
            del entity, actor
            return ActionResult(entity={"name": values["name"]})

        panels = (
            EntityPanelSpec(
                entity="territory",
                channel="territories",
                admin_actions=(ActionSpec(key="rename", label_key="m.a.rename", handler=rename),),
            ),
        )
        binding = _binding(panels=panels)
        created = await binding.store("territory").create("public", "A")
        interaction = _interaction()
        await run_action(interaction, binding, "territory", created["_id"], "rename", created, {"name": "New"})
        stored = await binding.store("territory").get(created["_id"])
        assert stored is not None
        assert stored["name"] == "New"


def _flat_texts(view: Any) -> list[str]:
    texts: list[str] = []

    def walk(item: Any) -> None:
        content = getattr(item, "content", None)
        if isinstance(content, str):
            texts.append(content)
        for child in getattr(item, "children", []) or []:
            walk(child)

    for top in getattr(view, "children", []) or []:
        walk(top)
    return texts


def _flat_buttons(view: Any) -> list[Any]:
    buttons: list[Any] = []

    def walk(item: Any) -> None:
        if isinstance(item, AdminActionButton) or isinstance(item, AdminOpenButton):
            buttons.append(item)
        for child in getattr(item, "children", []) or []:
            walk(child)

    for top in getattr(view, "children", []) or []:
        walk(top)
    return buttons
