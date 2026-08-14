# cortex/brain/tools/plan_tools.py
"""spawn_subtask: мини-PM движок для декомпозиции сложных задач.

Три инструмента поверх brain/plan.py — create_plan заводит roadmap и сразу
показывает его CEO (детерминированно, не полагаясь на то, что агент сам
решит рассказать), update_subtask отмечает шаг и умеет положить факт в
общий scratchpad (например, найденный номер/токен), get_plan_status
читает текущее состояние — вот так шаг 2 узнаёт то, что нашёл шаг 1."""

from __future__ import annotations

from ...errors import ToolError
from ...models import Action, ToolResult
from ...telegram import formatting as fmt
from ..plan import VALID_STATUSES, Plan, PlanStore, Subtask
from ..risk import RiskTier
from .base import BrainTool, BrainToolContext

_MAX_SUBTASKS = 20


class CreatePlanTool(BrainTool):
    name = "create_plan"
    description = (
        "Разбить сложную задачу на шаги (roadmap), когда её нельзя решить "
        "одним действием. Каждый шаг получает свой id и статус pending; "
        "дальше отмечай прогресс через update_subtask. Заводит новый план "
        "для этого чата — заменяет предыдущий, если он был. CEO сразу "
        "увидит краткий roadmap в чате."
    )
    usage = (
        '{"tool": "create_plan", "args": {"goal": "Зарегистрировать аккаунт", '
        '"tasks": [{"id": "1", "description": "Купить номер"}, '
        '{"id": "2", "description": "Получить код по смс"}, '
        '{"id": "3", "description": "Заполнить форму"}]}}'
    )
    risk = RiskTier.SAFE

    def __init__(self, *, store: PlanStore) -> None:
        self._store = store

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        goal = str(action.args.get("goal") or "").strip()
        if not goal:
            raise ToolError("нужна цель (args.goal)")

        raw_tasks = action.args.get("tasks")
        if not isinstance(raw_tasks, list) or not raw_tasks:
            raise ToolError("нужен непустой список шагов (args.tasks)")
        if len(raw_tasks) > _MAX_SUBTASKS:
            raise ToolError(f"слишком много шагов ({len(raw_tasks)}), максимум {_MAX_SUBTASKS}")

        subtasks: list[Subtask] = []
        seen_ids: set[str] = set()
        for index, raw in enumerate(raw_tasks, start=1):
            if not isinstance(raw, dict):
                raise ToolError(f"шаг #{index} должен быть объектом {{id, description}}")
            task_id = str(raw.get("id") or index).strip()
            description = str(raw.get("description") or "").strip()
            if not description:
                raise ToolError(f"у шага '{task_id}' нет description")
            if task_id in seen_ids:
                raise ToolError(f"повторяющийся id шага: '{task_id}'")
            seen_ids.add(task_id)
            subtasks.append(Subtask(id=task_id, description=description))

        plan = Plan(chat_id=ctx.chat_id, goal=goal, subtasks=subtasks)
        await self._store.set(plan)

        await ctx.deps.gateway.reply(
            ctx.chat_id,
            fmt.plan_roadmap_report(goal=goal, subtasks=[s.to_dict() for s in subtasks]),
        )

        return ToolResult.success(f"План создан: {len(subtasks)} шаг(ов)", plan.render_plain())


class UpdateSubtaskTool(BrainTool):
    name = "update_subtask"
    description = (
        "Отметить шаг плана (status: pending/in_progress/completed/failed) "
        "и, опционально, положить факт в общую память для следующих шагов "
        "через args.remember (например, найденный номер или токен)."
    )
    usage = (
        '{"tool": "update_subtask", "args": {"task_id": "1", "status": "completed", '
        '"result": "Номер куплен: +1234567890", "remember": {"phone": "+1234567890"}}}'
    )
    risk = RiskTier.SAFE

    def __init__(self, *, store: PlanStore) -> None:
        self._store = store

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        plan = self._store.get(ctx.chat_id)
        if plan is None:
            raise ToolError("плана ещё нет для этого чата — сначала create_plan")

        task_id = str(action.args.get("task_id") or "").strip()
        subtask = plan.get(task_id)
        if subtask is None:
            known = ", ".join(s.id for s in plan.subtasks) or "нет шагов"
            raise ToolError(f"шага '{task_id}' нет в плане. Доступные id: {known}")

        status = str(action.args.get("status") or "").strip()
        if status not in VALID_STATUSES:
            raise ToolError(f"недопустимый статус '{status}', используй: {', '.join(VALID_STATUSES)}")

        subtask.status = status
        subtask.result = str(action.args.get("result") or "").strip()

        remember = action.args.get("remember")
        if isinstance(remember, dict):
            for key, value in remember.items():
                plan.scratchpad[str(key)] = str(value)

        await self._store.set(plan)

        return ToolResult.success(f"Шаг '{task_id}' обновлён: {status}", plan.render_plain())


class GetPlanStatusTool(BrainTool):
    name = "get_plan_status"
    description = "Посмотреть текущий roadmap этого чата и общую память — что уже сделано, что дальше."
    usage = '{"tool": "get_plan_status", "args": {}}'
    risk = RiskTier.SAFE

    def __init__(self, *, store: PlanStore) -> None:
        self._store = store

    async def execute(self, action: Action, ctx: BrainToolContext) -> ToolResult:
        plan = self._store.get(ctx.chat_id)
        if plan is None:
            return ToolResult.failure("Плана ещё нет", "Вызови create_plan, чтобы начать.")
        return ToolResult.success("Текущий план", plan.render_plain())
