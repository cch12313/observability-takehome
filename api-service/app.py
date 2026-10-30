import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException

# ---------------------------------------------------------------------------
# api-service
#
# The public-facing service. For each product lookup it calls inventory-service
# to get current stock, then returns a combined product view.
#
#     GET /products/{sku}  ->  inventory-service GET /stock/{sku}
#
# NOTE: this service intentionally ships WITHOUT metrics or tracing. Adding
# observability, so that a request can be followed across both services, is
# part of the assignment.
# ---------------------------------------------------------------------------

INVENTORY_URL = os.getenv("INVENTORY_URL", "http://inventory-service:8001")
UPSTREAM_TIMEOUT_S = float(os.getenv("UPSTREAM_TIMEOUT_S", "3.0"))

CATALOG = {
    f"sku-{i}": {"name": f"Product {i}", "price": round(4.99 + i, 2)}
    for i in range(1, 21)
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.client = httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT_S)
    yield
    await app.state.client.aclose()


app = FastAPI(title="api-service", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "api-service"}


@app.get("/products/{sku}")
async def get_product(sku: str):
    client: httpx.AsyncClient = app.state.client
    try:
        resp = await client.get(f"{INVENTORY_URL}/stock/{sku}")
        resp.raise_for_status()
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="inventory-service timeout")
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"inventory-service error: {exc}")

    stock = resp.json()
    meta = CATALOG.get(sku, {"name": f"Unknown {sku}", "price": 9.99})
    return {
        "sku": sku,
        "name": meta["name"],
        "price": meta["price"],
        "quantity": stock["quantity"],
        "in_stock": stock["quantity"] > 0,
    }
