"""Verify Arcade HTTP contracts without live generation or purchases."""

import json
import unittest

import httpx

from app.config import Config
from app.services.arcade import Arcade


class ArcadeContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        """Make a real Arcade service whose HTTP transport is replaced in each test."""
        self.arcade = Arcade(Config("test", "test", "test", "test", "Core-test/contact"))
        await self.arcade.client.aclose()

    async def asyncTearDown(self):
        """Close the mock transport's client."""
        await self.arcade.close()

    def transport(self, handler):
        """Install a deterministic local HTTP response handler."""
        self.arcade.client = httpx.AsyncClient(base_url="https://example.com/v1/", transport=httpx.MockTransport(handler))

    async def test_actual_categories_only(self):
        """Select categories returned by Arcade and ignore unsupported worlds."""
        def respond(request):
            """Return a minimal real-shaped category list."""
            self.assertEqual(request.url.path, "/v1/categories/list")
            return httpx.Response(200, json={"items": [
                {"categoryId": "a", "name": "A", "world": "print"},
                {"categoryId": "b", "name": "B", "world": "jewelry"},
                {"categoryId": "c", "name": "C", "world": "unknown"},
            ]})
        self.transport(respond)
        self.assertEqual([item["categoryId"] for item in await self.arcade.categories()], ["a", "b"])

    async def test_combined_generation_body(self):
        """Both supported worlds use categoryId and avoid jewelry-incompatible fields."""
        def respond(request):
            """Inspect the documented generation request."""
            self.assertEqual(request.url.path, "/v1/patterns/create")
            self.assertEqual(set(json.loads(request.content)), {"requestId", "prompt", "categoryId"})
            return httpx.Response(200, json={"jobId": "job", "status": "running"})
        self.transport(respond)
        for category in ("print-id", "jewelry-id"):
            await self.arcade.generate({"requestId": category, "prompt": "A suitable design", "categoryId": category})

    async def test_full_catalog_is_cached(self):
        """Keep all categories, including same-group variants, and preload only once."""
        calls = []

        def respond(request):
            """Return more than three categories to catch the former truncation."""
            calls.append(request)
            return httpx.Response(200, json={"items": [
                {"categoryId": str(index), "name": f"Item {index}", "world": "print",
                 "category": {"categoryId": "shared", "name": "Shared group"}}
                for index in range(6)
            ]})

        self.transport(respond)
        self.assertEqual(len(await self.arcade.categories()), 6)
        self.assertEqual(len(await self.arcade.categories()), 6)
        self.assertEqual(len(calls), 1)
        self.arcade.catalog_loaded_at -= 3601
        await self.arcade.categories()
        self.assertEqual(len(calls), 2)

    async def test_checkout_quantity_one_requires_drop_ship(self):
        """Use Arcade's supported checkout schema, including the single-item prerequisite."""
        def respond(request):
            """Verify the exact checkout payload."""
            self.assertEqual(request.url.path, "/v1/checkouts/create")
            self.assertEqual(json.loads(request.content), {
                "requestId": "buyer-request", "dropShip": True,
                "lines": [{"productId": "product", "quantity": 1}],
            })
            return httpx.Response(200, json={"checkoutId": "1", "checkoutUrl": "https://example.com/pay"})
        self.transport(respond)
        await self.arcade.checkout("product", "buyer-request")

    async def test_unavailable_price_is_not_fabricated(self):
        """A null real price prevents publishing a made-up price."""
        def respond(request):
            """Return Arcade's documented temporary pricing state."""
            return httpx.Response(200, json={"unitPrice": None})
        self.transport(respond)
        with self.assertRaises(ValueError):
            await self.arcade.product("product")
