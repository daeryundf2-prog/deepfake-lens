"""P0R-4 (user decision 2026-10-17): the gate-0 scope freeze is visible in the CLI help."""

from __future__ import annotations

import argparse
import contextlib
import io
import unittest

from deepfake_lens.cli_parser import GATE_COMMANDS, NON_GATE_MARK, NON_GATE_NOTE, VENDOR_WEIGHTS_GATE_NOTE, build_parser


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    return next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))


class GateScopeHelpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.parser, self.commands = build_parser()
        self.sub = _subparsers(self.parser)
        self.helps = {action.dest: action.help or "" for action in self.sub._choices_actions}

    def test_the_gate_commands_are_the_decided_ones(self) -> None:
        # DESK (2026-10-23): `desktop` joined the set — it is the double-click entry of the same local web app (web plus
        # the key file, port choice, browser, folder dialog and shutdown), so it is verified like `web`.
        self.assertEqual(GATE_COMMANDS, {
            "scan", "classify", "verify-report", "evidence-statement", "doctor", "web", "api-serve", "corpus", "vendor-weights",
            "desktop",
        })
        self.assertTrue(GATE_COMMANDS <= set(self.sub.choices))

    def test_every_other_command_is_marked_in_its_help_line_and_description(self) -> None:
        others = sorted(set(self.sub.choices) - GATE_COMMANDS)
        self.assertGreater(len(others), 20)
        for name in others:
            with self.subTest(command=name):
                self.assertTrue(self.helps[name].endswith(NON_GATE_MARK), self.helps[name])
                self.assertTrue((self.sub.choices[name].description or "").startswith(NON_GATE_NOTE))

    def test_gate_commands_are_not_marked(self) -> None:
        for name in GATE_COMMANDS:
            with self.subTest(command=name):
                self.assertNotIn(NON_GATE_MARK, self.helps[name])
                self.assertNotIn(NON_GATE_NOTE, self.sub.choices[name].description or "")

    def test_vendor_weights_names_its_gate_actions(self) -> None:
        self.assertIn(VENDOR_WEIGHTS_GATE_NOTE, self.sub.choices["vendor-weights"].description or "")

    def test_the_top_level_help_shows_the_mark(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            self.parser.parse_args(["--help"])
        self.assertIn("3D 생성 표식", out.getvalue())
        self.assertIn(NON_GATE_MARK, out.getvalue())


if __name__ == "__main__":
    unittest.main()
