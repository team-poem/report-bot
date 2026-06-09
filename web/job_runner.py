"""잡 전용 바운디드 실행기 — 동시 N개까지만 실행, 초과분은 FIFO 대기."""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable


class JobRunner:
    """ThreadPoolExecutor 를 감싸 동시 실행 수를 max_workers 로 제한한다.

    max_workers 를 초과해 submit 된 작업은 내부 큐에 FIFO 로 쌓였다가
    워커가 비면 실행된다. 별도 큐 구현 없이 "N개 동시 + 나머지 대기"가 보장된다.
    """

    def __init__(self, max_workers: int):
        if max_workers < 1:
            max_workers = 1
        self.max_workers = max_workers
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="job")

    def submit(self, fn: Callable[..., Any], *args: Any) -> Future:
        return self._executor.submit(fn, *args)

    def shutdown(self, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait)
