"""Confirmation text shown before an automated benchmark plan starts.

Drafted by the local agent, reviewed.
"""

from typing import Any, Dict, List, Optional

Item = Dict[str, Any]
Command = Optional[str]


def format_duration(seconds: int) -> str:
    """Format a non-negative number of seconds using the required style."""
    if seconds <= 0:
        return "0 s"

    if seconds < 60:
        return f"{seconds} s"

    if seconds < 3600:
        minutes = seconds // 60
        remaining_seconds = seconds % 60
        if remaining_seconds == 0:
            return f"{minutes} min"
        return f"{minutes} min {remaining_seconds} s"

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if minutes == 0:
        return f"{hours} h"
    return f"{hours} h {minutes} min"


def plan_overview(items: List[Item], cooldown_seconds: int) -> Dict[str, Any]:
    """Build the confirmation dialog data for a benchmark plan."""
    lines: List[str] = []
    skipped: List[str] = []
    total_runs = 0
    max_seconds = 0

    for item in items:
        command: Command = item.get("command")

        if command is None:
            skipped.append(str(item.get("display_name", "")))
            continue

        display_name = str(item.get("display_name", ""))
        warmup_runs = int(item.get("warmup_runs", 0))
        repetitions = int(item.get("repetitions", 0))
        timeout_seconds = int(item.get("timeout_seconds", 0))
        verified = bool(item.get("verified", False))
        is_test = bool(item.get("is_test", False))

        runs = warmup_runs + repetitions
        total_runs += runs
        max_seconds += runs * timeout_seconds

        timeout_text = format_duration(timeout_seconds)
        lines.append(
            f"{display_name}: {warmup_runs} warmup + {repetitions} measured runs, "
            f"timeout {timeout_text} each"
        )
        lines.append(f"  command: {command}")

        if not verified:
            lines.append("  WARNING: this tool spec is not verified on a real machine")

        if is_test:
            lines.append("  stored as test data (not a hardware result)")

    for display_name in skipped:
        lines.append(f"{display_name}: skipped (not found)")

    max_seconds += cooldown_seconds * max(total_runs - 1, 0)

    lines.append(f"Cooldown between runs: {format_duration(cooldown_seconds)}")
    lines.append(f"Total: {total_runs} runs, at most {format_duration(max_seconds)}")

    return {
        "total_runs": total_runs,
        "max_seconds": max_seconds,
        "skipped": skipped,
        "text": "\n".join(lines),
    }
