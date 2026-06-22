"""Sustained-load soak harness for the durable queue (Spec A0, T10, A0-R-3).

Drives the REAL sync claim/complete/archive path under concurrency and samples
both metric families (latency/throughput + the slow-death signals: dead-tuple
ratio, hot-table size, autovacuum activity). The lean substrate's keep:

- **Shape 1** (this default, env-scaled): in-task evidence the harness works and
  the hot table stays bounded under churn while archival + autovacuum keep dead
  tuples from growing unbounded.
- **Shape 2** (4–6h, ≥1M jobs): the rigorous **dead-tuple-slope ≈ 0** gate, run by
  the orchestrator against a DISPOSABLE Fly Postgres (same size class, NEVER prod
  ``open-persona-db``). Same harness, ``SOAK_DURATION_S`` scaled up.

Opt-in only: ``uv run pytest -m soak``. Duration via ``SOAK_DURATION_S`` (default
60s smoke), workers via ``SOAK_WORKERS``, rate via ``SOAK_RATE``.
"""

# ruff: noqa: ARG001 — ``migrated_engine`` is a schema-building fixture param.
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from persona_api.jobs import JobQueue
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

pytestmark = pytest.mark.soak

_DURATION = float(os.environ.get("SOAK_DURATION_S", "60"))
_WORKERS = int(os.environ.get("SOAK_WORKERS", "8"))
_RATE = float(os.environ.get("SOAK_RATE", "20"))  # enqueues/sec
_ARCHIVE_AFTER_S = 2.0  # archive completed jobs fast so the hot table stays small
_N_USERS = 5


def _p(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * pct))]


def _slope(series: list[tuple[float, float]]) -> float:
    """Least-squares slope of (t, y); 0 if degenerate."""
    n = len(series)
    if n < 2:
        return 0.0
    tx = sum(t for t, _ in series) / n
    ty = sum(y for _, y in series) / n
    num = sum((t - tx) * (y - ty) for t, y in series)
    den = sum((t - tx) ** 2 for t, _ in series)
    return num / den if den else 0.0


def test_sustained_load_hot_table_and_bloat_stay_bounded(migrated_engine: Engine) -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL not set; skipping soak")
    # migrated_engine built the schema (jobs + reloptions + indexes). Use a
    # larger pool for the worker fleet.
    engine = create_engine(database_url, pool_size=_WORKERS + 6, max_overflow=8)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email) SELECT 'u'||g, 'u'||g||'@x' "
                "FROM generate_series(1, :n) g"
            ),
            {"n": _N_USERS},
        )
    queue = JobQueue(engine)
    stop = threading.Event()
    latencies: list[float] = []
    enqueued = [0]
    # (elapsed, total_relation_size_bytes, dead_ratio). SIZE is the honest
    # slow-death signal: with archival keeping the live set tiny, dead_ratio is
    # high-but-meaningless (a few hundred dead tuples over ~tens of live rows), so
    # we gate on ABSOLUTE size staying bounded + flat, not the ratio.
    samples: list[tuple[float, int, float]] = []
    t0 = time.monotonic()

    def producer() -> None:
        i = 0
        interval = 1.0 / _RATE
        while not stop.is_set():
            i += 1
            queue.enqueue(
                type="t",
                owner_id=f"u{i % _N_USERS + 1}",
                payload={},
                idempotency_key=f"soak:{i}",
            )
            enqueued[0] = i
            time.sleep(interval)

    def worker(wid: str) -> None:
        while not stop.is_set():
            t = time.perf_counter()
            claimed = queue.claim(worker_id=wid, lease_seconds=30, limit=1, max_per_user=3)
            latencies.append((time.perf_counter() - t) * 1000)
            if not claimed:
                time.sleep(0.01)
                continue
            rec = claimed[0]
            queue.mark_running(job_id=rec.id, worker_id=wid)
            queue.complete(job_id=rec.id, worker_id=wid)  # instant "work"

    def maintenance() -> None:
        while not stop.is_set():
            now = datetime.now(UTC)
            queue.reclaim_expired(now=now)
            queue.archive_terminal(older_than=now - timedelta(seconds=_ARCHIVE_AFTER_S), limit=2000)
            time.sleep(1.0)

    def observer() -> None:
        while not stop.is_set():
            with engine.begin() as conn:
                row = conn.execute(
                    text(
                        "SELECT n_live_tup, n_dead_tup, pg_total_relation_size('jobs') AS sz "
                        "FROM pg_stat_user_tables WHERE relname='jobs'"
                    )
                ).first()
            live, dead, sz = (
                (row.n_live_tup or 0, row.n_dead_tup or 0, row.sz or 0) if row else (0, 0, 0)
            )
            ratio = dead / (live + dead) if (live + dead) else 0.0
            samples.append((time.monotonic() - t0, sz, ratio))
            time.sleep(2.0)

    threads = [
        threading.Thread(target=producer, daemon=True),
        threading.Thread(target=maintenance, daemon=True),
        threading.Thread(target=observer, daemon=True),
    ]
    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        for t in threads:
            t.start()
        workers = [pool.submit(worker, f"w{i}") for i in range(_WORKERS)]
        time.sleep(_DURATION)
        stop.set()
        for w in workers:
            w.result(timeout=30)
    for t in threads:
        t.join(timeout=5)

    # --- evidence + gates -------------------------------------------------------
    with engine.begin() as conn:
        hot = conn.execute(text("SELECT count(*) FROM jobs")).scalar_one()
        cold = conn.execute(text("SELECT count(*) FROM jobs_archive")).scalar_one()
        av = conn.execute(
            text("SELECT autovacuum_count FROM pg_stat_user_tables WHERE relname='jobs'")
        ).scalar_one()
    p95 = _p(latencies, 0.95)
    second_half = samples[len(samples) // 2 :]
    max_ratio = max((r for _, _, r in samples), default=0.0)
    max_size = max((s for _, s, _ in samples), default=0)
    # Slope of total_relation_size over the second half — the slow-death gate.
    size_slope = _slope([(t, float(s)) for t, s, _ in second_half])  # bytes/s
    engine.dispose()

    print(  # noqa: T201 — soak evidence
        f"\n[soak] duration={_DURATION}s workers={_WORKERS} enqueued={enqueued[0]} "
        f"claims={len(latencies)} p95_claim_ms={p95:.1f} hot_rows={hot} archived={cold} "
        f"autovacuum_count={av} max_size_kb={max_size / 1024:.0f} "
        f"size_slope_bytes_s={size_slope:+.0f} (dead_ratio_informational={max_ratio:.2f})"
    )

    # Shape-1 in-task gates (the rigorous slope≈0 over 4–6h is Shape 2 on Fly):
    assert len(latencies) > 0
    assert p95 < 500, f"claim p95 {p95:.1f}ms exceeds budget — contention/degradation"
    # Archival keeps the HOT table tiny — it must NOT grow toward total processed.
    assert hot < max(2000, _RATE * 60), f"hot table not bounded by archival: {hot} rows"
    assert cold > 0, "archival moved nothing — cleaner not running"
    # The slow-death gate: ABSOLUTE table+index size stays bounded (a tiny queue
    # table; without archival it would balloon) AND is not climbing meaningfully in
    # the second half (autovacuum + archival reach equilibrium). Shape 2 tightens
    # the slope toward ≈0 over 4–6h.
    assert max_size < 50 * 1024 * 1024, f"jobs table size unbounded ({max_size} bytes) — bloat"
    assert size_slope < 50_000, f"jobs table size climbing ({size_slope:+.0f} B/s) — slow-death"
