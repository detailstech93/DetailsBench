"""Automation framework (Phase 5): one Workload interface, an adapter for external
command-line tools described by small JSON files (data/tools/*.json), timeouts,
an emergency stop and diagnostic snapshots.

Design decision: elevated GUI benchmarks (Geekbench) are NOT automated. A
non-elevated process is expected to be unable to drive an elevated window
(UIPI, unverified on this machine), and the Geekbench CLI needs a Pro license.
They stay assisted/manual; only tools with a documented command line are automated.
"""
