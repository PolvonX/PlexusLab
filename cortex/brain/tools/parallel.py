from __future__ import annotations

from typing import Any

from ...errors import AgentRunError, ToolError
from ...models import Action, AgentTask, ToolResult
from ...runtime.dag_executor import DAGExecutor
from ...runtime.queue import TaskInfo
from ..risk import RiskTier
from .base import BrainTool, BrainToolContext


class SpawnParallelTasksTool(BrainTool):
    name = "spawn_parallel_tasks"
    description = (
        "Запустить независимые подзадачи параллельно (через ориентированный ациклический граф - DAG) "
        "для существенного ускорения работы (сбор данных из разных источников и т.п.)."
    )
    usage = (
        '{"tool": "spawn_parallel_tasks", "args": {"tasks": [{"id": 1, "task": "parse X", "depends_on": []}, '
        '{"id": 2, "task": "parse Y", "depends_on": [1]}], "employee": "Frontend_Dev", "project": "sports_api"}}'
    )
    risk = RiskTier.NORMAL

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        tasks = action.args.get("tasks", [])
        if not isinstance(tasks, list):
            raise ToolError("args.tasks должен быть списком (массивом JSON)")

        employee_name = str(action.args.get("employee") or "").strip()
        if not employee_name:
            raise ToolError("нужен employee (args.employee)")
        employee = ctx.deps.registry.require(employee_name)

        project_name = str(action.args.get("project") or "").strip()
        if not project_name:
            raise ToolError("нужен project (args.project)")
        project = ctx.deps.workspaces.require(project_name)

        async def runner_func(task_data: dict[str, Any]) -> str:
            task_id_str = str(task_data["id"])
            task_text = str(task_data.get("task", ""))
            
            unique_task_id = f"{ctx.requester_id}-dag-{task_id_str}"
            bypass_project_lock_name = f"{project.name}_dag_{task_id_str}"

            agent_task = AgentTask(
                employee=employee,
                project=project.name,
                instruction=task_text,
                chat_id=ctx.chat_id,
                message_id=0,
                requester="Cortex_DAG",
                task_id=unique_task_id,
            )

            try:
                # Используем dispatch с lock_project, чтобы задачи шли параллельно,
                # но при этом имели доступ к инструментам, fallback-ам и ретраям!
                return await ctx.deps.orchestrator.dispatch(
                    agent_task, 
                    requester_id=ctx.requester_id,
                    lock_project=bypass_project_lock_name
                )
            except Exception as exc:
                raise RuntimeError(str(exc)) from exc

        async def _background_dag():
            try:
                executor = DAGExecutor(tasks, runner_func)
                result_dict = await executor.execute()

                report = [f"Отчет о выполнении DAG ({len(tasks)} задач):", "-" * 40]
                
                for t in tasks:
                    tid = t["id"]
                    report.append(f"### ЗАДАЧА {tid} ###")
                    report.append(f"Инструкция: {t.get('task', '')}")
                    report.append(f"Зависит от: {t.get('depends_on', [])}")
                    
                    if tid in result_dict["results"]:
                        report.append(f"Статус: ✅ ВЫПОЛНЕНА\nРезультат:\n{result_dict['results'][tid]}")
                    elif tid in result_dict["errors"]:
                        report.append(f"Статус: ❌ ОШИБКА\nДетали:\n{result_dict['errors'][tid]}")
                    else:
                        report.append("Статус: ⚠️ ПРОПУЩЕНА ИЛИ НЕИЗВЕСТНО")
                    
                    report.append("-" * 40)

                notification = (
                    f"[Системное уведомление] Параллельные задачи (DAG) завершены.\n\n"
                    + "\n".join(report)
                )
                
                await ctx.deps.brain.handle_message(
                    chat_id=ctx.chat_id,
                    message_id=0,
                    text=notification,
                    requester_id=0
                )
            except Exception:
                import logging
                logging.getLogger("parallel").exception("Ошибка в фоновом выполнении DAG")

        import asyncio
        from .work import _BACKGROUND
        
        background = asyncio.create_task(_background_dag(), name=f"brain-dag-{ctx.chat_id}")
        _BACKGROUND.add(background)
        background.add_done_callback(_BACKGROUND.discard)

        return ToolResult.success(
            "DAG-задачи запущены в фоне", 
            "Я (Cortex) получу системное уведомление со сводным отчетом, когда все ветки завершатся."
        )
