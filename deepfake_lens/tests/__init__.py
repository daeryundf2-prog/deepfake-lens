"""Unit tests (``python -m unittest discover deepfake_lens/tests``).

R15-4 (round 15): a test run started as a background job or under ``nohup``
inherits SIGINT / SIGHUP as ignored, and an ignored signal survives exec —
the tests that signal a child (SIGINT cleanup, SIGHUP/SIGTERM shutdown) then
waited on a child that slept through the signal (the R14-1 … R14-5 commits
failed their own test that way: docs/KNOWN-HISTORICAL-ISSUES.md). Importing
the test package makes every child of the test run start with SIGINT /
SIGTERM / SIGHUP at their default action.
"""

from deepfake_lens.shutdown import children_start_with_default_signals

children_start_with_default_signals()
