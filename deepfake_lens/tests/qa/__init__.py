"""Phase-0 QA scenarios: exactly four modules, one per QA area (W2, spec WP-J) —
test_qa_in.py (QA-IN-*), test_qa_out.py (QA-OUT-*), test_qa_adv.py (QA-ADV-*)
and test_qa_sys.py (QA-SYS-*, plus the traceability meta-tests).

N16: every test's docstring first line is "<QA ID>: <통과 기준 원문>" (the
criterion verbatim from the spec, as traceability.json records it) and its
optional second line says what that particular test checks; the tests of a
QA ID live in the file traceability.json names for it (checked by
test_qa_sys.TraceabilityTest). traceability.json maps requirement -> gap -> QA ID and
scripts/qa_phase0.py turns a run into docs/CONFORMANCE.md.
Everything here runs without neural-network weights.
"""
