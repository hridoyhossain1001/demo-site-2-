import argparse
import asyncio
import os
import statistics
import time
from collections import Counter
from typing import Any
from urllib.parse import urlparse

import httpx


DEFAULT_URL = os.getenv("TEST_URL", "http://localhost:8000/api/v1/events")
PRODUCTION_HOSTS = {"api.buykori.app", "www.api.buykori.app"}
URL = DEFAULT_URL
API_KEY = os.getenv("TEST_API_KEY", "")
DURATION_SECONDS = int(os.getenv("SOAK_DURATION_SECONDS", "300"))
TARGET_RPS = float(os.getenv("SOAK_TARGET_RPS", "30"))
CONCURRENCY = int(os.getenv("SOAK_CONCURRENCY", "40"))
HTTP2 = os.getenv("SOAK_HTTP2", "false").lower() in {"1", "true", "yes"}
INGEST_ONLY = os.getenv("SOAK_INGEST_ONLY", "true").lower() in {"1", "true", "yes"}
UNSAFE_PRODUCTION_OK = os.getenv("SOAK_UNSAFE_PRODUCTION_OK", "").lower() in {"1", "true", "yes"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Production-guarded Buykori events soak test.")
    parser.add_argument("--url", default=DEFAULT_URL, help="Events endpoint URL. Defaults to localhost.")
    parser.add_argument(
        "--unsafe-production-ok",
        action="store_true",
        default=UNSAFE_PRODUCTION_OK,
        help="Required when targeting api.buykori.app. Use only with a dedicated LoadTest client.",
    )
    return parser.parse_args()


def validate_target_url(url: str, unsafe_production_ok: bool) -> None:
    host = (urlparse(url).hostname or "").lower()
    if host in PRODUCTION_HOSTS and not unsafe_production_ok:
        raise SystemExit(
            "Production URL detected. Re-run with --unsafe-production-ok only after "
            "using a dedicated LoadTest client with downstream delivery disabled."
        )


def make_payload(i: int) -> dict[str, Any]:
    now = int(time.time())
    event_id = f"soak_{time.time_ns()}_{i}"
    return {
        "data": [
            {
                "event_name": "PageView",
                "event_time": now,
                "event_id": event_id,
                "event_source_url": "https://loadtest.buykori.local/soak",
                "action_source": "website",
                "user_data": {
                    "client_ip_address": f"203.0.113.{(i % 200) + 1}",
                    "client_user_agent": "BuyKori-SoakTest/1.0",
                },
                "custom_data": {
                    "content_name": "soak-test",
                    "content_category": "performance",
                },
            }
        ]
    }


async def worker(
    queue: asyncio.Queue[int | None],
    client: httpx.AsyncClient,
    latencies: list[float],
    statuses: Counter,
    errors: Counter,
) -> None:
    while True:
        item = await queue.get()
        try:
            if item is None:
                return

            started = time.perf_counter()
            try:
                response = await client.post(URL, json=make_payload(item), timeout=15.0)
                statuses[str(response.status_code)] += 1
            except Exception as exc:
                errors[type(exc).__name__] += 1
            finally:
                latencies.append(time.perf_counter() - started)
        finally:
            queue.task_done()


async def main(args: argparse.Namespace) -> None:
    global URL
    URL = args.url
    validate_target_url(URL, args.unsafe_production_ok)
    if not API_KEY:
        raise SystemExit("Set TEST_API_KEY to a safe LoadTest client key before running.")
    if TARGET_RPS <= 0:
        raise SystemExit("SOAK_TARGET_RPS must be greater than 0.")
    if DURATION_SECONDS <= 0:
        raise SystemExit("SOAK_DURATION_SECONDS must be greater than 0.")

    queue: asyncio.Queue[int | None] = asyncio.Queue(maxsize=CONCURRENCY * 4)
    latencies: list[float] = []
    statuses: Counter = Counter()
    errors: Counter = Counter()
    headers = {"X-API-Key": API_KEY, "Content-Type": "application/json"}
    if INGEST_ONLY:
        headers["X-BuyKori-Test-Mode"] = "ingest-only"
    limits = httpx.Limits(max_connections=CONCURRENCY, max_keepalive_connections=CONCURRENCY)

    print(
        f"Starting soak: url={URL}, duration={DURATION_SECONDS}s, "
        f"target_rps={TARGET_RPS:g}, concurrency={CONCURRENCY}, "
        f"http2={HTTP2}, ingest_only={INGEST_ONLY}"
    )

    async with httpx.AsyncClient(headers=headers, http2=HTTP2, limits=limits) as client:
        tasks = [
            asyncio.create_task(worker(queue, client, latencies, statuses, errors))
            for _ in range(CONCURRENCY)
        ]

        start = time.perf_counter()
        sent = 0
        interval = 1.0 / TARGET_RPS
        next_send = start

        while True:
            now = time.perf_counter()
            if now - start >= DURATION_SECONDS:
                break
            if now < next_send:
                await asyncio.sleep(next_send - now)
            await queue.put(sent)
            sent += 1
            next_send += interval

        for _ in tasks:
            await queue.put(None)
        await queue.join()
        await asyncio.gather(*tasks)

    elapsed = time.perf_counter() - start
    sorted_latencies = sorted(latencies)

    def percentile(p: float) -> float:
        if not sorted_latencies:
            return 0.0
        index = min(len(sorted_latencies) - 1, int(len(sorted_latencies) * p / 100))
        return sorted_latencies[index] * 1000

    print("Done")
    print(f"sent={sent} elapsed={elapsed:.2f}s actual_rps={sent / elapsed:.2f}")
    print(f"statuses={dict(statuses)} errors={dict(errors)}")
    if sorted_latencies:
        print(
            "latency_ms "
            f"avg={statistics.fmean(sorted_latencies) * 1000:.1f} "
            f"p50={percentile(50):.1f} "
            f"p95={percentile(95):.1f} "
            f"p99={percentile(99):.1f} "
            f"max={max(sorted_latencies) * 1000:.1f}"
        )


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
