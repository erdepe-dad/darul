from __future__ import annotations

import argparse
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from graph_engine.cli import command_install_hooks
from graph_engine.config import Settings


class HookInstallationTests(unittest.TestCase):
    def test_post_merge_hook_is_idempotent_and_preserves_existing_hook(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            hook = root / ".git" / "hooks" / "post-merge"
            hook.write_text("#!/bin/sh\necho existing\n", encoding="utf-8")
            settings = Settings(root, "repo", "bolt://unused", "neo4j", "x", None, 1.0, frozenset())
            with patch("graph_engine.cli.SETTINGS", settings):
                command_install_hooks(argparse.Namespace())
                command_install_hooks(argparse.Namespace())
            content = hook.read_text(encoding="utf-8")

        self.assertEqual(content.count("# >>> graph-engine >>>"), 1)
        self.assertLess(content.index("graph_engine.cli sync"), content.index("echo existing"))
        self.assertIn("post-merge sync failed", content)


if __name__ == "__main__":
    unittest.main()
