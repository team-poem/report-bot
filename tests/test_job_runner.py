import threading

from web.job_runner import JobRunner


def test_one_worker_runs_tasks_serially():
    runner = JobRunner(max_workers=1)
    try:
        release = threading.Event()
        task1_running = threading.Event()
        started2 = threading.Event()

        def task1():
            task1_running.set()
            release.wait(2)

        def task2():
            started2.set()

        f1 = runner.submit(task1)
        assert task1_running.wait(2)          # task1 이 실제로 실행 중
        runner.submit(task2)
        # 워커가 1개뿐이므로 task1 이 점유하는 동안 task2 는 시작되면 안 된다
        assert not started2.wait(0.3)
        release.set()                          # task1 해제 → task2 차례
        f1.result(2)
        assert started2.wait(2)
    finally:
        runner.shutdown(wait=True)


def test_two_workers_run_concurrently():
    runner = JobRunner(max_workers=2)
    try:
        # Barrier(2): 두 작업이 동시에 도달해야 통과, 직렬이면 timeout→BrokenBarrierError
        barrier = threading.Barrier(2, timeout=2)

        def task():
            barrier.wait()

        f1 = runner.submit(task)
        f2 = runner.submit(task)
        f1.result(3)
        f2.result(3)                           # 동시 실행이 아니면 여기서 예외가 재발생
    finally:
        runner.shutdown(wait=True)


def test_max_workers_below_one_is_clamped():
    runner = JobRunner(max_workers=0)
    try:
        assert runner.max_workers == 1
        assert runner.submit(lambda: 42).result(2) == 42
    finally:
        runner.shutdown(wait=True)
