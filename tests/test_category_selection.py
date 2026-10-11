"""Check that GPT recommendations stay grounded in the full Arcade catalog."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.services.lore import CategorySelection, select_categories


class CategorySelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_requested_jewelry_uses_real_category(self):
        """Pass the complete catalog and user request, preserving GPT's ranked IDs."""
        catalog = [{"categoryId": str(index), "name": f"Category {index}", "world": "print"}
                   for index in range(5)]
        catalog.append({"categoryId": "ring", "name": "Ring", "world": "jewelry"})
        client = SimpleNamespace(responses=SimpleNamespace(parse=AsyncMock(return_value=
            SimpleNamespace(output_parsed=CategorySelection(category_ids=["ring"])))))
        selected = await select_categories(client, "gpt-5.6-luna", {"lore": "Our shared motto"},
                                           "@CoreBot make us a ring", catalog)
        self.assertEqual(selected, [catalog[-1]])
        payload = json.loads(client.responses.parse.call_args.kwargs["input"][1]["content"])
        self.assertEqual(payload["available_categories"], catalog)
        self.assertEqual(payload["request_text"], "@CoreBot make us a ring")

    async def test_invented_and_duplicate_categories_are_rejected(self):
        """Invalid recommendations never become voting options."""
        catalog = [{"categoryId": "ring", "name": "Ring", "world": "jewelry"}]
        for ids in (["invented"], ["ring", "ring"]):
            client = SimpleNamespace(responses=SimpleNamespace(parse=AsyncMock(return_value=
                SimpleNamespace(output_parsed=CategorySelection(category_ids=ids)))))
            with self.assertRaises(ValueError):
                await select_categories(client, "gpt-5.6-luna", {}, "make something", catalog)
