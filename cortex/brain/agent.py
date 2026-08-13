# cortex/brain/agent.py
"""Мозг Cortex: цикл «контекст -> claude -> действие -> результат -> …».

Один инструмент за ход (см. docs/superpowers/plans/2026-08-13-cortex-brain.md,
Task 15) — так подтверждение рискованного действия остаётся однозначным.
Разговор продолжается через claude --resume, поэтому каждый следующий ход —
не пересборка всего контекста, а только результат предыдущего действия.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from typing import TYPE_CHECKING

from ..errors import AgentRunError, CortexError
from ..logging_setup import get_logger
from ..models import Action, ChatMessage, ToolResult
from ..telegram import formatting as fmt
from ..tools.parser import extract_actions, strip_actions
from .context import BrainPromptBuilder
from .pending import PendingAction, PendingActionStore
from .risk import parse_autonomy, requires_confirmation, resolve_risk
from .session import BrainSession
from .tools.base import BrainToolContext, BrainToolRegistry

if TYPE_CHECKING:  # pragma: no cover
    from ..deps import Deps

log = get_logger("brain.agent")

_BRAIN_PROJECT = "__brain__"


def _describe(action: Action) -> str:
    args_preview = ", ".join(f"{k}={v!r}" for k, v in action.args.items())
    return f"{action.tool}({args_preview})"


class BrainAgent:
    def __init__(
        self,
        *,
        deps: "Deps",
        tools: BrainToolRegistry,
        prompts: BrainPromptBuilder,
        session: BrainSession,
        pending: PendingActionStore,
    ) -> None:
        self.deps = deps
        self.tools = tools
        self.prompts = prompts
        self.session = session
        self.pending = pending
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    # ------------------------------------------------------------------
    async def handle_message(
        self, *, chat_id: int, message_id: int | None, text: str, requester_id: int
    ) -> None:
        """Точка входа из brain_router.py — запускается через
        asyncio.create_task(), поэтому это последний рубеж: любое
        исключение отсюда либо уходит в чат, либо тонет молча в
        "Task exception was never retrieved" (как уже случалось с
        NotImplementedError из-за WindowsSelectorEventLoopPolicy)."""
        deps = self.deps
        try:
            history_block = deps.history.render(
                chat_id, limit=deps.config.history_messages, budget=deps.config.history_chars_budget
            )
            prompt = self.prompts.build_initial(
                chat_id=chat_id, history_block=history_block, message_text=text
            )
            await self._run_loop(
                chat_id=chat_id, message_id=message_id, requester_id=requester_id,
                prompt=prompt, iteration=1,
            )
        except Exception as exc:  # noqa: BLE001 — последний рубеж, см. docstring
            await self._report_unexpected_failure(chat_id, message_id, exc)

    # ------------------------------------------------------------------
    async def _report_unexpected_failure(
        self, chat_id: int, message_id: int | None, exc: Exception
    ) -> None:
        if isinstance(exc, CortexError):
            log.error("Мозг: %s", exc)
        else:
            log.exception("Мозг упал неожиданно")
        await self.deps.gateway.reply(
            chat_id, fmt.error_report(exc, context="мозг Cortex споткнулся"), reply_to=message_id
        )

    # ------------------------------------------------------------------
    async def _run_loop(
        self, *, chat_id: int, message_id: int | None, requester_id: int, prompt: str, iteration: int
    ) -> None:
        deps = self.deps
        if iteration > deps.config.brain_max_iterations:
            await deps.gateway.reply(
                chat_id,
                "Кажется, я зациклился — остановлюсь здесь и подожду новых указаний.",
                reply_to=message_id,
            )
            return

        session_flag = self.session.session_flag(chat_id)
        try:
            async with self._locks[chat_id]:
                result = await deps.runner.run(
                    prompt=prompt,
                    workspace=deps.config.root,
                    agent="Cortex",
                    project=_BRAIN_PROJECT,
                    timeout=deps.config.runner_timeout,
                    system_prompt=self.prompts.persona(),
                    session_flag=session_flag,
                    driver=deps.config.brain_driver,
                )
        except AgentRunError as exc:
            await deps.gateway.reply(
                chat_id,
                fmt.agent_error_report(
                    agent="Cortex", project=_BRAIN_PROJECT, error=exc,
                    stderr_limit=deps.config.stderr_report_chars,
                ),
                reply_to=message_id,
            )
            return

        self.session.mark_used(chat_id)

        actions, parse_errors = extract_actions(result.stdout)
        reply_text = strip_actions(result.stdout)

        if reply_text:
            await deps.gateway.reply(chat_id, fmt.esc(reply_text), reply_to=message_id)
            deps.history.add(
                ChatMessage(
                    chat_id=chat_id, message_id=message_id or 0, author="Cortex",
                    text=reply_text, is_agent=True,
                )
            )

        if parse_errors:
            await deps.gateway.reply(
                chat_id,
                "⚠️ Не разобрал часть действий:\n" + "\n".join(f"- {e}" for e in parse_errors),
            )

        if not actions:
            return

        if len(actions) > 1:
            log.debug("Мозг вернул %d действий за ход — беру первое, остальные отбрасываю", len(actions))

        await self._process_action(
            chat_id=chat_id, message_id=message_id, requester_id=requester_id,
            action=actions[0], iteration=iteration,
        )

    # ------------------------------------------------------------------
    async def _process_action(
        self, *, chat_id: int, message_id: int | None, requester_id: int, action: Action, iteration: int
    ) -> None:
        deps = self.deps
        default_tier = self.tools.risk_of(action.tool)

        if default_tier is not None:
            tier = resolve_risk(action.tool, default_tier, deps.config.brain_risk_overrides)
            autonomy = parse_autonomy(deps.config.brain_autonomy)
            if requires_confirmation(tier, autonomy):
                pending = PendingAction(
                    id=uuid.uuid4().hex[:10],
                    chat_id=chat_id,
                    message_id=message_id,
                    requester_id=requester_id,
                    tool=action.tool,
                    args=action.args,
                    risk=tier.value,
                    summary=_describe(action),
                )
                await self.pending.add(pending)
                await self._ask_confirmation(pending)
                return

        ctx = BrainToolContext(deps=deps, chat_id=chat_id, requester_id=requester_id)
        result = await self.tools.dispatch(action, ctx)
        await self._continue_after_result(
            chat_id=chat_id, message_id=message_id, requester_id=requester_id,
            tool_name=action.tool, result=result, iteration=iteration,
        )

    # ------------------------------------------------------------------
    async def _ask_confirmation(self, pending: PendingAction) -> None:
        await self.deps.gateway.ask_confirmation(
            chat_id=pending.chat_id,
            action_id=pending.id,
            summary=pending.summary,
            risk=pending.risk,
        )

    # ------------------------------------------------------------------
    async def resolve_pending(self, action_id: str, *, approved: bool) -> None:
        """Второй вход из asyncio.create_task() (brain_router.py, callback
        подтверждения) — тот же последний рубеж, что и у handle_message."""
        pending = await self.pending.pop(action_id)
        if pending is None:
            return

        try:
            if not approved:
                result = ToolResult.failure("Действие отменено CEO")
            else:
                ctx = BrainToolContext(
                    deps=self.deps, chat_id=pending.chat_id, requester_id=pending.requester_id
                )
                action = Action(tool=pending.tool, args=pending.args)
                result = await self.tools.dispatch(action, ctx)

            await self._continue_after_result(
                chat_id=pending.chat_id, message_id=pending.message_id,
                requester_id=pending.requester_id, tool_name=pending.tool, result=result, iteration=1,
            )
        except Exception as exc:  # noqa: BLE001 — последний рубеж, см. handle_message
            await self._report_unexpected_failure(pending.chat_id, pending.message_id, exc)

    # ------------------------------------------------------------------
    async def _continue_after_result(
        self, *, chat_id: int, message_id: int | None, requester_id: int,
        tool_name: str, result: ToolResult, iteration: int,
    ) -> None:
        followup = self.prompts.build_followup(tool_name=tool_name, result=result)
        await self._run_loop(
            chat_id=chat_id, message_id=message_id, requester_id=requester_id,
            prompt=followup, iteration=iteration + 1,
        )
