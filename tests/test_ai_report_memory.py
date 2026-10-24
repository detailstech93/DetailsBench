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


def test_every_llama_benchy_measurement_is_in_the_report():
    # owner, 2026-10-22: the report showed one number; llama-benchy measures prompt processing, token generation,
    # time to first token, context depths and the prefix cache
    export = _export("llama_benchy")
    m = lambda v, u: {"mean": v, "unit": u}
    export["metric_summaries"] = {"llama_benchy": {
        "pp_tok_s@d0_c1": m(5.31, "tok/s"), "tg_tok_s@d0_c1": m(3.44, "tok/s"), "e2e_ttft_ms@d0_c1": m(86171.8, "ms"),
        "pp_tok_s@d4096_c1": m(4.2, "tok/s"), "tg_tok_s@d4096_c1": m(3.1, "tok/s"),
        "ctx_pp_tok_s@d4096_c1": m(4.0, "tok/s"), "ctx_e2e_ttft_ms@d4096_c1": m(1000000.0, "ms"),
        "tel_cpu_load_avg": m(50.0, "%")}}
    html = report._free_llama_section(export)
    assert "Prompt processing</div><div class=\"value\">5.3 <span>tok/s" in html
    assert "Time to first token</div><div class=\"value\">86.17 <span>s" in html
    assert "All llama-benchy measurements" in html
    assert "<tr><td>1</td><td>0</td><td>measured</td><td>5.3</td><td>3.4</td><td>86.17</td></tr>" in html
    assert ("<tr><td>1</td><td>4,096</td><td>context prefill (prefix cache)</td><td>4.0</td><td>-</td>"
            "<td>1,000.00</td></tr>") in html
    assert html.index("context prefill") < html.index("<td>4,096</td><td>measured")
    fixed = report._model_sections({**export, "runs": [{**export["runs"][0], "benchmark_name": "llama_benchy_x"}],
                                    "summaries": {"llama_benchy_x": {"stats": {"mean": 3.4}}},
                                    "metric_summaries": {"llama_benchy_x": export["metric_summaries"]["llama_benchy"]}})
    assert "All llama-benchy measurements" in fixed and "Time to first token" in fixed
    assert "All llama-benchy measurements" not in report._mlperf_section(_export("mlperf_client"))


def test_the_report_names_pp_tg_context_depth_and_concurrency():
    # owner, 2026-10-24: the llama-benchy report said neither pp nor tg nor context depth nor concurrency
    export = _export("llama_benchy")
    export["runs"][0]["note"] = "Model: gemma; Context: 4096; Parallel: 2; pp/tg: 512/128"
    m = lambda v: {"mean": v, "unit": "tok/s"}
    export["metric_summaries"] = {"llama_benchy": {"tg_tok_s@d0_c1": m(3.0), "tg_tok_s@d4096_c1": m(2.0),
                                                   "tg_tok_s@d0_c2": m(5.0), "ctx_pp_tok_s@d8192_c1": m(1.0)}}
    html = report._free_llama_section(export)
    assert "<h4>Test settings</h4>" in html
    for row in ("<tr><th>Prompt tokens (pp)</th><td>512</td></tr>", "<tr><th>Generated tokens (tg)</th><td>128</td></tr>",
                "<tr><th>Context depth (tokens)</th><td>0, 4,096</td></tr>",
                "<tr><th>Concurrency (requests at once)</th><td>1, 2</td></tr>",
                "<tr><th>Loaded context (tokens)</th><td>4096</td></tr>"):
        assert row in html
    assert "pp/tg" not in html


def test_a_fixed_model_that_did_not_finish_still_shows_its_settings():
    export = _export("llama_benchy_qwen35_4b")
    export["runs"][0].update(status="cancelled", score=None, note="stopped by user")
    html = report._model_sections(export)
    assert "The test did not finish: stopped by user." in html
    assert "<tr><th>Prompt tokens (pp)</th><td>512</td></tr>" in html
    assert "<tr><th>Concurrency (requests at once)</th><td>1</td></tr>" in html
