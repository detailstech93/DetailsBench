"""The one page the remote-start server serves (app/remote/server.py). Everything is inline: the page loads nothing
from the internet. Text from the app is always put in with textContent, never as HTML."""

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PC Benchmark Manager: remote start</title>
<style>
:root { --bg:#f6f7f9; --card:#fff; --text:#1d2330; --muted:#667085; --line:#d9dee7; --accent:#2e7d32;
        --stop:#c62828; --warn:#b26a00; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#14171c; --card:#1d2229; --text:#e7ebf0; --muted:#98a2b3; --line:#2f3640; --accent:#4caf50;
          --stop:#ef5350; --warn:#ffb74d; }
}
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:15px/1.45 system-ui, sans-serif; }
main { max-width:720px; margin:0 auto; padding:16px; }
h1 { font-size:20px; margin:4px 0 12px; }
h2 { font-size:16px; margin:0 0 8px; }
section { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px; margin-bottom:12px; }
.muted { color:var(--muted); font-size:13px; }
.hidden { display:none; }
input[type=text], input[type=number] { font:inherit; padding:8px; border:1px solid var(--line); border-radius:6px;
  background:var(--bg); color:var(--text); width:100%; }
button { font:inherit; padding:9px 14px; border-radius:6px; border:1px solid var(--line); background:var(--bg);
  color:var(--text); cursor:pointer; }
button.go { background:var(--accent); border-color:var(--accent); color:#fff; }
button.stop { background:var(--stop); border-color:var(--stop); color:#fff; }
button:disabled { opacity:.5; cursor:default; }
.row { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
.grid { display:grid; grid-template-columns:repeat(3, 1fr); gap:8px; }
.grid label { font-size:13px; color:var(--muted); }
.group { margin-top:10px; font-weight:600; }
.test { display:flex; gap:8px; align-items:flex-start; padding:6px 0; border-bottom:1px solid var(--line); }
.test:last-child { border-bottom:0; }
.test small { color:var(--muted); display:block; }
dl { display:grid; grid-template-columns:max-content 1fr; gap:4px 12px; margin:0; }
dt { color:var(--muted); }
dd { margin:0; overflow-wrap:anywhere; }
.bar { height:10px; background:var(--line); border-radius:5px; overflow:hidden; margin:8px 0; }
.bar div { height:100%; width:0; background:var(--accent); transition:width .5s; }
.error { color:var(--stop); }
a { color:var(--accent); }
</style>
</head>
<body>
<main>
<h1>PC Benchmark Manager: remote start</h1>

<section id="login">
  <h2>Code</h2>
  <p class="muted">Type the code shown in the app on the PC (Tests screen, Remote start). It changes every time
  remote start is switched on.</p>
  <div class="row"><input id="code" type="text" autocomplete="off" autocapitalize="characters" maxlength="16"
    style="max-width:220px"><button id="enter" class="go">Connect</button></div>
  <p id="loginError" class="error"></p>
</section>

<div id="app" class="hidden">
  <section>
    <h2>Now</h2>
    <div id="stateText">...</div>
    <div class="bar"><div id="bar"></div></div>
    <div class="row">
      <button id="stop" class="stop" disabled>Emergency stop</button>
      <button id="report" disabled>Open the last report</button>
      <a id="reportLink" class="hidden" target="_blank" rel="noopener">Report ready: tap to open</a>
    </div>
    <p id="message" class="muted"></p>
  </section>

  <section>
    <h2>This PC</h2>
    <dl id="specs"></dl>
  </section>

  <section>
    <h2>Tests</h2>
    <p class="muted">Only tests that run by themselves are here. Tests that need someone at the PC (typed
    scores, a license still to accept, Steam games with frame capture) start from the PC.</p>
    <div class="row"><button id="all">Select all</button><button id="none">Select none</button></div>
    <div id="tests"></div>
    <div class="grid" style="margin-top:12px">
      <div><label for="rep">Measured runs</label><input id="rep" type="number" min="1" max="50"></div>
      <div><label for="warm">Warm-up runs</label><input id="warm" type="number" min="0" max="10"></div>
      <div><label for="cool">Pause (s)</label><input id="cool" type="number" min="0" max="3600"></div>
    </div>
    <p id="note" class="muted"></p>
    <button id="start" class="go" style="margin-top:6px">Start</button>
  </section>
</div>
</main>
<script>
"use strict";
let code = "";
try { code = sessionStorage.getItem("pcbm-code") || ""; } catch (e) {}
const $ = (id) => document.getElementById(id);
let running = false, timer = null;

async function call(path, body) {
  const opt = { headers: { "X-Code": code } };
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
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
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
    const row = el("label", undefined, "test");
    const cb = el("input"); cb.type = "checkbox"; cb.value = t.id;
    const txt = el("span", t.name);
    const meta = [];
    if (t.minutes) meta.push("about " + t.minutes + " min");
    meta.push(t.verified ? "verified on a real run" : "not verified yet");
    txt.append(el("small", meta.join(", ")));
    row.append(cb, txt);
    box.append(row);
  }
  if (!info.tests.length) box.append(el("p", "No test can start from here on this PC now.", "muted"));
  $("rep").value = info.defaults.repetitions;
  $("warm").value = info.defaults.warmup_runs;
  $("cool").value = info.defaults.cooldown_seconds;
  $("note").textContent = info.note || "";
}

function showState(s) {
  running = s.running;
  $("stateText").textContent = s.text || (s.running ? "Running..." : "Nothing is running.");
  $("bar").style.width = (s.percent == null ? (s.running ? 100 : 0) : s.percent) + "%";
  $("stop").disabled = !s.running;
  $("start").disabled = s.running;
  $("report").disabled = !s.report;
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
    if (!timer) timer = setInterval(refresh, 2000);
  } catch (e) {
    if (e.message !== "code") $("loginError").textContent = "The PC does not answer.";
  }
}

$("enter").onclick = connect;
$("code").addEventListener("keydown", (e) => { if (e.key === "Enter") connect(); });
$("all").onclick = () => document.querySelectorAll("#tests input").forEach((c) => c.checked = true);
$("none").onclick = () => document.querySelectorAll("#tests input").forEach((c) => c.checked = false);
$("start").onclick = async () => {
  const ids = [...document.querySelectorAll("#tests input:checked")].map((c) => c.value);
  const body = { ids, repetitions: parseInt($("rep").value, 10), warmup_runs: parseInt($("warm").value, 10),
                 cooldown_seconds: parseInt($("cool").value, 10) };
  $("message").textContent = "Starting...";
  try {
    const d = await (await call("/api/start", body)).json();
    $("message").textContent = d.text || d.error || "";
    refresh();
  } catch (e) { if (e.message !== "code") $("message").textContent = "The PC does not answer."; }
};
$("stop").onclick = async () => {
  if (!confirm("Stop the run now? The current run is cut off and stored as stopped.")) return;
  try { $("message").textContent = (await (await call("/api/stop", {})).json()).text || ""; refresh(); }
  catch (e) {}
};
$("report").onclick = async () => {
  try {
    const r = await call("/api/report");
    if (!r.ok) { $("message").textContent = "No report yet."; return; }
    const url = URL.createObjectURL(new Blob([await r.text()], { type: "text/html" }));
    const a = $("reportLink"); a.href = url; a.classList.remove("hidden");
  } catch (e) {}
};
if (code) { $("code").value = code; connect(); }
</script>
</body>
</html>
"""
