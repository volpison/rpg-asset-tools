"""
parallel.py
-----------
Run a CPU-bound function over many items in separate processes.

Why processes: the scan is pure Python.  In a thread it holds the GIL and the window
becomes sluggish; in other processes the window stays responsive and all cores are used.
Anything that goes wrong (cannot start, cannot pickle, a worker dies) falls back to doing
the work in this process, so the result is always the same, just slower.
"""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor, as_completed

MIN_ITEMS_FOR_POOL = 4
MIN_BYTES_FOR_POOL = 1_500_000      # starting processes costs ~0.3 s: not worth it for small projects


def cpu_workers() -> int:
    return max(1, min(os.cpu_count() or 1, 6))


def threaded_map(fn, items: list, workers: int = 12, on_done=None) -> dict:
    """{item: fn(item)} using threads - for waiting on the disk (opening files), where the GIL is
    released.  An item whose call raises gets None."""
    out: dict = {}
    if not items:
        return out
    from concurrent.futures import ThreadPoolExecutor
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(items)))) as ex:
        futs = {ex.submit(fn, it): it for it in items}
        for fut in as_completed(futs):
            try:
                out[futs[fut]] = fut.result()
            except Exception:                      # noqa: BLE001
                out[futs[fut]] = None
            done += 1
            if on_done:
                on_done(done)
    return out


def run_parallel(task, items: list, initializer=None, initargs=(), on_done=None,
                 workers: int | None = None, weights: list | None = None) -> list:
    """
    task(item) -> result, for each item; returns results in the same order.
    `initializer(*initargs)` runs once per process (and once here for the fallback).
    `on_done(n_finished)` is called as items finish.
    `weights` (e.g. file sizes) lets small jobs skip the pool.
    """
    n = len(items)
    workers = workers or cpu_workers()
    results: list = [None] * n
    done = 0

    def inline(indices):
        nonlocal done
        if initializer:
            initializer(*initargs)
        for i in indices:
            results[i] = task(items[i])
            done += 1
            if on_done:
                on_done(done)

    small = weights is not None and sum(weights) < MIN_BYTES_FOR_POOL
    if n < MIN_ITEMS_FOR_POOL or workers < 2 or small:
        inline(range(n))
        return results

    finished = set()
    try:
        with ProcessPoolExecutor(max_workers=workers, initializer=initializer,
                                 initargs=initargs) as pool:
            futures = {pool.submit(task, items[i]): i for i in range(n)}
            for fut in as_completed(futures):
                i = futures[fut]
                results[i] = fut.result()
                finished.add(i)
                done += 1
                if on_done:
                    on_done(done)
    except Exception:                      # noqa: BLE001 - any pool problem: do the rest here
        inline([i for i in range(n) if i not in finished])
    return results
