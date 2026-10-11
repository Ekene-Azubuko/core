"""Arcade physical-product API, following docs.arcade.ai/openapi.json."""

import asyncio
from decimal import Decimal
from time import monotonic

import httpx


class JobFailed(Exception):
    """An Arcade job reached a confirmed terminal failure."""


class Arcade:
    def __init__(self, config):
        """Configure a single authenticated HTTP client with a bounded timeout."""
        self.catalog = []
        self.catalog_loaded_at = 0
        self.catalog_lock = asyncio.Lock()
        self.client = httpx.AsyncClient(
            base_url=config.arcade_base_url.rstrip("/") + "/", timeout=45,
            headers={"Authorization": f"Bearer {config.arcade_key}",
                     "User-Agent": config.arcade_user_agent},
        )

    async def post(self, path, body):
        """Retry temporary failures with the identical idempotent request body."""
        for attempt in range(3):
            try:
                response = await self.client.post(path, json=body)
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < 2:
                        try:
                            delay = max(1, float(response.headers.get("Retry-After", 2 ** attempt)))
                        except ValueError:
                            delay = 2 ** attempt
                        if delay <= 60:
                            await asyncio.sleep(delay)
                            continue
                response.raise_for_status()
                return response.json()
            except httpx.TransportError:
                if attempt == 2:
                    raise
                await asyncio.sleep(2 ** attempt)

    async def categories(self):
        """Return the full supported catalog, refreshing the startup cache after one hour."""
        async with self.catalog_lock:
            if self.catalog and monotonic() - self.catalog_loaded_at < 3600:
                return self.catalog
            result = await self.post("categories/list", {})
            catalog = []
            for item in result["items"]:
                if item["world"] not in ("print", "jewelry"):
                    continue
                category = {key: item[key] for key in ("categoryId", "name", "world")}
                if item.get("category"):
                    category["category"] = item["category"]
                catalog.append(category)
            if not catalog:
                raise ValueError("Arcade returned no supported product categories")
            self.catalog = catalog
            self.catalog_loaded_at = monotonic()
            return self.catalog

    async def generate(self, request):
        """Start the documented combined pattern-and-product workflow for either world."""
        return await self.post("patterns/create", request)

    async def job(self, job_id):
        """Retrieve the real job state without inventing progress."""
        return await self.post("jobs/get", {"jobId": job_id})

    async def product(self, product_id):
        """Fetch fresh images and a real single-unit quote, rejecting unavailable prices."""
        result = await self.post("products/get", {"productId": product_id, "quantity": 1})
        price = result["unitPrice"]
        if price is None:
            raise ValueError("Arcade pricing is temporarily unavailable; retry retrieval")
        amount = Decimal(price["amount"])
        if not amount.is_finite() or amount < 0 or not price["currency"]:
            raise ValueError("Arcade returned an invalid price")
        return result

    async def checkout(self, product_id, request_id):
        """Create a separate quantity-one hosted checkout, including required drop shipping."""
        return await self.post("checkouts/create", {
            "requestId": request_id, "dropShip": True,
            "lines": [{"productId": product_id, "quantity": 1}],
        })

    async def close(self):
        """Close the HTTP connection pool."""
        await self.client.aclose()
