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
from pathlib import Path
from typing import TYPE_CHECKING

from ..errors import AgentRunError, CortexError
from ..logging_setup import get_logger
from ..models import Action, AgentTask, ChatMessage, ToolResult
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


#: Подстроки, которыми claude.cmd реально сообщает "--resume не нашёл эту
#: сессию" (см. docs/superpowers/reviews/2026-08-13-cli-reliability-review.md
#: — рекомендация была найдена ревью, но не применена до живого вопроса
#: "что будет, если кончится квота claude?"). Никаких других причин отказа
#: сюда попадать не должно.
_SESSION_MISSING_MARKERS = ("no conversation found", "session not found", "invalid session id")
#: Сколько раз подряд можно пробовать один и тот же сгенерированный
#: (create_tool) инструмент, прежде чем сдаться и честно доложить CEO —
#: без этого предела self-healing loop мог бы крутиться до
#: brain_max_iterations, сжигая ходы на явно неисправимую с первого раза
#: ошибку.
_MAX_CUSTOM_TOOL_ATTEMPTS = 3


def _looks_like_missing_session(exc: AgentRunError) -> bool:
    """Отличает 'сессия не найдена' (стоит сбросить и попробовать заново) от
    любого другого сбоя claude.cmd — например, исчерпанной квоты. Раньше оба
    случая ловились одним and тем же except и трактовались как первое: при
    исчерпанной квоте это означало бессмысленный сброс живой, рабочей сессии
    (повтор упирается в ту же квоту и тоже падает) — CEO терял контекст
    разговора без всякой пользы взамен."""
    text = f"{exc} {exc.stderr}".lower()
    return any(marker in text for marker in _SESSION_MISSING_MARKERS)


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
        #: (chat_id, tool_name) -> детали каждой неудачной попытки подряд —
        #: self-healing loop для create_tool-инструментов (см. _process_action).
        self._custom_tool_failures: dict[tuple[int, str], list[str]] = defaultdict(list)

    # ------------------------------------------------------------------
    def _brain_workspace(self) -> Path:
        """Изолированная, пустая cwd для вызовов claude — НЕ корень проекта.

        --tools "" должен и без того запрещать claude трогать файлы, но cwd
        всё равно не должен указывать на дерево с .env и токенами: это
        defense-in-depth на случай, если ограничение инструментов когда-то
        не сработает (найдено вживую — CEO получил внутренние детали проекта,
        которых ни один мета-инструмент не отдаёт, см. коммит с этим фиксом).
        """
        path = self.deps.config.data_dir / "brain_workspace"
        path.mkdir(parents=True, exist_ok=True)
        return path

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
                prompt=prompt, iteration=1, original_text=text,
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
    async def _keep_typing(self, chat_id: int) -> None:
        """Пока claude думает, в чате видно «печатает…» — тот же приём, что
        Orchestrator._keep_typing использует для сотрудников."""
        interval = self.deps.config.typing_interval
        try:
            while True:
                try:
                    await self.deps.gateway.gateway_bot.send_chat_action(
                        chat_id=chat_id, action="typing"
                    )
                except Exception:  # noqa: BLE001 — индикатор набора не критичен
                    pass
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    async def _run_loop(
        self,
        *,
        chat_id: int,
        message_id: int | None,
        requester_id: int,
        prompt: str,
        iteration: int,
        resume_retry_done: bool = False,
        original_text: str | None = None,
        fallback_attempt: int = 0,
    ) -> None:
        """resume_retry_done защищает от бесконечного цикла: --resume может
        не найти сессию (в этой среде — регулярно, см. docs/superpowers/plans/
        2026-08-13-cortex-brain.md, Task 4 «Открытый вопрос»), и тогда мы один
        раз откатываемся на свежую сессию с тем же prompt, а не сдаёмся сразу.

        Откат разрешён ТОЛЬКО на iteration == 1: там prompt — это полный
        build_initial() с историей чата, так что свежая сессия всё равно
        получает нужный контекст. На iteration > 1 prompt — это короткий
        build_followup() (просто результат последнего инструмента); отправить
        его в новую сессию с нуля значит дать claude ответить вообще без
        понимания, что за задачу он вообще решал — а найдено ревью-агентом,
        не наблюдалось живьём. Поэтому там сбой --resume считается настоящим
        сбоем и идёт в чат как ошибка, а не глотается тихой пересборкой.

        И откат разрешён ТОЛЬКО если ошибка реально похожа на 'сессия не
        найдена' (_looks_like_missing_session) — иначе, например, исчерпанная
        квота claude тоже трактовалась бы как проблема сессии и сбрасывала бы
        рабочую сессию без всякой пользы (повтор всё равно упрётся в ту же
        квоту), CEO терял бы контекст разговора просто так."""
        deps = self.deps
        if iteration > deps.config.brain_max_iterations:
            await deps.gateway.reply(
                chat_id,
                "Кажется, я зациклился — остановлюсь здесь и подожду новых указаний.",
                reply_to=message_id,
            )
            return

        fallbacks = deps.config.brain_fallback_drivers
        if fallback_attempt > 0:
            driver = fallbacks[fallback_attempt - 1]
            session_flag = f"--session-id {uuid.uuid4()}"
        else:
            driver = deps.config.brain_driver
            session_flag = self.session.session_flag(chat_id)
        is_resume = session_flag.startswith("--resume")
        typing = asyncio.create_task(self._keep_typing(chat_id))
        result = None
        try:
            async with self._locks[chat_id]:
                result = await deps.runner.run(
                    prompt=prompt,
                    workspace=self._brain_workspace(),
                    agent="Cortex",
                    project=_BRAIN_PROJECT,
                    timeout=deps.config.runner_timeout,
                    system_prompt=self.prompts.persona(),
                    session_flag=session_flag,
                    driver=driver,
                )
        except AgentRunError as exc:
            if (
                is_resume and not resume_retry_done and iteration == 1
                and _looks_like_missing_session(exc)
            ):
                log.warning(
                    "Мозг: --resume не нашёл сессию чата %s, начинаю новую: %s", chat_id, exc
                )
                self.session.reset(chat_id)
                # Небольшая пауза перед повтором: живой инцидент показал, что
                # два запуска claude.cmd почти впритык друг за другом иногда
                # оба падают — похоже на коллизию npm-обёртки/её временных
                # файлов при повторном старте процесса без паузы.
                await asyncio.sleep(1.5)
            elif exc.retry_after is not None and fallback_attempt < len(fallbacks):
                next_driver = fallbacks[fallback_attempt]
                log.warning(
                    "Мозг: retryable-ошибка (%s), пробую fallback-драйвер %s чата %s",
                    exc, next_driver.name, chat_id,
                )
                typing.cancel()
                await self._run_loop(
                    chat_id=chat_id, message_id=message_id, requester_id=requester_id,
                    prompt=prompt, iteration=iteration, resume_retry_done=resume_retry_done,
                    original_text=original_text, fallback_attempt=fallback_attempt + 1,
                )
                return
            else:
                await deps.gateway.reply(
                    chat_id,
                    fmt.agent_error_report(
                        agent="Cortex", project=_BRAIN_PROJECT, error=exc,
                        stderr_limit=deps.config.stderr_report_chars,
                    ),
                    reply_to=message_id,
                )
                return
        finally:
            typing.cancel()

        if result is None:
            # Сюда попадаем только после сброса сессии на строке выше —
            # тот же prompt, но уже со свежим --session-id.
            await self._run_loop(
                chat_id=chat_id, message_id=message_id, requester_id=requester_id,
                prompt=prompt, iteration=iteration, resume_retry_done=True,
                original_text=original_text,
            )
            return

        if fallback_attempt == 0:
            # Fallback-ответы не персистятся как resume-сессия основной
            # модели — следующий обычный ход снова пробует основную модель
            # с её собственной (нетронутой) сохранённой сессией.
            self.session.mark_used(chat_id, session_flag)

        actions, parse_errors = extract_actions(result.stdout)
        reply_text = strip_actions(result.stdout)

        if reply_text:
            await deps.gateway.reply(chat_id, fmt.markdown_to_html(reply_text), reply_to=message_id)
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
            # Битый <action>-блок — сигнал деградации сессии (живой
            # инцидент: многочасовая сессия начала путать контрактный
            # формат с нативным tool-call синтаксисом claude). Следующий
            # ход начинается с чистой сессии, а не резюмирует эту же.
            self.session.reset(chat_id)

        if not actions:
            return

        if len(actions) > 1:
            log.debug("Мозг вернул %d действий за ход — беру первое, остальные отбрасываю", len(actions))

        await self._process_action(
            chat_id=chat_id, message_id=message_id, requester_id=requester_id,
            action=actions[0], iteration=iteration, original_text=original_text,
        )

    # ------------------------------------------------------------------
    async def _process_action(
        self, *, chat_id: int, message_id: int | None, requester_id: int, action: Action,
        iteration: int, original_text: str | None = None,
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

        tool = self.tools.get(action.tool)
        is_custom = bool(tool is not None and tool.is_custom)
        key = (chat_id, action.tool)
        if is_custom and result.ok:
            self._custom_tool_failures.pop(key, None)
        elif is_custom and not result.ok:
            await self._handle_custom_tool_failure(
                chat_id=chat_id, message_id=message_id, requester_id=requester_id,
                tool_name=action.tool, result=result, iteration=iteration,
                original_text=original_text,
            )
            return

        await self._continue_after_result(
            chat_id=chat_id, message_id=message_id, requester_id=requester_id,
            tool_name=action.tool, result=result, iteration=iteration,
            original_text=original_text,
        )

    # ------------------------------------------------------------------
    async def _handle_custom_tool_failure(
        self, *, chat_id: int, message_id: int | None, requester_id: int,
        tool_name: str, result: ToolResult, iteration: int, original_text: str | None,
    ) -> None:
        """Self-healing loop: сгенерированный (create_tool) инструмент упал.
        До лимита попыток — отдаём ошибку агенту с явным traceback и
        напоминанием про create_tool, тем же followup-циклом. На лимите —
        сдаёмся с честным, детерминированным отчётом CEO (не полагаемся на
        то, что модель сама решит подвести итог хорошо)."""
        key = (chat_id, tool_name)
        attempts = self._custom_tool_failures[key]
        attempts.append(result.detail or result.summary)

        if len(attempts) >= _MAX_CUSTOM_TOOL_ATTEMPTS:
            report = fmt.custom_tool_giveup_report(
                tool_name=tool_name, goal=original_text or "", attempts=list(attempts)
            )
            del self._custom_tool_failures[key]
            await self.deps.gateway.reply(chat_id, report, reply_to=message_id)
            return

        followup = self.prompts.build_custom_tool_failure_followup(
            tool_name=tool_name, result=result,
            attempt=len(attempts), max_attempts=_MAX_CUSTOM_TOOL_ATTEMPTS,
        )
        await self._run_loop(
            chat_id=chat_id, message_id=message_id, requester_id=requester_id,
            prompt=followup, iteration=iteration + 1, original_text=original_text,
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
    async def resolve_pending(self, action_id: str, *, chat_id: int, approved: bool) -> None:
        """Второй вход из asyncio.create_task() (brain_router.py, callback
        подтверждения) — тот же последний рубеж, что и у handle_message.

        chat_id приходит от самого callback (а не от pending), потому что
        pending может быть None (двойной клик, устаревшая кнопка после
        перезапуска) — а телеграм-сообщение к этому моменту уже переписано
        на "✅ Подтверждено", так что CEO нужно честно сказать, что на самом
        деле ничего не выполнилось."""
        pending = await self.pending.pop(action_id)
        if pending is None:
            await self.deps.gateway.reply(
                chat_id, "Это действие уже обработано или больше не актуально — повторно не выполняю."
            )
            return

        try:
            async with self._locks[pending.chat_id]:
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
                # Нет свободного текста CEO в этом потоке (кнопка, не
                # сообщение) — summary самого действия как разумная замена
                # "исходной задачи" для итогового отчёта self-healing loop.
                original_text=pending.summary,
            )
        except Exception as exc:  # noqa: BLE001 — последний рубеж, см. handle_message
            await self._report_unexpected_failure(pending.chat_id, pending.message_id, exc)

    # ------------------------------------------------------------------
    async def _continue_after_result(
        self, *, chat_id: int, message_id: int | None, requester_id: int,
        tool_name: str, result: ToolResult, iteration: int, original_text: str | None = None,
    ) -> None:
        followup = self.prompts.build_followup(tool_name=tool_name, result=result)
        await self._run_loop(
            chat_id=chat_id, message_id=message_id, requester_id=requester_id,
            prompt=followup, iteration=iteration + 1, original_text=original_text,
        )
