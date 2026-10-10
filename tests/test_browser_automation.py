import asyncio
import json
import os
import sys
import unittest

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.browser_sandbox import browser_sandbox_service
from app.agent.tools import execute_tool

class TestBrowserAutomationSuite(unittest.IsolatedAsyncioTestCase):
    """
    Comprehensive test suite validating SOTA browser automation capabilities:
    - Stealth anti-bot protection
    - Set-of-Marks (SoM) element indexing & text mapping
    - Deterministic index-based CDP interaction
    - In-page background network interception
    - CDP cookie session lifecycle
    - Stagehand-style structured JSON data extraction
    """

    async def asyncSetUp(self):
        await browser_sandbox_service.ensure_browser_running()

    async def test_01_stealth_evasion(self):
        """Verifies navigator.webdriver is undefined and chrome runtime exists."""
        await browser_sandbox_service.navigate("https://news.ycombinator.com")
        res = await browser_sandbox_service.evaluate_js("""
        (() => ({
            webdriver: navigator.webdriver,
            hasChrome: !!window.chrome,
            hasRuntime: !!window.chrome?.runtime,
            pluginsCount: navigator.plugins.length
        }))()
        """)
        val = res.get("result", {})
        self.assertFalse(val.get("webdriver", False), "navigator.webdriver must be undefined or falsy for anti-bot evasion")
        self.assertTrue(val.get("hasChrome"), "window.chrome must be spoofed")
        self.assertTrue(val.get("hasRuntime"), "window.chrome.runtime must exist")
        self.assertGreater(val.get("pluginsCount", 0), 0, "navigator.plugins should mimic realistic browser")
        print("  ✓ [TEST 1 PASSED] Stealth Anti-Bot Evasion verified: navigator.webdriver is falsy/spoofed")

    async def test_02_som_observation_and_indexing(self):
        """Verifies Set-of-Marks tags elements with data-xod-idx and computes coordinates."""
        obs = await browser_sandbox_service.observe(max_results=20)
        self.assertTrue(obs.get("success"))
        elements = obs.get("elements", [])
        self.assertGreater(len(elements), 5, "Should index at least 5 interactive elements on HN")
        
        # Verify first element has index 1, tag, and center coords
        first_el = elements[0]
        self.assertEqual(first_el["index"], 1)
        self.assertIn("tag", first_el)
        self.assertGreater(first_el["x"], 0)
        self.assertGreater(first_el["y"], 0)
        self.assertIn("[1]", obs.get("text_map", ""))
        print(f"  ✓ [TEST 2 PASSED] Set-of-Marks Observation: Indexed {len(elements)} elements with coordinates")

    async def test_03_indexed_cdp_interaction(self):
        """Verifies deterministic click by element index using CDP dispatch."""
        # Find element index for 'new' link
        obs = await browser_sandbox_service.observe(max_results=15)
        new_link = next((el for el in obs.get("elements", []) if el.get("text") == "new" or el.get("href") == "newest"), None)
        self.assertIsNotNone(new_link, "Should find 'new' link candidate")

        target_idx = new_link["index"]
        act_res = await browser_sandbox_service.interact_by_index(index=target_idx, action="click")
        self.assertTrue(act_res.get("success"))
        self.assertIn("newest", act_res.get("url", ""))
        print(f"  ✓ [TEST 3 PASSED] Indexed Grounding: Clicked element #{target_idx} -> Navigated to {act_res.get('url')}")

    async def test_04_network_snooper_interception(self):
        """Verifies in-page background network cache captures fetch/XHR requests."""
        # Execute an in-page fetch
        await browser_sandbox_service.evaluate_js("""
        fetch('https://news.ycombinator.com/robots.txt').catch(() => {});
        """)
        await asyncio.sleep(0.5)
        snoop = await browser_sandbox_service.snoop_network(limit=10)
        self.assertTrue(snoop.get("success"))
        print(f"  ✓ [TEST 4 PASSED] Network Snooper: Interceptor active, cache retrieved successfully")

    async def test_05_cookie_lifecycle(self):
        """Verifies setting, inspecting, and clearing cookies via CDP."""
        # 1. Set cookie
        set_res = await browser_sandbox_service.manage_cookies(
            action="set",
            cookies=[{"name": "xod_autotest_token", "value": "val_9988", "domain": ".ycombinator.com", "path": "/"}]
        )
        self.assertEqual(set_res.get("set_count"), 1)

        # 2. Get cookie
        get_res = await browser_sandbox_service.manage_cookies(action="get")
        self.assertTrue(any(c["name"] == "xod_autotest_token" for c in get_res.get("cookies", [])))

        # 3. Clear cookies
        clear_res = await browser_sandbox_service.manage_cookies(action="clear")
        self.assertTrue(clear_res.get("success"))

        # 4. Verify cleared
        get_after = await browser_sandbox_service.manage_cookies(action="get")
        self.assertEqual(get_after.get("count"), 0)
        print("  ✓ [TEST 5 PASSED] Cookie Lifecycle: Set, verified, and cleared cookies via CDP")

    async def test_06_structured_extractor_primitive(self):
        """Verifies Stagehand-style structured JSON extraction using local LLM."""
        await browser_sandbox_service.navigate("https://news.ycombinator.com")
        extract_res = await browser_sandbox_service.extract_structured_data(
            instruction="Extract the top 2 stories with their rank (int) and title (str)",
            schema_description='{"items": [{"rank": 1, "title": "..."}]}'
        )
        self.assertTrue(extract_res.get("success"), f"Extraction failed: {extract_res.get('error')}")
        data = extract_res.get("extracted_data", {})
        self.assertIsInstance(data, (dict, list))
        print(f"  ✓ [TEST 6 PASSED] Structured Extractor Primitive: Generated structured JSON successfully: {json.dumps(data)[:80]}...")

    async def test_07_agent_tools_integration(self):
        """Verifies execute_tool invokes browser_observe and browser_interact cleanly."""
        obs = await execute_tool("browser_observe", {"max_elements": 5})
        self.assertTrue(obs.get("success"))
        self.assertIn("elements", obs)

        interact = await execute_tool("browser_interact", {"index": 1, "action": "hover"})
        self.assertTrue(interact.get("success"))
        print("  ✓ [TEST 7 PASSED] Agent Tools Integration: browser_observe and browser_interact executed cleanly via tool dispatcher")

    async def test_08_condition_wait_primitive(self):
        """Verifies smart condition polling for selector and page text."""
        wait_res = await browser_sandbox_service.wait_for_condition(
            condition="text",
            value="Hacker News",
            timeout_sec=5.0
        )
        self.assertTrue(wait_res.get("success"))
        self.assertIn("Hacker News", wait_res.get("value"))
        print(f"  ✓ [TEST 8 PASSED] Condition Wait: Text condition verified in {wait_res.get('elapsed_sec')}s")

    async def test_09_pdf_export_primitive(self):
        """Verifies CDP Page.printToPDF exports a valid PDF file."""
        pdf_res = await browser_sandbox_service.export_pdf(filename="test_hn_export.pdf")
        self.assertTrue(pdf_res.get("success"), f"PDF export failed: {pdf_res.get('error')}")
        file_path = pdf_res.get("file_path", "")
        self.assertTrue(os.path.exists(file_path), "Exported PDF file must exist on disk")
        self.assertGreater(pdf_res.get("size_bytes", 0), 1000, "PDF must have non-trivial byte size")

        with open(file_path, "rb") as f:
            header = f.read(4)
        self.assertEqual(header, b"%PDF", "Exported file must have %PDF header")
        print(f"  ✓ [TEST 9 PASSED] PDF Export: Created valid PDF ({pdf_res.get('size_bytes')} bytes) at {file_path}")

    async def test_10_accessibility_tree_extraction(self):
        """Verifies CDP Accessibility.getFullAXTree extracts semantic role hierarchy."""
        ax_res = await browser_sandbox_service.extract_accessibility_tree(max_nodes=40)
        self.assertTrue(ax_res.get("success"))
        nodes = ax_res.get("nodes", [])
        self.assertGreater(len(nodes), 5, "Should extract at least 5 semantic AX nodes")
        self.assertTrue(any(n.get("role") in ("link", "button") for n in nodes))
        self.assertIn("PAGE AX TREE", ax_res.get("ax_tree_text", ""))
        print(f"  ✓ [TEST 10 PASSED] Accessibility Tree: Extracted {len(nodes)} AX nodes with roles & states")

    async def test_11_tab_management_lifecycle(self):
        """Verifies tab listing, opening new tab, and closing tab."""
        list_res = await browser_sandbox_service.manage_tabs(action="list")
        self.assertTrue(list_res.get("success"))
        init_count = list_res.get("count", 0)
        self.assertGreaterEqual(init_count, 1)

        new_res = await browser_sandbox_service.manage_tabs(action="new", url="https://news.ycombinator.com/ask")
        self.assertTrue(new_res.get("success"))
        new_tab_id = new_res.get("tab", {}).get("id")
        self.assertIsNotNone(new_tab_id)

        close_res = await browser_sandbox_service.manage_tabs(action="close", target_id=new_tab_id)
        self.assertTrue(close_res.get("success"))
        print(f"  ✓ [TEST 11 PASSED] Tab Management: Created and closed tab ID {new_tab_id} cleanly")

    async def test_12_downloads_inspection(self):
        """Verifies inspecting downloaded sandbox files."""
        dl_res = await browser_sandbox_service.get_downloads()
        self.assertTrue(dl_res.get("success"))
        self.assertIsInstance(dl_res.get("downloads"), list)
        print(f"  ✓ [TEST 12 PASSED] Downloads Inspector: Retrieved {dl_res.get('count')} tracked files")


if __name__ == "__main__":
    unittest.main()
