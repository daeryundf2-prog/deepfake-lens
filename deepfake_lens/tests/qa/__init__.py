"""Phase-0 QA scenarios, one module per QA group (test_qa_<group>*.py).

Each automated QA ID has exactly one canonical test whose docstring first
line is "<QA ID>: <통과 기준 원문>" (checked by test_qa_traceability.py);
other tests whose docstring (or class docstring) starts with the QA ID
support it. traceability.json maps requirement -> gap -> QA ID and
scripts/qa_phase0.py turns a run into docs/CONFORMANCE.md.
Everything here runs without neural-network weights.
"""
