# Failure cases

What the app does when something goes wrong, and what shows it. "Test" = a unit test with fake programs
(never the host machine); "Real" = a `scripts/verify_*.py` run on the development computer with its raw output
(2026-10-17/18). The rule behind all of them: **a run that did not produce a result is stored with its status
and reason, never with a score, and never as 0.**

## Runs and plans

| Situation | What happens | Evidence |
|---|---|---|
| Tool not installed | Listed as unavailable with the places looked in; cannot be ticked; a run is `skipped` | Test: `test_tool_spec.py` (`test_resolve_not_found`), `test_storage_tools.py`, `test_simulation_script.py` |
| Tool exits with an error code | `failed`, exit code and output files kept, diagnostic snapshot in the run folder | Test: `test_automation_runner.py::test_failure_stored_without_score_and_gets_diagnostics` |
| Output has no result | `invalid`, no score | Test: `test_workload_failures_never_score` (storage, llama-benchy), `test_parse_failures_never_score` (MLPerf) |
| Tool hangs past its time limit | Its process tree (only ours) is ended; `timed_out` | Test: `test_automation_process.py::test_timeout_terminates_only_our_tree` |
| Emergency stop (button or Ctrl+Shift+X) | Current run `cancelled`, its tree ended, no further run; runs not started are counted, not stored | Test: `test_emergency_stop_mid_run`, `test_stop_during_cooldown` |
| A workload raises an exception | That run `failed` ("workload raised ..."), the plan continues | Test: `test_automation_runner.py` |
| App crashes / power loss / forced close mid-plan | The plan journal stays; next start offers Resume: the interrupted run is stored `failed` (no result), the rest runs in the same session | Test: `test_plan_journal.py`, `test_automation_ui.py::test_resume_or_discard_an_interrupted_plan`. Real: `scripts/verify_resume.py` (7-Zip plan killed by force during run 2, resumed: run 2 failed, run 3 completed) |
| Journal unreadable or written by another version | Ignored (no Resume button) | Test: `test_plan_journal.py::test_unusable_journal_is_ignored` |
| Session deleted before resuming | Resume refused with the reason; Discard still works | Test: `test_resume_or_discard_an_interrupted_plan` |
| A tool of the interrupted plan is gone | Named in the Resume question and skipped | Test: `test_resume_restores_settings_and_reports_missing_tools` |
| Inconsistent result object (completed without score, failed with score) | Rejected in code before it can be stored | Test: `test_automation_process.py::test_workload_result_rejects_inconsistent` |
| Bad tool spec / simulation script file | Listed under "Spec errors", the others still load | Test: `test_tool_spec.py`, `test_simulation_script.py::test_shipped_scripts_are_valid_and_chords_parse` |

## Simulation scripts (keys typed into a program)

| Situation | What happens | Evidence |
|---|---|---|
| Another program has the focus, Windows refuses to switch | **No key is sent**; `failed` ("no key was sent"), program closed | Test: `test_no_key_is_sent_when_another_program_has_the_focus` |
| Window or control never appears | `timed_out` after the step's limit; program closed (by force after 10 s if it does not close) | Test: `test_cancel_timeout_and_not_installed`, `test_missing_result_is_invalid_and_stubborn_program_is_ended` |
| Program closes or crashes during the script | `failed` ("the program ended before ...") 10 s after its last process ended (a launcher may hand over to the game) | Test: `test_program_that_ends_early_fails_at_once`, `test_game_closed_before_its_result_fails_at_once` |
| Result text missing or not a number | `invalid`; the window's control texts are saved (`window_controls.json`) | Test: `test_missing_result_is_invalid_and_stubborn_program_is_ended` |
| Game's result file has other keys / is not JSON | `invalid`; the file is copied to the run folder to fix the script | Test: `test_steam_game_failures_never_score` |
| Steam game does not start | `failed` with Steam's message; nothing is closed (nothing was started) | Test: `test_steam_game_failures_never_score` |

## Games and frame capture

| Situation | What happens | Evidence |
|---|---|---|
| UAC prompt for PresentMon declined or not answered | `cancelled` / `failed`, the game is not started | Test: `test_game_benchmark.py::test_declined_uac_starts_no_game` |
| Game does not start | PresentMon stopped, nothing stored | Test: `test_game_that_does_not_start_stops_presentmon_and_stores_nothing` |
| Recording shorter than a window | That window `invalid` without a score | Test: `test_window_the_recording_does_not_reach_is_invalid_without_score` |
| No frames from the game | `failed` | Test: `test_no_frames_from_the_game_is_a_failure` |
| Game does not close | Closed politely first, by force after 30 s (only processes the launch started) | Test: `test_polite_close_first`, `test_force_after_the_grace_time_and_problems_reported` |

## Data and packaging

| Situation | What happens | Evidence |
|---|---|---|
| `settings.json` malformed | Ignored with a note, default database path used | Test: `test_paths.py` |
| Old database in the project folder | Copied once to the per-user folder; the original is never changed or deleted | Test: `test_paths.py` |
| Packaged app (no Python) | Python-based specs unavailable with the reason; llama-benchy only through `PCBM_LLAMA_BENCHY`; commit read from `build_info.json` | Test: `test_python_spec_is_unavailable_in_the_packaged_app`, `test_benchy_path_override_and_packaged_app`, `test_packaged_app_reads_the_commit_it_was_built_from`. Real: `scripts/verify_package.py` (window after 6.1 s, exit 0, new empty database) |

## Not covered yet

- Disk full: only the database side is tested (`test_database_error_ends_the_plan_but_keeps_it_resumable`: the
  plan ends as "plan aborted", the journal stays, Resume stores the run whose result could not be written as
  failed with the crash message, which is not the exact reason). A real full disk was not tried.
- Frame-capture benchmarks are not in the plan journal: a crash during one is not resumed.
- The Cyberpunk 2077 script is not verified on a real run (see README, Simulation scripts).
- No test on a second computer or on ARM.
