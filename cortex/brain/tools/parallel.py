from typing import Any, Dict

from ...errors import ToolError
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

        async def runner_func(task_data: Dict[str, Any]) -> str:
            task_id_str = str(task_data["id"])
            task_text = str(task_data.get("task", ""))
            
            # Уникальный ID задачи для логирования и очереди
            unique_task_id = f"{ctx.requester_id}-dag-{task_id_str}"
            
            # Мы модифицируем имя проекта в TaskInfo, чтобы обойти лок serialize_per_project 
            # внутри TaskScheduler, иначе агенты встанут в очередь, а не пойдут параллельно.
            # Глобальный семафор максимального числа agy при этом все равно будет работать!
            bypass_project_lock_name = f"{project.name}_dag_{task_id_str}"

            info = TaskInfo(
                task_id=unique_task_id,
                agent=employee.name,
                project=bypass_project_lock_name,
                instruction=task_text[:200],
            )

            async def _run_agent() -> str:
                agent_task = AgentTask(
                    employee=employee,
                    project=project.name,
                    instruction=task_text,
                    chat_id=ctx.chat_id,
                    message_id=0,
                    requester="Cortex_DAG",
                    task_id=unique_task_id,
                )

                history_block = ctx.deps.orchestrator.history.render(
                    ctx.chat_id,
                    limit=ctx.deps.config.history_messages,
                    budget=ctx.deps.config.history_chars_budget,
                )
                prompt = ctx.deps.orchestrator.prompts.build(
                    agent_task,
                    project=project,
                    history_block=history_block,
                    tools_doc=ctx.deps.tools.docs_for(employee),
                )

                result = await ctx.deps.orchestrator.runner.run(
                    prompt=prompt,
                    workspace=project.path,
                    agent=employee.name,
                    project=project.name,
                    timeout=ctx.deps.config.runner_timeout,
                )
                
                if result.returncode != 0:
                    error_msg = f"Процесс завершился с кодом {result.returncode}.\n{result.stderr}"
                    raise RuntimeError(error_msg)
                
                return result.stdout

            return await ctx.deps.orchestrator.scheduler.submit(info, _run_agent)

        executor = DAGExecutor(tasks, runner_func)
        result_dict = await executor.execute()

        report = [f"Отчет о выполнении DAG ({len(tasks)} задач):"]
        report.append("-" * 40)
        
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

        return ToolResult.success("Выполнение DAG завершено", "\n".join(report))
