"""The one page the remote-start server serves (app/remote/server.py). Everything is inline: the page loads nothing
from the internet. Text from the app is always put in with textContent, never as HTML. The reports are shown in a
frame of the same server (/report/<name>, opened with the cookie the code gives)."""

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PC Benchmark Manager: remote</title>
<style>
:root { --bg:#f3f5f8; --card:#fff; --text:#1d2330; --muted:#667085; --line:#d9dee7; --accent:#2e7d32;
        --accent-soft:#e8f5e9; --stop:#c62828; --warn:#b26a00; --warn-soft:#fff4e0; --run:#1565c0; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#111418; --card:#1b2027; --text:#e7ebf0; --muted:#98a2b3; --line:#2f3640; --accent:#4caf50;
          --accent-soft:#1d3320; --stop:#ef5350; --warn:#ffb74d; --warn-soft:#33270f; --run:#64b5f6; }
}
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:15px/1.45 system-ui, sans-serif; }
main { max-width:760px; margin:0 auto; padding:16px; }
header { display:flex; align-items:center; justify-content:space-between; gap:8px; margin:4px 0 14px; }
h1 { font-size:20px; margin:0; }
h2 { font-size:16px; margin:0 0 10px; }
section { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px; margin-bottom:12px; }
.muted { color:var(--muted); font-size:13px; }
.hidden { display:none !important; }
input[type=text], input[type=number], select { font:inherit; padding:8px; border:1px solid var(--line);
  border-radius:8px; background:var(--bg); color:var(--text); width:100%; }
button, .btn { font:inherit; padding:9px 14px; border-radius:8px; border:1px solid var(--line); background:var(--bg);
  color:var(--text); cursor:pointer; text-decoration:none; display:inline-block; }
button.go { background:var(--accent); border-color:var(--accent); color:#fff; font-weight:600; }
button.stop { background:var(--stop); border-color:var(--stop); color:#fff; font-weight:600; }
button:disabled { opacity:.45; cursor:default; }
.row { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
.grid { display:grid; grid-template-columns:repeat(3, 1fr); gap:8px; }
.grid label { font-size:13px; color:var(--muted); }
.pill { font-size:12px; font-weight:700; padding:3px 10px; border-radius:20px; color:#fff; background:var(--muted); }
.pill.on { background:var(--run); }
.big { font-size:18px; font-weight:600; overflow-wrap:anywhere; }
.bar { position:relative; height:22px; background:var(--line); border-radius:11px; overflow:hidden; margin:10px 0 6px; }
.bar div { height:100%; width:0; background:var(--accent); transition:width .6s; }
.bar span { position:absolute; inset:0; text-align:center; font-size:12px; font-weight:700; line-height:22px;
  color:#fff; text-shadow:0 0 3px #000, 0 0 2px #000; }
.times { display:flex; gap:16px; flex-wrap:wrap; }
.plan { margin-top:10px; border-top:1px solid var(--line); }
.ptest { display:grid; grid-template-columns:auto 1fr auto; gap:4px 10px; align-items:center; padding:8px 0;
  border-bottom:1px solid var(--line); }
.ptest:last-child { border-bottom:0; }
.dot { width:12px; height:12px; border-radius:50%; background:var(--line); }
.dot.running { background:var(--run); box-shadow:0 0 0 4px rgba(21,101,192,.18); }
.dot.done { background:var(--accent); }
.dot.failed { background:var(--stop); }
.ptest .scores { grid-column:2 / 4; font-size:13px; color:var(--muted); overflow-wrap:anywhere; }
.ptest .count { font-size:13px; color:var(--muted); white-space:nowrap; }
.group { margin-top:12px; font-weight:700; font-size:13px; text-transform:uppercase; letter-spacing:.04em;
  color:var(--muted); }
.test { display:flex; gap:10px; align-items:flex-start; padding:8px 0; border-bottom:1px solid var(--line); }
.test:last-child { border-bottom:0; }
.test input { margin-top:4px; width:18px; height:18px; flex:none; }
.test small { color:var(--muted); display:block; }
.test.off { opacity:.55; }
.test .why { color:var(--muted); font-size:12px; display:block; }
.test .warn { color:var(--warn); background:var(--warn-soft); font-size:12px; display:block; border-radius:6px;
  padding:3px 6px; margin-top:3px; }
dl { display:grid; grid-template-columns:max-content 1fr; gap:4px 12px; margin:8px 0 0; }
dt { color:var(--muted); }
dd { margin:0; overflow-wrap:anywhere; }
details summary { cursor:pointer; font-weight:600; }
.error { color:var(--stop); }
.ok { color:var(--accent); }
iframe { width:100%; height:70vh; border:1px solid var(--line); border-radius:8px; background:#fff; margin-top:10px; }
a { color:var(--accent); }
.sticky { position:sticky; bottom:0; background:var(--card); padding-top:10px; }
</style>
</head>
<body>
<main>
<header><h1>PC Benchmark Manager</h1><span id="pill" class="pill">remote</span></header>

<section id="login">
  <h2>Code</h2>
  <p class="muted">Type the code shown in the app on the PC (Tests screen, Remote). It changes every time remote
  mode is switched on.</p>
  <div class="row"><input id="code" type="text" autocomplete="off" autocapitalize="characters" maxlength="16"
    style="max-width:220px"><button id="enter" class="go">Connect</button></div>
  <p id="loginError" class="error"></p>
</section>

<div id="app" class="hidden">
  <section>
    <h2>Now</h2>
    <div id="stateText" class="big">...</div>
    <div id="stepText" class="muted"></div>
    <div class="bar"><div id="bar"></div><span id="barText"></span></div>
    <div class="times muted"><span id="elapsed"></span><span id="left"></span></div>
    <div id="plan" class="plan hidden"></div>
    <div class="row" style="margin-top:12px">
      <button id="stop" class="stop" disabled>Emergency stop</button>
    </div>
    <p id="message" class="muted"></p>
  </section>

  <section id="reportBox" class="hidden">
    <h2>Report</h2>
    <div class="row">
      <select id="reportPick" style="flex:1; min-width:200px"></select>
      <a id="reportOpen" class="btn" target="_blank" rel="noopener">Open full screen</a>
    </div>
    <iframe id="reportFrame" title="Report"></iframe>
  </section>

  <section>
    <h2>Tests</h2>
    <p class="muted">Every test for this PC. Greyed ones need someone at the PC and start there; an orange note
    means someone must answer a Windows prompt on the PC.</p>
    <div class="row"><button id="all">Select all</button><button id="none">Select none</button>
      <span id="picked" class="muted"></span></div>
    <div id="tests"></div>
    <div class="sticky">
      <div class="grid">
        <div><label for="rep">Measured runs</label><input id="rep" type="number" min="1" max="50"></div>
        <div><label for="warm">Warm-up runs</label><input id="warm" type="number" min="0" max="10"></div>
        <div><label for="cool">Pause (s)</label><input id="cool" type="number" min="0" max="3600"></div>
      </div>
      <p id="note" class="muted"></p>
      <button id="start" class="go" style="width:100%">Start</button>
    </div>
  </section>

  <section>
    <details><summary>This PC</summary><dl id="specs"></dl></details>
  </section>
</div>
</main>
<script>
"use strict";
let code = "";
try { code = sessionStorage.getItem("pcbm-code") || ""; } catch (e) {}
const $ = (id) => document.getElementById(id);
let running = false, timer = null, shownReport = "", knownReport = null, wasRunning = null;

async function call(path, body) {
  const opt = { headers: { "X-Code": code }, credentials: "same-origin" };
  if (body !== undefined) {
    opt.method = "POST"; opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body);
  }
  const r = await fetch(path, opt);
  if (r.status === 401) {
    const d = await r.json().catch(() => ({}));
    logout(d.error || "wrong code");
    throw new Error("code");
  }
  return r;
}

function logout(why) {
  if (timer) clearInterval(timer);
  timer = null;
  try { sessionStorage.removeItem("pcbm-code"); } catch (e) {}
  $("app").classList.add("hidden");
  $("login").classList.remove("hidden");
  $("loginError").textContent = why;
}

function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined && text !== null) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}

function countPicked() {
  const n = document.querySelectorAll("#tests input:checked").length;
  $("picked").textContent = n ? n + " selected" : "";
}

function showInfo(info) {
  const specs = $("specs");
  specs.replaceChildren();
  for (const [k, v] of info.specs) { specs.append(el("dt", k), el("dd", v)); }
  const box = $("tests");
  box.replaceChildren();
  let group = null;
  for (const t of info.tests) {
    if (t.group !== group) { group = t.group; box.append(el("div", group, "group")); }
    const row = el("label", undefined, "test" + (t.can_start ? "" : " off"));
    const cb = el("input"); cb.type = "checkbox"; cb.value = t.id; cb.disabled = !t.can_start;
    cb.addEventListener("change", countPicked);
    const txt = el("span", t.name);
    const meta = [];
    if (t.minutes) meta.push("about " + t.minutes + " min");
    meta.push(t.verified ? "verified on a real run" : "not verified yet");
    txt.append(el("small", meta.join(", ")));
    if (t.why) txt.append(el("span", t.why, "why"));
    if (t.warning) txt.append(el("span", t.warning, "warn"));
    row.append(cb, txt);
    box.append(row);
  }
  if (!info.tests.length) box.append(el("p", "No test for this PC was found.", "muted"));
  $("rep").value = info.defaults.repetitions;
  $("warm").value = info.defaults.warmup_runs;
  $("cool").value = info.defaults.cooldown_seconds;
  $("note").textContent = info.note || "";
  countPicked();
}

const STATE_WORDS = { running: "running now", waiting: "waiting", done: "done", failed: "failed",
                      "not run": "not run" };

function showPlan(tests) {
  const box = $("plan");
  box.replaceChildren();
  box.classList.toggle("hidden", !tests || !tests.length);
  for (const t of tests || []) {
    const row = el("div", undefined, "ptest");
    const name = el("div");
    name.append(el("strong", t.name), el("span", "  " + (STATE_WORDS[t.state] || t.state), "muted"));
    let count = t.runs + " / " + t.planned + " runs";
    if (t.failed) count += ", " + t.failed + " failed";
    row.append(el("span", undefined, "dot " + t.state.replace(" ", "")), name, el("span", count, "count"));
    if (t.scores.length) {
      const unit = t.unit ? " " + t.unit : "";
      row.append(el("div", "Scores: " + t.scores.join(", ") + unit +
                     (t.scores.length > 1 ? "   (average " + t.average + unit + ")" : ""), "scores"));
    }
    box.append(row);
  }
}

async function showReports(pick) {
  try {
    const d = await (await call("/api/reports")).json();
    const sel = $("reportPick");
    sel.replaceChildren();
    for (const r of d.reports || []) {
      const o = el("option", r.time + "  " + r.name); o.value = r.name; sel.append(o);
    }
    if (!sel.options.length) { $("reportBox").classList.add("hidden"); return; }
    $("reportBox").classList.remove("hidden");
    const name = pick && [...sel.options].some((o) => o.value === pick) ? pick : sel.options[0].value;
    sel.value = name;
    openReport(name);
  } catch (e) {}
}

function openReport(name) {
  const url = "/report/" + encodeURIComponent(name);
  shownReport = name;
  $("reportOpen").href = url;
  $("reportFrame").src = url;
}

function showState(s) {
  running = s.running;
  $("pill").textContent = s.running ? "running" : "idle";
  $("pill").className = "pill" + (s.running ? " on" : "");
  $("stateText").textContent = s.text || (s.running ? "Running..." : "Nothing is running.");
  $("stepText").textContent = s.step || "";
  const p = s.percent == null ? (s.running ? null : 0) : s.percent;
  $("bar").style.width = (p == null ? 100 : p) + "%";
  $("barText").textContent = p == null ? "" : p + "%";
  $("elapsed").textContent = s.elapsed ? "Time: " + s.elapsed : "";
  $("left").textContent = s.left ? "Left: " + s.left : "";
  showPlan(s.tests);
  $("stop").disabled = !s.running;
  $("start").disabled = s.running;
  if (s.report_name && !s.running && (s.report_name !== knownReport || wasRunning)) {
    const finished = wasRunning;              // a run just ended: show its report
    knownReport = s.report_name;
    showReports(s.report_name).then(() => {
      if (finished) $("reportBox").scrollIntoView({ behavior: "smooth" });
    });
  }
  wasRunning = s.running;
}

async function refresh() {
  try { showState(await (await call("/api/state")).json()); }
  catch (e) { if (e.message !== "code") $("stateText").textContent = "The PC does not answer (is the app open?)."; }
}

async function connect() {
  code = $("code").value.trim().toUpperCase();
  $("loginError").textContent = "";
  try {
    const r = await call("/api/info");
    const info = await r.json();
    try { sessionStorage.setItem("pcbm-code", code); } catch (e) {}
    $("login").classList.add("hidden");
    $("app").classList.remove("hidden");
    showInfo(info);
    await refresh();
    if (!shownReport) showReports("");
    if (!timer) timer = setInterval(refresh, 2000);
  } catch (e) {
    if (e.message !== "code") $("loginError").textContent = "The PC does not answer.";
  }
}

$("enter").onclick = connect;
$("code").addEventListener("keydown", (e) => { if (e.key === "Enter") connect(); });
$("all").onclick = () => { document.querySelectorAll("#tests input:not(:disabled)").forEach((c) => c.checked = true);
                           countPicked(); };
$("none").onclick = () => { document.querySelectorAll("#tests input").forEach((c) => c.checked = false);
                            countPicked(); };
$("reportPick").onchange = () => openReport($("reportPick").value);
$("start").onclick = async () => {
  const ids = [...document.querySelectorAll("#tests input:checked")].map((c) => c.value);
  if (!ids.length) { $("message").textContent = "Tick at least one test."; return; }
  const body = { ids, repetitions: parseInt($("rep").value, 10), warmup_runs: parseInt($("warm").value, 10),
                 cooldown_seconds: parseInt($("cool").value, 10) };
  $("message").textContent = "Starting...";
  try {
    const d = await (await call("/api/start", body)).json();
    $("message").textContent = d.text || d.error || "";
    refresh();
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (e) { if (e.message !== "code") $("message").textContent = "The PC does not answer."; }
};
$("stop").onclick = async () => {
  if (!confirm("Stop the run now? The current run is cut off and stored as stopped.")) return;
  try { $("message").textContent = (await (await call("/api/stop", {})).json()).text || ""; refresh(); }
  catch (e) {}
};
if (code) { $("code").value = code; connect(); }
</script>
</body>
</html>
"""
