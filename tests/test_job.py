"""
test_job.py — PyATS Job File
=============================
Orchestrates the two testscripts in sequence:
  1. pre_check  — infrastructure sanity
  2. post_check — OSPF/routes/BFD/ping validation

Usage:
  pyats run job tests/test_job.py --testbed-file tests/testbed.yml

The testbed passed via --testbed-file is automatically available in
runtime.testbed and forwarded to each testscript via the 'testbed' parameter.
"""

import os
from pyats.easypy import run

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))


def main(runtime):
    # ── 1. Pre-check : infrastructure sanity ──────────────────────────────────
    run(
        testscript=os.path.join(TESTS_DIR, "pre_check.py"),
        runtime=runtime,
        taskid="pre_check",
        testbed=runtime.testbed,
    )

    # ── 2. Post-check : full OSPF + routing + E2E validation ──────────────────
    run(
        testscript=os.path.join(TESTS_DIR, "post_check.py"),
        runtime=runtime,
        taskid="post_check",
        testbed=runtime.testbed,
    )
