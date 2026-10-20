"""AI reports chart the graphics card memory and the PC's memory over the whole test (owner, 2026-10-20)."""

from app import report


def _export(name):
    series = {"tel_gpu0_vram_used": {"unit": "GiB", "points": [[0, 1.0], [10, 22.5], [20, 23.8]]},
              "tel_ram_used_gib": {"unit": "GiB", "points": [[0, 12.0], [10, 30.5], [20, 31.0]]},
              "tel_gpu0_load": {"unit": "%", "points": [[0, 5.0], [10, 13.0], [20, 12.0]]}}
    run = {"benchmark_name": name, "status": "completed", "is_warmup": 0, "is_test": 0, "score": 12.4,
           "unit": "tok/s", "note": "Model: gemma", "series": series}
    return {"session": {"is_test": 0}, "runs": [run],
            "summaries": {name: {"stats": {"mean": 12.4}}}, "metric_summaries": {}}


def test_mlperf_and_free_llama_have_the_memory_charts():
    for html in (report._mlperf_section(_export("mlperf_client")), report._free_llama_section(_export("llama_benchy"))):
        assert "Graphics card memory (VRAM) used&#58; GPU" in html
        assert "Memory (RAM) used&#58; PC" in html
        assert "Load&#58; GPU" in html


def test_other_tools_keep_their_charts():
    html = report._sevenzip_section(_export("sevenzip_benchmark"))
    assert "Load&#58; GPU" in html and "<title>Graphics card memory" not in html


def test_memory_charts_show_the_use_out_of_all_of_it():
    """Owner, 2026-10-20: not only the highest use: how much of the whole memory was used."""
    export = _export("llama_benchy")
    series = export["runs"][0]["series"]
    series["tel_gpu0_vram_total"] = {"unit": "GiB", "points": [[0, 24.0], [20, 24.0]]}
    series["tel_ram_total_gib"] = {"unit": "GiB", "points": [[0, 64.0], [20, 64.0]]}
    html = report._free_llama_section(export)
    assert "Graphics card memory (VRAM) used&#58; GPU (of 24.0 GiB)" in html
    assert "Highest 23.8 of 24.0 GiB (99%) · average 15.8 GiB (66%)" in html
    assert "Memory (RAM) used&#58; PC (of 64.0 GiB)" in html and "Highest 31.0 of 64.0 GiB (48%)" in html


def test_the_chart_top_is_the_whole_memory():
    from app.charts import line_chart
    full = line_chart("VRAM", [0, 1], [2.0, 4.0], y_unit="GiB", y_full=16.0)
    alone = line_chart("VRAM", [0, 1], [2.0, 4.0], y_unit="GiB")
    assert full != alone and "16" in full
