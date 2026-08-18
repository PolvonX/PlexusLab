import asyncio
from typing import Any, Awaitable, Callable, Dict, List

from ..logging_setup import get_logger

log = get_logger("dag_executor")


class DAGExecutor:
    """Исполнитель ориентированных ациклических графов (DAG) задач.
    
    Запускает задачи параллельно, дожидаясь выполнения зависимостей.
    Если задача падает, зависящие от неё ветки отменяются, но независимые
    продолжают выполняться.
    """

    def __init__(
        self,
        tasks: List[Dict[str, Any]],
        runner_func: Callable[[Dict[str, Any]], Awaitable[str]],
    ) -> None:
        self.tasks = tasks
        self.runner_func = runner_func
        self.results: Dict[Any, str] = {}
        self.errors: Dict[Any, str] = {}

    async def execute(self) -> Dict[str, Any]:
        task_map = {t["id"]: t for t in self.tasks}
        graph = {t["id"]: [] for t in self.tasks}
        in_degree = {t["id"]: len(t.get("depends_on", [])) for t in self.tasks}

        for t in self.tasks:
            for dep in t.get("depends_on", []):
                if dep in graph:
                    graph[dep].append(t["id"])
                else:
                    log.warning("Узел %s зависит от неизвестной задачи %s", t["id"], dep)

        while True:
            # Находим задачи, у которых in_degree == 0 и они еще не выполнены/не упали
            ready = [
                tid for tid, deg in in_degree.items()
                if deg == 0 and tid not in self.results and tid not in self.errors
            ]
            if not ready:
                break

            # Запускаем партию готовых задач
            async def run_task(tid: Any) -> None:
                try:
                    res = await self.runner_func(task_map[tid])
                    self.results[tid] = res
                except Exception as e:
                    self.errors[tid] = str(e)

            await asyncio.gather(*(run_task(tid) for tid in ready))

            # Обновляем зависимости
            for tid in ready:
                if tid in self.results:  # Успех
                    for dependent in graph[tid]:
                        in_degree[dependent] -= 1
                else:  # Провал
                    self._fail_tree(tid, graph)

        return {
            "results": self.results,
            "errors": self.errors,
        }

    def _fail_tree(self, failed_tid: Any, graph: Dict[Any, List[Any]]) -> None:
        """Рекурсивно помечаем зависимые задачи как проваленные."""
        for dependent in graph[failed_tid]:
            if dependent not in self.errors:
                self.errors[dependent] = f"Отменено: зависимость {failed_tid} завершилась с ошибкой."
                self._fail_tree(dependent, graph)
