"""Phase-0 QA scenarios: exactly four modules, one per QA area (W2, spec WP-J) —
test_qa_in.py (QA-IN-*), test_qa_out.py (QA-OUT-*), test_qa_adv.py (QA-ADV-*)
and test_qa_sys.py (QA-SYS-*, plus the traceability meta-tests).

Each automated QA ID has exactly one canonical test whose docstring first
line is "<QA ID>: <통과 기준 원문>", in the file traceability.json names for it
(checked by test_qa_sys.TraceabilityTest);
other tests whose docstring (or class docstring) starts with the QA ID
support it. traceability.json maps requirement -> gap -> QA ID and
scripts/qa_phase0.py turns a run into docs/CONFORMANCE.md.
Everything here runs without neural-network weights.
"""
