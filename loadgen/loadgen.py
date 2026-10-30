import os
import random
import time

import httpx

# ---------------------------------------------------------------------------
# Simple traffic generator: sends a steady stream of product lookups to
# api-service. Rate and target are configurable via environment variables.
# ---------------------------------------------------------------------------

TARGET_URL = os.getenv("TARGET_URL", "http://api-service:8000")
RPS = float(os.getenv("RPS", "10"))
SKUS = [f"sku-{i}" for i in range(1, 21)]


def main() -> None:
    interval = 1.0 / RPS if RPS > 0 else 0.1
    print(f"loadgen -> {TARGET_URL} at ~{RPS} req/s", flush=True)
    with httpx.Client(timeout=10.0) as client:
        while True:
            sku = random.choice(SKUS)
            start = time.perf_counter()
            try:
                resp = client.get(f"{TARGET_URL}/products/{sku}")
                elapsed_ms = (time.perf_counter() - start) * 1000
                print(
                    f"GET /products/{sku} -> {resp.status_code} {elapsed_ms:.0f}ms",
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001
                elapsed_ms = (time.perf_counter() - start) * 1000
                print(
                    f"GET /products/{sku} -> ERROR {exc} {elapsed_ms:.0f}ms",
                    flush=True,
                )
            time.sleep(interval)


if __name__ == "__main__":
    main()
