"""Everything the Automated runs section lists: tool specs (data/tools), AI adapters, storage adapters and simulation scripts (data/scripts)."""

from typing import List, Tuple


def load_workloads() -> Tuple[list, List[str]]:
    from app.ai.llama_benchy import LlamaBenchyWorkload
    from app.ai.mlperf_client import mlperf_workloads
    from app.ai.model_set import model_workloads
    from app.automation.cli_workload import load_workloads as load_cli
    from app.pro.blender_benchmark import blender_workloads
    from app.pro.cinebench import cinebench_workloads
    from app.pro.geekbench import geekbench_workloads
    from app.pro.handbrake import handbrake_workloads
    from app.pro.passmark import passmark_workloads
    from app.pro.vray_benchmark import vray_workloads
    from app.pro.ycruncher import ycruncher_workloads
    from app.simulation.script import load_scripts
    from app.storage.disk_tools import storage_workloads
    from app.stress.workload import stress_workloads
    from app.web.benchmarks import web_workloads
    workloads, errors = load_cli()
    scripts, script_errors = load_scripts()
    return (workloads + [LlamaBenchyWorkload(None)] + model_workloads() + mlperf_workloads() + storage_workloads() + stress_workloads() + blender_workloads() + vray_workloads()
            + cinebench_workloads() + passmark_workloads() + ycruncher_workloads()
            + handbrake_workloads() + web_workloads() + geekbench_workloads() + scripts,
            errors + script_errors)
