"""Everything the Automated runs section lists: tool specs (data/tools), AI adapters and storage adapters."""

from typing import List, Tuple


def load_workloads() -> Tuple[list, List[str]]:
    from app.ai.llama_benchy import LlamaBenchyWorkload
    from app.ai.mlperf_client import MlperfClientWorkload
    from app.automation.cli_workload import load_workloads as load_cli
    from app.storage.disk_tools import storage_workloads
    workloads, errors = load_cli()
    return workloads + [LlamaBenchyWorkload(None), MlperfClientWorkload()] + storage_workloads(), errors
