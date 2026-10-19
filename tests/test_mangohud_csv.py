"""MangoHud frame logs (Linux) read into frame times: made-up logs in MangoHud's documented layout."""

import os
import time

from app.frames.mangohud_csv import find_new_logs, parse_mangohud_log
from app.frames.stats import compute_frame_stats

HEAD = ("os,cpu,gpu,ram,kernel,driver,cpuscheduler\n"
        "Ubuntu 24.04,AMD Ryzen 7 7800X3D,AMD Radeon RX 7900 XTX,32GB,6.8.0,Mesa 24.0,none\n"
        "fps,frametime,cpu_load,cpu_power,gpu_load,cpu_temp,gpu_temp,gpu_core_clock,gpu_mem_clock,gpu_vram_used,"
        "gpu_power,ram_used,swap_used,process_rss,cpu_mhz,elapsed\n")


def _row(frametime_ms, elapsed_s):
    return f"{1000 / frametime_ms:.1f},{frametime_ms},20,30,90,60,70,2500,1250,8.1,300,12.0,0.0,3.2,4800," \
           f"{int(elapsed_s * 1e9)}\n"


def test_frames_are_read_by_column_name_and_cut_to_the_window():
    text = HEAD + "".join(_row(10.0 if i % 2 else 20.0, i * 0.015) for i in range(1, 2001))
    whole = parse_mangohud_log(text)
    assert whole.error is None and whole.rows_used == 2000 and set(whole.frametimes_ms) == {10.0, 20.0}
    part = parse_mangohud_log(text, start_seconds=10.0, seconds=5.0)
    assert 300 < part.rows_used < 340 and part.rows_total == 2000
    stats = compute_frame_stats(whole.frametimes_ms)
    assert round(stats.avg_fps) == 67                   # 1000 / 15 ms


def test_old_logs_bad_rows_and_files_that_are_not_logs():
    old = "fps,frametime,cpu_load,gpu_load,elapsed\n60,16.6,10,90,1000000\nx,,1,1,2\n60,0,1,1,3\n"
    r = parse_mangohud_log(old)
    assert r.frametimes_ms == [16.6] and r.rows_skipped == 2
    assert parse_mangohud_log("a,b\n1,2\n").error.startswith("not a MangoHud frame log")
    assert "in that time window" in parse_mangohud_log(HEAD + _row(10, 1), start_seconds=5, seconds=5).error
    no_elapsed = "fps,frametime\n60,16.6\n"
    assert parse_mangohud_log(no_elapsed).rows_used == 1 and parse_mangohud_log(no_elapsed, 1, 5).rows_used == 0


def test_only_new_frame_logs_are_found(tmp_path):
    since = time.time() - 5
    old = tmp_path / "Game_2026-10-18_20-00-00.csv"
    new = tmp_path / "wine-Game_2026-10-19_09-15-00.csv"
    for p in (old, new, tmp_path / "wine-Game_2026-10-19_09-15-00_summary.csv", tmp_path / "notes.csv"):
        p.write_text(HEAD, encoding="utf-8")
    os.utime(old, (since - 3600, since - 3600))
    assert find_new_logs(tmp_path, since) == [new]
    assert find_new_logs(tmp_path / "missing", since) == []
