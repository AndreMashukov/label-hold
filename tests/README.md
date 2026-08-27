# Day 0 placeholder. The pytest suite runs on Day 1 onward once the
# ADK graph and Firestore writes exist. Four test files planned:
#
#   test_allergens.py    # set difference is fail-closed
#   test_graph.py        # root is Sequential wrapping Parallel + Loop
#   test_poster.py       # write_lot_status mutates Firestore
#   test_idempotency.py  # replay HK-HOLD-MILK -> one row, same undeclared