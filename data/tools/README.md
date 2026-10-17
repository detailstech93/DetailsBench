# Tool spec files

One JSON file per external command-line benchmark. Loaded by `app/automation/cli_workload.py`, validated by
`app/automation/tool_spec.py`. A file with errors is skipped and its errors are shown; it never stops the app.

| Key | Required | Meaning |
|---|---|---|
| `id` | yes | `[a-z0-9_]{2,40}`; stored as the benchmark name. Ids starting with `selftest_` are stored as test data. |
| `display_name` | yes | Shown in the UI. |
| `executable` | yes | `{"python": true}` (this app's Python) or `{"candidates": ["%ProgramFiles%\\Tool\\tool.exe"], "path_names": ["tool"]}`. `%VAR%` comes from the environment. |
| `args` | no | List of strings. `{output_dir}` becomes the run folder. No shell is used. |
| `timeout_seconds` | yes | 1 to 86400. On timeout the tool and its own child processes are terminated. |
| `success_exit_codes` | no | Default `[0]`. |
| `parser` | yes | `{"type": "regex", "pattern": "...(?P<score>...)..."}`, `{"type": "json", "path": "a.b"}` or `{"type": "last_number"}`; optional `unit`, `higher_is_better`. |
| `verified` | yes | `true` only after the command and the output format were checked on a real machine (raw output). |
| `license`, `notes` | no | Free text. |
| `requires_elevation` | no | Must be `false`: tools that need administrator rights are run assisted, not automated. |

Rules: only documented commands (no invented flags); `verified: false` until checked with raw output; never a tool
that uploads results by default without the user's consent.
