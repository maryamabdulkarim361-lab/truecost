"""Offline regression coverage for TEST-006 timeouts and late Cost writes."""

import asyncio
import copy
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from google.api_core.exceptions import FailedPrecondition

from config.errors import A2AError, ErrorCode, TrueCostError
from config.settings import Settings
from services.cost_execution import PriceEnrichmentBudget, bounded_storage_call
from services.firestore_service import FirestoreService

pytestmark = pytest.mark.usefixtures("block_network")


class FakeDB:
    """Atomic batch/precondition fake, including a concurrent-commit hook."""

    def __init__(self):
        self.docs = {"estimates/e": {"status": "processing", "pipelineStatus": {}}}
        self.version = 0
        self.lock = threading.Lock()
        self.before_commit = None

    def collection(self, name):
        return Ref(self, name)

    def write_option(self, **kwargs):
        return kwargs

    def batch(self):
        return Batch(self)


class Ref:
    def __init__(self, db, path):
        self.db, self.path = db, path

    def collection(self, name):
        return Ref(self.db, f"{self.path}/{name}")

    document = collection

    def get(self, *, retry, timeout):
        assert retry is None and 0 < timeout <= 3
        with self.db.lock:
            data = copy.deepcopy(self.db.docs.get(self.path))
            return SimpleNamespace(exists=data is not None, update_time=self.db.version,
                                   to_dict=lambda: data)


def apply_fields(target, fields):
    for path, value in fields.items():
        parts = path.split(".")
        current = target
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = value


class Batch:
    def __init__(self, db):
        self.db, self.writes = db, []

    def update(self, ref, data, *, option):
        self.parent, self.updates, self.option = ref.path, data, option

    def set(self, ref, data, *, merge):
        self.writes.append((ref.path, data, merge))

    def commit(self, *, retry, timeout):
        assert retry is None and timeout == 3
        with self.db.lock:
            if self.db.before_commit:
                callback, self.db.before_commit = self.db.before_commit, None
                callback()
            if self.option["last_update_time"] != self.db.version:
                raise FailedPrecondition("concurrent write")
            apply_fields(self.db.docs[self.parent], self.updates)
            for path, data, merge in self.writes:
                if merge:
                    self.db.docs.setdefault(path, {}).update(data)
                else:
                    self.db.docs[path] = dict(data)
            self.db.version += 1


@pytest.fixture
def store():
    return FirestoreService(db=FakeDB())


@pytest.mark.parametrize("signal", ["USE_FIREBASE_EMULATORS", "FUNCTIONS_EMULATOR", "FIRESTORE_EMULATOR_HOST"])
def test_local_signals_never_route_to_production(monkeypatch, signal):
    from services import price_comparison_service as module
    for name in ("USE_FIREBASE_EMULATORS", "FUNCTIONS_EMULATOR", "FIRESTORE_EMULATOR_HOST"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(signal, "127.0.0.1:8081" if signal == "FIRESTORE_EMULATOR_HOST" else "true")
    monkeypatch.setattr(module, "settings", Settings(firebase_project_id="offline-project"))
    assert module._build_function_url("comparePrices") == (
        "http://127.0.0.1:5001/offline-project/us-central1/comparePrices"
    )


@pytest.mark.asyncio
async def test_unavailable_comparison_falls_back_without_polling(monkeypatch):
    from services import price_comparison_service as module
    call = AsyncMock(side_effect=httpx.ConnectError("offline unavailable"))
    poll = AsyncMock()
    monkeypatch.setattr(module, "_call_cloud_function", call)
    monkeypatch.setattr(module, "_poll_firestore_for_completion", poll)
    assert await module.get_material_prices(["cabinet"], "project") == {}
    call.assert_awaited_once()
    poll.assert_not_awaited()


@pytest.mark.asyncio
async def test_stalled_comparison_is_cancelled_and_late_results_are_isolated(monkeypatch):
    from services import price_comparison_service as module
    monkeypatch.setattr(module, "settings", SimpleNamespace(price_enrichment_budget_seconds=0.01))
    projects, cancelled = [], []

    async def call(function, data, **kwargs):
        projects.append(data["data"]["request"]["projectId"])
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    monkeypatch.setattr(module, "_call_cloud_function", call)
    for _ in range(2):
        assert await module.get_material_prices(["cabinet"], "project") == {}
    assert len(set(projects)) == 2
    assert len(cancelled) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [{}, {"item-0": 42}])
async def test_batch_misses_do_not_trigger_fifty_remote_calls(monkeypatch, result):
    from services import cost_data_service as module
    remote = AsyncMock(return_value=result)
    monkeypatch.setattr(module, "_price_comparison_service", remote)
    service = module.CostDataService()
    products = [f"item-{i}" for i in range(50)]
    await service.batch_prefetch_prices(products, "project")
    await service.batch_prefetch_prices(products, "project")
    for product in products:
        material = await service.get_material_cost("06-0000", product, "project")
        assert material["unit_cost"].low >= 0
    remote.assert_awaited_once()
    if result:
        assert service._price_cache["project:item-0"] == 42


@pytest.mark.asyncio
async def test_shared_budget_uses_one_clock_deadline():
    clock = [0.0]
    budget = PriceEnrichmentBudget(20, clock=lambda: clock[0])
    calls = []

    async def lookup():
        calls.append(True)
        clock[0] += 11
        return "price"

    assert await budget.run(lookup) == "price"
    assert budget.remaining == 9
    assert await budget.run(lookup) == "price"
    assert await budget.run(lookup) is None
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_enrichment_timeout_awaits_operation_cleanup():
    finished = []

    async def lookup():
        try:
            await asyncio.Event().wait()
        finally:
            finished.append(True)

    assert await PriceEnrichmentBudget(0.01).run(lookup) is None
    assert finished == [True]


def test_config_leaves_outer_margin():
    settings = Settings()
    assert settings.price_enrichment_budget_seconds < settings.cost_execution_budget_seconds <= 270
    assert settings.a2a_timeout_seconds == 300
    with pytest.raises(ValueError, match="30 seconds"):
        Settings(cost_execution_budget_seconds=300)


@pytest.mark.asyncio
async def test_cost_deadline_cancels_generation_before_llm_cleanup(monkeypatch):
    from agents.primary import cost_agent as module
    monkeypatch.setattr(module, "settings", SimpleNamespace(
        cost_execution_budget_seconds=0.02, price_enrichment_budget_seconds=0.01))
    agent = module.CostAgent(firestore_service=AsyncMock(), llm_service=MagicMock(),
                            cost_data_service=MagicMock(), serper_service=MagicMock())
    events = []

    async def generation(*args):
        try:
            await asyncio.Event().wait()
        finally:
            events.append("generation cancelled")

    agent._run_cost = generation
    with pytest.raises(TrueCostError, match="budget exhausted"):
        try:
            await agent.run("e", {})
        finally:
            events.append("A2A cleanup")
    assert events == ["generation cancelled", "A2A cleanup"]
    agent.firestore.save_agent_output.assert_not_awaited()


@pytest.mark.asyncio
async def test_active_success_saves_output_and_items_atomically(store):
    await store.begin_cost_attempt("e", "one", time.time() + 60)
    await store.update_agent_status("e", "cost", "running", attempt_id="one")
    await store.save_cost_items("e", [{"id": "item", "cost": 12}], attempt_id="one")
    await store.save_agent_output("e", "cost", {"total": 12}, attempt_id="one")
    assert store.db.docs["estimates/e"]["costOutput"] == {"total": 12}
    assert store.db.docs["estimates/e/agentOutputs/cost"]["output"] == {"total": 12}
    await store.end_cost_attempt("e", "one")
    await store.save_agent_output("e", "cost", {"total": 12}, score=95,
                                  attempt_id="one", allow_inactive=True)
    assert store.db.docs["estimates/e/agentOutputs/cost"]["score"] == 95


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", [False, True])
async def test_late_attempt_cannot_overwrite_newer_or_terminal_state(store, terminal):
    await store.begin_cost_attempt("e", "old", time.time() + 60)
    await store.end_cost_attempt("e", "old")
    if terminal:
        store.db.docs["estimates/e"]["status"] = "failed"
    else:
        await store.begin_cost_attempt("e", "new", time.time() + 60)
        await store.save_agent_output("e", "cost", {"new": True}, attempt_id="new")
    before = copy.deepcopy(store.db.docs)
    for write in (
        lambda: store.save_agent_output("e", "cost", {"old": True}, attempt_id="old"),
        lambda: store.update_agent_status("e", "cost", "completed", attempt_id="old"),
        lambda: store.save_cost_items("e", [{"id": "stale"}], attempt_id="old"),
    ):
        with pytest.raises(TrueCostError, match="Stale"):
            await write()
    assert store.db.docs == before


@pytest.mark.asyncio
async def test_concurrent_terminal_update_wins_atomic_commit_race(store):
    await store.begin_cost_attempt("e", "one", time.time() + 60)

    def fail_pipeline():
        store.db.docs["estimates/e"]["status"] = "failed"
        store.db.version += 1

    store.db.before_commit = fail_pipeline
    with pytest.raises(TrueCostError, match="Stale"):
        await store.save_agent_output("e", "cost", {"late": True}, attempt_id="one")
    assert "costOutput" not in store.db.docs["estimates/e"]
    assert "estimates/e/agentOutputs/cost" not in store.db.docs


@pytest.mark.asyncio
async def test_test006_caller_times_out_then_old_cost_finishes(store):
    from agents.orchestrator import PipelineOrchestrator
    from agents.primary.cost_agent import CostAgent
    finish = asyncio.Event()
    old_tasks = []

    async def send_task(**kwargs):
        message = kwargs["message"]
        agent = CostAgent(firestore_service=store, llm_service=MagicMock(),
                          cost_data_service=MagicMock(), serper_service=MagicMock())
        agent._attempt_id = message["attempt_id"]
        agent._attempt_expires_at = message["attempt_expires_at"]

        async def old_work(*args):
            await finish.wait()
            await store.save_agent_output("e", "cost", {"old": True}, attempt_id=agent._attempt_id)

        agent._run_cost = old_work
        old_tasks.append(asyncio.create_task(agent.run("e", {})))
        await asyncio.sleep(0)
        raise A2AError(ErrorCode.A2A_TIMEOUT, "simulated 300s timeout", "cost")

    client = MagicMock(send_task=AsyncMock(side_effect=send_task))
    orchestrator = PipelineOrchestrator(firestore_service=store, a2a_client=client)
    with pytest.raises(A2AError):
        await orchestrator._call_primary_agent("e", "cost", {})
    assert store.db.docs["estimates/e"]["costAttempt"]["active"] is False
    await store.begin_cost_attempt("e", "retry", time.time() + 60)
    await store.save_agent_output("e", "cost", {"new": True}, attempt_id="retry")
    finish.set()
    with pytest.raises(TrueCostError, match="Stale"):
        await old_tasks[0]
    assert store.db.docs["estimates/e"]["costOutput"] == {"new": True}
    client.send_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancelled_storage_call_joins_worker():
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def operation():
        started.set()
        release.wait(1)
        finished.set()

    task = asyncio.create_task(bounded_storage_call(operation))
    while not started.is_set():
        await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    task.cancel()  # A nested Cost deadline must not detach enrichment's SDK IO.
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


@pytest.mark.asyncio
async def test_expired_attempt_and_missing_identity_are_rejected(store):
    await store.begin_cost_attempt("e", "expired", time.time() - 1)
    for attempt_id in ("expired", None):
        with pytest.raises(TrueCostError, match="Stale"):
            await store.save_agent_output("e", "cost", {"late": True}, attempt_id=attempt_id)
    assert "costOutput" not in store.db.docs["estimates/e"]


@pytest.mark.asyncio
async def test_scope_material_estimate_survives_unavailable_pricing(monkeypatch):
    from agents.primary.cost_agent import CostAgent
    from services import cost_data_service as module
    remote = AsyncMock(return_value={})
    monkeypatch.setattr(module, "_price_comparison_service", remote)
    data = module.CostDataService()
    await data.batch_prefetch_prices(["Cabinet"], "project")
    agent = CostAgent(firestore_service=AsyncMock(), llm_service=MagicMock(),
                      cost_data_service=data, serper_service=MagicMock())
    result = await agent._get_material_cost_with_search(
        {"item": "Cabinet", "materialCostPerUnit": 175, "costCodeSource": "llm_estimate",
         "laborHoursPerUnit": 1.25}, "80202", "project"
    )
    assert result["unit_cost"].low == 175
    assert result["labor_hours_per_unit"] == 1.25
    remote.assert_awaited_once()
