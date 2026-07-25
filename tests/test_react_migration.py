"""Smoke tests for Phase 1 React migration (HLD §8.1).

Tests:
- _serve_static returns dist file first, falls back to static/
- _serve_static returns None when neither has the file
"""

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent  # projects/switchboard/
REPO = ROOT.parent.parent  # workspace root
sys.path.insert(0, str(ROOT))  # so `import server` and `import control_plane` work
sys.path.insert(0, str(REPO))


class ServeStaticTest(unittest.TestCase):
    """Unit tests for _serve_static — does NOT require a running server."""

    def setUp(self):
        import server as srv_mod

        self.srv_mod = srv_mod
        self._orig_static = srv_mod.STATIC
        self._orig_legacy = srv_mod._STATIC_LEGACY

    def tearDown(self):
        self.srv_mod.STATIC = self._orig_static
        self.srv_mod._STATIC_LEGACY = self._orig_legacy

    def test_serve_static_falls_back_to_static_when_dist_missing(self):
        """_serve_static returns file from _STATIC_LEGACY when STATIC (dist) lacks it."""
        with tempfile.TemporaryDirectory() as tmp:
            dist_dir = Path(tmp) / "dist"
            static_dir = Path(tmp) / "static"
            dist_dir.mkdir()
            static_dir.mkdir()

            # Only static has terminal.html — simulates Phase 1 (board in dist, terminal still in static)
            (static_dir / "terminal.html").write_text("legacy terminal")
            self.srv_mod.STATIC = dist_dir
            self.srv_mod._STATIC_LEGACY = static_dir

            result = self.srv_mod._serve_static("terminal.html")
            self.assertIsNotNone(result)
            self.assertEqual(result.read_text(), "legacy terminal")

    def test_serve_static_dist_wins_when_both_exist(self):
        """dist/ file is preferred over static/ when both exist."""
        with tempfile.TemporaryDirectory() as tmp:
            dist_dir = Path(tmp) / "dist"
            static_dir = Path(tmp) / "static"
            dist_dir.mkdir()
            static_dir.mkdir()

            (dist_dir / "index.html").write_text("react board")
            (static_dir / "index.html").write_text("legacy board")
            self.srv_mod.STATIC = dist_dir
            self.srv_mod._STATIC_LEGACY = static_dir

            result = self.srv_mod._serve_static("index.html")
            self.assertIsNotNone(result)
            self.assertEqual(result.read_text(), "react board")

    def test_serve_static_returns_none_for_missing(self):
        """_serve_static returns None when neither directory has the file."""
        with tempfile.TemporaryDirectory() as tmp:
            dist_dir = Path(tmp) / "dist"
            static_dir = Path(tmp) / "static"
            dist_dir.mkdir()
            static_dir.mkdir()
            self.srv_mod.STATIC = dist_dir
            self.srv_mod._STATIC_LEGACY = static_dir

            self.assertIsNone(self.srv_mod._serve_static("nonexistent.html"))

    def test_vite_build_has_single_agent_entry(self):
        """Agent views share one Vite entry; chat.html is only a compat redirect."""
        vite_config = (ROOT / "vite.config.ts").read_text()
        self.assertIn("agent: resolve(__dirname, 'agent.html')", vite_config)
        self.assertNotIn("chat: resolve(__dirname, 'chat.html')", vite_config)

    def test_chat_input_lives_inside_center_area(self):
        """Chat input must resize with the center pane when drawers open."""
        agent = (ROOT / "src" / "pages" / "Agent.tsx").read_text()
        center_idx = agent.index('className="center-area"')
        chat_input_idx = agent.index('className="chat-bottom"')
        right_drawer_idx = agent.index("<RightDrawer")

        self.assertGreater(chat_input_idx, center_idx)
        self.assertLess(chat_input_idx, right_drawer_idx)

    def test_chat_is_read_only_reads_transcript_from_poll_not_ptysend(self):
        """ADR-0005: chat is a read-only transcript viewer — no send path, no
        ChatInput, replaced by ReadOnlyBar. The transcript poll is the sole
        message source."""
        agent = (ROOT / "src" / "pages" / "Agent.tsx").read_text()

        # No optimistic append or send path
        self.assertNotIn("const userMsg", agent)
        self.assertNotIn("setMessages((prev) => [...prev, userMsg])", agent)
        self.assertNotIn("sendMessage", agent)
        self.assertNotIn("ChatInput", agent)

        # ReadOnlyBar replaces ChatInput (read-only transcript viewer — ADR-0005)
        self.assertIn("ReadOnlyBar", agent)
        self.assertIn("onSwitchToTerminal", agent)

    def test_chat_refreshes_transcript_while_harness_is_working(self):
        """Chat must keep following the shared PTY transcript between interval polls."""
        agent = (ROOT / "src" / "pages" / "Agent.tsx").read_text()
        notifications = (
            ROOT / "src" / "hooks" / "useSessionNotifications.ts"
        ).read_text()

        self.assertIn("scheduleTranscriptRefresh", agent)
        self.assertIn("if (!sessionId || !typing) return;", agent)
        self.assertIn("setInterval(() => {", agent)
        self.assertIn("onEvent?: (event: NotificationEvent) => void", notifications)


if __name__ == "__main__":
    unittest.main()
