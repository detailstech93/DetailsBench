"""Automation framework (Phase 5): one Workload interface, an adapter for external
command-line tools described by small JSON files (data/tools/*.json), timeouts,
an emergency stop and diagnostic snapshots.

Design decision: elevated GUI benchmarks (Geekbench's window) are NOT driven. A
non-elevated process is expected to be unable to drive an elevated window
(UIPI, unverified on this machine). Only tools with a documented command line are
automated: Geekbench 7's free command line is (app/pro/geekbench.py; it uploads
every result, which the owner allowed on 2026-10-20).
"""
