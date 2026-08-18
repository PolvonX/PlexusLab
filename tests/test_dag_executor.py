# tests/test_dag_executor.py
"""Тесты для DAGExecutor."""
from __future__ import annotations

import pytest

from cortex.runtime.dag_executor import DAGExecutor


async def _ok(task_data: dict) -> str:
    return f"done:{task_data['id']}"


async def _fail(task_data: dict) -> str:
    raise RuntimeError(f"failed:{task_data['id']}")


@pytest.mark.asyncio
async def test_empty_tasks():
    executor = DAGExecutor([], _ok)
    result = await executor.execute()
    assert result["results"] == {}
    assert result["errors"] == {}


@pytest.mark.asyncio
async def test_single_task_success():
    tasks = [{"id": 1, "task": "do A", "depends_on": []}]
    executor = DAGExecutor(tasks, _ok)
    result = await executor.execute()
    assert 1 in result["results"]
    assert result["results"][1] == "done:1"
    assert result["errors"] == {}


@pytest.mark.asyncio
async def test_single_task_failure():
    tasks = [{"id": 1, "task": "do A", "depends_on": []}]
    executor = DAGExecutor(tasks, _fail)
    result = await executor.execute()
    assert 1 in result["errors"]
    assert "failed:1" in result["errors"][1]
    assert result["results"] == {}


@pytest.mark.asyncio
async def test_chain_tasks_success():
    """A → B → C — последовательная цепочка."""
    tasks = [
        {"id": "A", "task": "A", "depends_on": []},
        {"id": "B", "task": "B", "depends_on": ["A"]},
        {"id": "C", "task": "C", "depends_on": ["B"]},
    ]
    executor = DAGExecutor(tasks, _ok)
    result = await executor.execute()
    assert set(result["results"]) == {"A", "B", "C"}
    assert result["errors"] == {}


@pytest.mark.asyncio
async def test_chain_failure_cancels_dependents():
    """A падает → B и C (зависят от A) помечаются как ошибка."""
    tasks = [
        {"id": "A", "task": "A", "depends_on": []},
        {"id": "B", "task": "B", "depends_on": ["A"]},
        {"id": "C", "task": "C", "depends_on": ["B"]},
    ]

    async def runner(task_data: dict) -> str:
        if task_data["id"] == "A":
            raise RuntimeError("A failed")
        return f"done:{task_data['id']}"

    executor = DAGExecutor(tasks, runner)
    result = await executor.execute()
    assert "A" in result["errors"]
    assert "B" in result["errors"]
    assert "C" in result["errors"]
    assert result["results"] == {}


@pytest.mark.asyncio
async def test_parallel_independent_tasks():
    """Две независимые задачи — обе выполняются."""
    tasks = [
        {"id": 1, "task": "X", "depends_on": []},
        {"id": 2, "task": "Y", "depends_on": []},
    ]
    executor = DAGExecutor(tasks, _ok)
    result = await executor.execute()
    assert set(result["results"]) == {1, 2}
    assert result["errors"] == {}


@pytest.mark.asyncio
async def test_diamond_dag():
    """
         A
        / \\
       B   C
        \\ /
         D
    """
    tasks = [
        {"id": "A", "task": "A", "depends_on": []},
        {"id": "B", "task": "B", "depends_on": ["A"]},
        {"id": "C", "task": "C", "depends_on": ["A"]},
        {"id": "D", "task": "D", "depends_on": ["B", "C"]},
    ]
    executor = DAGExecutor(tasks, _ok)
    result = await executor.execute()
    assert set(result["results"]) == {"A", "B", "C", "D"}
    assert result["errors"] == {}


@pytest.mark.asyncio
async def test_unknown_dependency_logs_warning(caplog):
    """Задача с зависимостью на несуществующую — не падает, просто предупреждение."""
    tasks = [
        {"id": 1, "task": "X", "depends_on": [99]},  # 99 не существует
    ]
    import logging
    with caplog.at_level(logging.WARNING, logger="cortex.dag_executor"):
        executor = DAGExecutor(tasks, _ok)
        await executor.execute()
    assert any("неизвестной задачи" in r.message for r in caplog.records)
