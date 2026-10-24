/*
  app.js
  What the page does: asks the API (app/main.py) for the server's state and draws it.

  Plain JavaScript, no framework: the browser runs this file as it is, nothing to build.
  The page never sees the fault's hidden truth, only what GET /status shows, which is
  what a real admin could see on the server.
*/

// What each container does, in words a visitor understands without knowing Docker.
const ROLES = {
  web: "Website (nginx)",
  api: "Shop API",
  db: "Database (PostgreSQL)",
  cache: "Cache (Redis)",
};

// The health check addresses, in plain words.
const CHECK_NAMES = {
  "http://localhost/": "Shop homepage",
  "http://localhost/api/health": "API health check",
};

// The disks, in plain words.
const DISK_NAMES = {
  "/": "main disk",
  "/var/lib/postgresql": "database disk",
};

// How often the dashboard asks for fresh data (milliseconds). Every 5 s feels live
// without flooding the server with requests.
const REFRESH_EVERY = 5000;
// While Ultra builds a custom or random fault, ask how it's going this often.
const BUILD_CHECK_EVERY = 2000;

// What the page knows right now. The break buttons are only usable on a healthy server,
// with no fault being built and no request still on its way.
const state = {
  broken: false,    // the last GET /status had an alert
  building: false,  // Ultra is building a custom or random fault
  busy: false,      // a request from a button is on its way (stops double clicks)
  incident: null,   // the agent's run on this fault: null before "Start the investigation"
  view: 1,          // which step is on screen: 1 Break, 2 Investigate, 3 Decide, 4 Result
  focus: null,      // the part the agent is checking right now (e.g. "api"), lit up on the map
};


/** Turn text into safe HTML: a log line with "<" in it must show as text, not become a tag. */
function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

/**
 * Call the API and return its JSON answer.
 * On an error (4xx/5xx) it throws, with the API's own message ("detail") when there is one,
 * so the page can show "This server is already broken" instead of "Error 409".
 */
async function api(method, path, body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    // FastAPI's own errors (422) give a list in "detail"; ours give a sentence.
    const detail = typeof data.detail === "string" ? data.detail : `${response.status} ${response.statusText}`;
    throw new Error(detail);
  }
  return data;
}

/** "2026-10-12T00:19:11" -> "12 days", "3 hours", "5 minutes": how long ago that was. */
function timeSince(isoTime) {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(isoTime)) / 60000));
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"}`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} hour${hours === 1 ? "" : "s"}`;
  return `${Math.round(hours / 24)} days`;
}

/**
 * Green, amber or red for a disk or memory percentage, the same everywhere on the page.
 * Red only when it's full (98% and up), because red means "broken". From 80% it's amber:
 * worth watching, nothing is failing yet. (24 Oct: a disk fix moved a backup onto the main disk,
 * which went to 90%; with red from 90% the page looked broken although the verdict was FIXED.)
 */
function levelFor(percent) {
  if (percent >= 98) return "bad";
  if (percent >= 80) return "warn";
  return "ok";
}

/** A container's status as a colour and a word: green Running, amber Restarting, red Stopped. */
function containerState(c) {
  if (c.status === "restarting") return { level: "warn", label: "Restarting" };
  if (c.status === "exited") return { level: "bad", label: `Stopped (exit ${c.exit_code})` };
  return { level: "ok", label: "Running" };
}

/** One container as a card: name, role, a status label, image, uptime, memory bar. */
function containerCard(c) {
  const { level, label } = containerState(c);
  const memoryPercent = Math.round((c.memory_mb / c.memory_limit_mb) * 100);
  const uptime = c.status === "running" ? `up ${timeSince(c.since)}` : `since ${timeSince(c.since)} ago`;
  const restarts = c.restarts ? `, ${c.restarts} restart${c.restarts === 1 ? "" : "s"}` : "";

  return `
    <div class="card ${level}">
      <div class="card-title">${escapeHtml(c.name)} <span class="badge ${level}">${label}</span></div>
      <div class="role">${escapeHtml(ROLES[c.name] || "")}</div>
      <div class="image">${escapeHtml(c.image)}</div>
      <div class="facts">${uptime}${restarts}</div>
      <div class="facts">Memory ${c.memory_mb} / ${c.memory_limit_mb} MB</div>
      <div class="bar ${levelFor(memoryPercent)}"><span style="width:${Math.min(memoryPercent, 100)}%"></span></div>
    </div>`;
}

/** One health check: "Shop homepage ... OK 40 ms" or "... HTTP 503". */
function endpointRow(e) {
  const ok = e.status === 200;
  const result = ok ? `<span class="ok-text">OK</span> ${e.ms} ms` : `<span class="bad-text">HTTP ${e.status}</span>`;
  return `<div class="row row-line"><span>${escapeHtml(CHECK_NAMES[e.url] || e.url)}</span><span>${result}</span></div>`;
}

/** One disk with a usage bar: red when it's nearly full. */
function diskRow(d) {
  const level = levelFor(d.used_percent);
  return `
    <div class="row">
      <div class="row-line"><span>${escapeHtml(d.mount)}
          <span class="muted">${DISK_NAMES[d.mount] ? `(${DISK_NAMES[d.mount]})` : ""}</span></span>
        <span class="${level}-text">${d.used_percent}%</span></div>
      <div class="bar ${level}"><span style="width:${d.used_percent}%"></span></div>
      <div class="muted">${d.used_gb} of ${d.size_gb} GB used</div>
    </div>`;
}

/** One scheduled job: on or off. */
function jobRow(j) {
  const onOff = j.enabled ? `<span class="ok-text">on</span>` : `<span class="muted">off</span>`;
  return `<div class="row row-line"><span>${escapeHtml(j.name)} <span class="muted">(${escapeHtml(j.schedule)})</span></span>${onOff}</div>`;
}

// ---------- The map: customers -> web -> api -> database and cache ----------

// How bad each colour is, to keep the worst one when a part has several problems.
const RANK = { ok: 0, warn: 1, bad: 2 };

/**
 * How each part of the shop is doing, from one GET /status answer: a colour and a short note
 * ("running", "HTTP 502", "disk 100%"). Not only the container's own status: all four can say
 * "Running" while the database disk is full, and the map should show where it hurts.
 */
function partStates(status, resolved) {
  const parts = {};
  // The box shows its first note, so the note of the worst problem goes first: a red box must say
  // why it's red. (24 Oct: a cache box was red from full memory but said "restarting", the amber reason.)
  const add = (name, level, note) => {
    const part = parts[name];
    if (!part || level === "ok") return;
    if (RANK[level] > RANK[part.level]) {
      part.level = level;
      part.notes.unshift(note);
    } else {
      part.notes.push(note);
    }
  };

  for (const c of status.containers) {
    const { level, label } = containerState(c);
    parts[c.name] = { level: "ok", notes: [] };
    add(c.name, level, label.toLowerCase());
    const memory = Math.round((c.memory_mb / c.memory_limit_mb) * 100);
    add(c.name, levelFor(memory), `memory ${memory}%`);
  }
  for (const e of status.endpoints) {
    if (e.status !== 200) add(e.url.includes("/api/") ? "api" : "web", "bad", `HTTP ${e.status}`);
  }
  for (const d of status.disks) {
    // The database disk belongs to the database; the main disk is where nginx and the api write.
    const dbDisk = d.mount === "/var/lib/postgresql";
    const note = `${dbDisk ? "disk" : "main disk"} ${d.used_percent}%`;
    for (const owner of dbDisk ? ["db"] : ["web", "api"]) add(owner, levelFor(d.used_percent), note);
  }

  // The customers: unhappy while there's an alert that no fix has answered yet.
  parts.customers = status.alert && !resolved
    ? { level: "bad", notes: ["complaining"] }
    : { level: "ok", notes: [status.alert ? "happy again" : "happy"] };
  return parts;
}

/** One box on the map. The data-part name lets the page light it up while the agent checks it. */
function mapNode(name, role, part) {
  const note = part.notes[0] || "OK";
  return `<div class="map-node ${part.level}" data-part="${name}">
            <span class="map-name">${escapeHtml(name)}</span>
            ${role ? `<span class="map-role">${escapeHtml(role)}</span>` : ""}
            <span class="map-note">${escapeHtml(note)}</span>
          </div>`;
}

/**
 * Draw the map in the order a customer's request travels: customers -> web -> api, and the
 * api uses both the database and the cache. A line takes the colour of the part it leads to,
 * so a red line shows where requests stop getting through.
 */
function renderMap(status, resolved) {
  const p = partStates(status, resolved);
  document.getElementById("map").innerHTML = `
    ${mapNode("customers", "", p.customers)}
    <div class="map-line ${p.customers.level}"></div>
    ${mapNode("web", "nginx", p.web)}
    <div class="map-line ${p.web.level}"></div>
    ${mapNode("api", "shop app", p.api)}
    <div class="map-fork">
      <span class="fork-in ${p.api.level}"></span>
      <span class="fork-db ${p.db.level}"></span>
      <span class="fork-cache ${p.cache.level}"></span>
    </div>
    <div class="map-pair">
      ${mapNode("db", "PostgreSQL", p.db)}
      ${mapNode("cache", "Redis", p.cache)}
    </div>`;
}

/**
 * Which part a tool call looks at: get_logs(container=api) -> "api", a check of the api's
 * health address -> "api", anything under the database folder -> "db". null for calls that
 * look at the whole server (check_disk, get_metrics...).
 */
function toolTarget(call) {
  const container = call.match(/container=(\w+)/);
  if (container) return container[1];
  if (call.startsWith("check_endpoint(")) return call.includes("/api") ? "api" : "web";
  if (call.includes("postgresql")) return "db";
  return null;
}

/** Light up the part the agent is checking, on the map and in the status strip. */
function renderFocus() {
  for (const element of document.querySelectorAll("[data-part]")) {
    element.classList.toggle("focus", element.dataset.part === state.focus);
  }
}

/**
 * The small status strip on steps 2 and 3: one light per container and the alert in one line,
 * so the server is never out of sight while the visitor watches the agent or picks a fix.
 */
function renderStrip(status, resolved) {
  const lights = status.containers.map((c) => {
    const { level, label } = containerState(c);
    return `<span class="light ${level}" data-part="${escapeHtml(c.name)}" title="${escapeHtml(c.name)}: ${label}"></span>${escapeHtml(c.name)}`;
  }).join(" ");
  let text = "No complaints from customers.";
  if (status.alert) {
    text = resolved ? `<strong>${state.incident.verdict === "FIXED" ? "Fixed." : "Fixed for now."}</strong> ` +
                      escapeHtml(outcome(state.incident).back)
                    : `<strong>Alert:</strong> ${escapeHtml(status.alert)}`;
  }
  const strip = document.getElementById("strip");
  const open = document.getElementById("main").classList.contains("show-server");
  strip.className = `strip view s2 s3 ${status.alert ? (resolved ? "ok" : "bad") : ""}`;
  strip.innerHTML = `<span class="strip-inner"><span class="lights">${lights}</span>` +
                    `<span class="strip-text">${text}</span>` +
                    `<span class="strip-toggle">${open ? "Hide server ▴" : "Show server ▾"}</span></span>`;
}

/** Draw the whole dashboard from one GET /status answer. */
function renderStatus(status) {
  const alertBox = document.getElementById("alert");
  // The fault stays on the server until Reset, so the API keeps its alert. Once a fix
  // worked, show it as resolved: a red banner over a fixed shop would tell the wrong story.
  const verdict = state.incident && state.incident.verdict;
  const resolved = verdict === "FIXED" || verdict === "FIXED FOR NOW";
  renderStrip(status, resolved);
  renderMap(status, resolved);
  if (status.alert) {
    alertBox.className = resolved ? "alert resolved" : "alert";
    // After a fix, the old complaint is gone from the banner: "placing an order fails" under a
    // green label still read like a current error (Pavan, 24 Oct). Instead it says what works
    // again for THIS fault, from the server's recovery logs, and what's left to watch.
    alertBox.innerHTML = resolved ? resolvedText(state.incident) : `<strong>Alert:</strong> ${escapeHtml(status.alert)}`;
    alertBox.hidden = false;
  } else {
    alertBox.hidden = true;
  }
  state.broken = Boolean(status.alert);
  updateBreakPanel();
  updateAgentPanel();
  updateStepper();
  document.getElementById("containers").innerHTML = status.containers.map(containerCard).join("");
  document.getElementById("endpoints").innerHTML = status.endpoints.map(endpointRow).join("");
  document.getElementById("disks").innerHTML = status.disks.map(diskRow).join("");
  document.getElementById("jobs").innerHTML = status.jobs.map(jobRow).join("");
  renderFocus();  // the map and strip were just redrawn: light the checked part up again
}

/** Ask for the latest server state and redraw. Shows a short note if the API can't be reached. */
async function refresh() {
  const updated = document.getElementById("updated");
  try {
    renderStatus(await api("GET", "/status"));
    updated.textContent = `Updated ${new Date().toLocaleTimeString()}`;
  } catch (error) {
    updated.textContent = `Can't reach the server: ${error.message}`;
  }
}


// ---------- 1. Break the server ----------

/** Show a short message under the break panel. kind: "" (neutral), "ok" or "bad". Empty text hides it. */
function showBreakMessage(html, kind = "") {
  const box = document.getElementById("break-message");
  box.className = `message ${kind}`;
  box.innerHTML = html;
  box.hidden = !html;
}

/**
 * Grey out the break buttons whenever breaking isn't possible. Reset always works.
 * Once the server is broken, show the way to step 2.
 */
function updateBreakPanel() {
  const canBreak = !state.broken && !state.building && !state.busy;
  for (const button of document.querySelectorAll("#break-panel button:not(#reset):not(#go-investigate)")) {
    button.disabled = !canBreak;
  }
  document.getElementById("custom-idea").disabled = !canBreak;
  document.getElementById("reset").disabled = state.busy;
  document.getElementById("again").disabled = state.busy;

  document.getElementById("next-investigate").hidden = !state.broken;
  // The fault stays on the server until Reset, so "broken" alone isn't the whole story:
  // after a decision this box says how it ended and leads to the result.
  const incident = state.incident;
  const verdict = incident && incident.verdict;
  let text = "The shop is broken and customers are complaining.";
  let action = incident ? "Back to the agent →" : "Call the agent →";  // start it, or go back to it
  if (verdict === "FIXED" || verdict === "FIXED FOR NOW") {
    text = `${verdict === "FIXED" ? "Fixed." : "Fixed for now."} ${outcome(incident).back}`;
    action = "See the result →";
  } else if (verdict) {
    text = "Customers are still complaining: nothing was fixed.";
    action = "See the result →";
  }
  document.getElementById("next-investigate-text").textContent = text;
  document.getElementById("go-investigate").textContent = action;
  document.getElementById("go-investigate").disabled = state.busy;
}

/**
 * Run one button's request: block the buttons while it's on its way, show the API's
 * message if it fails (with showError, next to that button), and redraw the dashboard afterwards.
 */
async function runAction(action, showError = showBreakMessage) {
  state.busy = true;
  updateBreakPanel();
  try {
    await action();
  } catch (error) {
    // Shown next to the button that was used, so the visitor doesn't have to look for it.
    showError(escapeHtml(error.message), "bad");
  } finally {
    state.busy = false;
    await refresh();
  }
}

/** A preset button was clicked: it breaks the server at once. */
function breakPreset(preset) {
  runAction(async () => {
    await api("POST", `/break/preset/${preset.id}`);
    showBreakMessage(`Done: <strong>${escapeHtml(preset.title)}</strong>. Customers have started complaining (see the alert below).`, "ok");
  });
}

// The timer that follows a fault build. Kept here so Reset can stop it: otherwise it would
// keep asking about a build that reset just threw away, and show a confusing error.
let buildTimer = null;

/** Stop following a fault build. */
function stopWatching() {
  clearInterval(buildTimer);
  buildTimer = null;
  state.building = false;
}

/** Ask every few seconds how Ultra's fault build is going, until it's ready or failed. */
function watchBuild() {
  stopWatching();
  state.building = true;
  updateBreakPanel();
  let shownNote = null;  // only redraw when the note changes, or the spinner would restart every 2 s
  buildTimer = setInterval(async () => {
    let build;
    try {
      build = await api("GET", "/break/status");
    } catch (error) {
      // 404 = the build is gone, e.g. the server restarted or the visitor reset in another tab.
      stopWatching();
      showBreakMessage(escapeHtml(error.message), "bad");
      updateBreakPanel();
      return;
    }
    if (build.state === "building") {
      // e.g. "a different problem is being set up": the visitor sees why it takes longer.
      if (build.note && build.note !== shownNote) {
        shownNote = build.note;
        showBreakMessage(`<span class="spinner"></span>${escapeHtml(build.note)} This can take up to 2 more minutes...`);
      }
      return;
    }
    stopWatching();
    if (build.state === "ready") {
      showBreakMessage("Done. The shop is broken. Read what the customers say below.", "ok");
    } else {
      showBreakMessage(escapeHtml(build.message), "bad");
    }
    await refresh();
  }, BUILD_CHECK_EVERY);
}

/** Show "building" with a spinner, then follow the build. The random idea is never shown. */
function startWatching(build) {
  const what = build.idea ? `Setting up "${escapeHtml(build.idea)}"` : "Picking a problem and setting it up";
  showBreakMessage(`<span class="spinner"></span>${what}. This can take up to 2 minutes...`);
  watchBuild();
}

/** Load the preset buttons once, when the page opens. */
async function loadPresets() {
  const presets = await api("GET", "/presets");
  const box = document.getElementById("presets");
  box.innerHTML = "";
  for (const preset of presets) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = preset.title;
    button.title = preset.alert;  // hover to see what customers will report
    button.addEventListener("click", () => breakPreset(preset));
    box.appendChild(button);
  }
  updateBreakPanel();
}

document.getElementById("custom-form").addEventListener("submit", (event) => {
  event.preventDefault();  // a form would otherwise reload the whole page
  const idea = document.getElementById("custom-idea").value.trim();
  if (!idea) {
    showBreakMessage("Type a problem first, for example: the cache crashed.", "bad");
    return;
  }
  runAction(async () => {
    showBreakMessage(`<span class="spinner"></span>Checking your idea...`);
    // The guard answers at once: a refusal comes back as an error with its reason.
    startWatching(await api("POST", "/break/custom", { idea }));
  });
});

document.getElementById("random").addEventListener("click", () => {
  runAction(async () => startWatching(await api("POST", "/break/random")));
});

/** A fresh healthy server, and back to step 1. Used by "Reset server" and "Try another fault". */
function resetServer() {
  goStep(1);
  runAction(async () => {
    stopWatching();
    clearAgent();
    await api("POST", "/reset");
    document.getElementById("custom-idea").value = "";
    showBreakMessage("The shop is healthy again. Pick another problem.", "ok");
  });
}

document.getElementById("reset").addEventListener("click", resetServer);
document.getElementById("again").addEventListener("click", resetServer);


// ---------- 2. The agent investigates ----------

// The live connection to /incident/stream, and the timer for the "Running 12 s" clock.
let stream = null;
let clockTimer = null;

/** Show a short message under the timeline. kind: "" (neutral), "ok" or "bad". Empty text hides it. */
function showAgentMessage(html, kind = "") {
  const box = document.getElementById("agent-message");
  box.className = `message ${kind}`;
  box.innerHTML = html;
  box.hidden = !html;
}

/** The investigate button only works on a broken server that the agent hasn't looked at yet. */
function updateAgentPanel() {
  document.getElementById("investigate").disabled = !state.broken || state.incident !== null || state.busy;
}

/** Stop listening to the live stream and stop the clock. */
function stopFollowing() {
  if (stream) stream.close();
  stream = null;
  clearInterval(clockTimer);
}

/** Empty the agent, decide and result sections, e.g. after Reset. */
function clearAgent() {
  stopFollowing();
  state.incident = null;
  state.focus = null;
  renderFocus();
  document.getElementById("timeline").innerHTML = "";
  document.getElementById("agent-clock").textContent = "";
  showAgentMessage("");
  document.getElementById("next-decide").hidden = true;
  document.getElementById("decide-panel").hidden = true;
  document.getElementById("result-panel").hidden = true;
  updateAgentPanel();
  updateStepper();
}

// What each badge in the timeline means, shown when the mouse is over it.
// "Guardrail" is the usual name for code that checks an AI's work: no model in that step.
const BADGE_INFO = {
  Nano: "NVIDIA Nemotron 3 Nano: a small, fast model for the first look",
  Super: "NVIDIA Nemotron 3 Super: investigates with tools and web search",
  Ultra: "NVIDIA Nemotron 3 Ultra: the biggest model, makes the final call",
  Guardrail: "Our own code checking the AI's work. No AI model in this step.",
};

/** One step's heading: time, which model (or our code) did it, and a title. */
function stepHead(step, model, title) {
  const info = BADGE_INFO[model] ? ` title="${BADGE_INFO[model]}"` : "";
  const badge = model ? `<span class="model ${model.toLowerCase().replace(" ", "-")}"${info}>${model}</span>` : "";
  return `<div class="step-head"><span class="step-time">${escapeHtml(step.at)}</span>${badge}
          <span class="step-title">${title}</span></div>`;
}

/** Text that can be long (a report, tool output) folded away under a one-line summary. */
function folded(summary, text, open = false) {
  return `<details${open ? " open" : ""}><summary>${summary}</summary><pre>${escapeHtml(text)}</pre></details>`;
}

/** 'get_logs({"container": "api", "lines": 30})' -> 'get_logs(container=api, lines=30)': easier to read. */
function readableCall(text) {
  const match = text.match(/^(\w+)\(([\s\S]*)\)$/);
  if (!match) return text;
  try {
    const args = JSON.parse(match[2] || "{}");
    return `${match[1]}(${Object.entries(args).map(([key, value]) => `${key}=${value}`).join(", ")})`;
  } catch {
    return text;
  }
}

/** Nano's triage arrives as JSON: show it as one readable line. */
function triageText(text) {
  try {
    const t = JSON.parse(text);
    // Nano doesn't always end its guess with a full stop; add one so the two sentences don't run together.
    const guess = String(t.first_guess || "").trim().replace(/([^.!?])$/, "$1.");
    return `Severity <strong>${escapeHtml(t.severity)}</strong>, category <strong>${escapeHtml(t.category)}</strong>. ` +
           `First guess: ${escapeHtml(guess)} Check first: ${escapeHtml(t.check_first)}.`;
  } catch {
    return escapeHtml(text);
  }
}

/**
 * Our code's rule steps in plain words. The agent writes short technical notes (kept as they are
 * for the terminal and the postmortem); a visitor needs to know what happened and why.
 * A rule this list doesn't know is shown as it is.
 */
function ruleView(text) {
  if (text.startsWith("No web search yet")) {
    return { title: "Sent back: search the web first",
             text: "Super wanted to write its report without looking anything up. Our rule: check the error " +
                   "on the web first, so it was sent back once." };
  }
  if (text.startsWith("Empty answer")) {
    return { title: "Asked again: the report was empty",
             text: "Super's answer was empty, so it was asked once more to write its report from what it found." };
  }
  if (text.startsWith("Source links")) {
    return { title: "Source links checked",
             text: "Every link the AI cites must come from a real search result, so a made-up link can't get through.\n" +
                   text.split("\n").slice(1).join("\n") };
  }
  if (text.startsWith("Final call not usable")) {
    const match = text.match(/\((.*)\)/);
    return { title: "Asked again: Ultra's answer couldn't be used",
             text: `Ultra's answer was missing something we need (${match ? match[1] : "it was incomplete"}), ` +
                   "so it was asked once more." };
  }
  if (text.startsWith("Every option was blocked")) {
    return { title: "Asked again: every fix was blocked",
             text: "The safety check stopped all of Ultra's fixes, so Ultra was told why and asked once for safe ones." };
  }
  return { title: "Rule check", text };
}

/** Add one agent step to the timeline, drawn by its kind. */
function addStep(step) {
  const timeline = document.getElementById("timeline");
  const lines = step.text.trim().split("\n");

  // A tool's output goes inside its tool call's entry, folded: the call is what matters at a glance.
  if (step.kind === "tool_result" && timeline.lastElementChild?.classList.contains("tool")) {
    const label = lines.length === 1 ? "Output" : `Output (${lines.length} lines)`;
    timeline.lastElementChild.insertAdjacentHTML("beforeend", folded(label, step.text));
    return;
  }

  const item = document.createElement("li");
  let html;
  switch (step.kind) {
    case "triage":
      html = stepHead(step, "Nano", "First look") + `<div class="step-text">${triageText(step.text)}</div>`;
      break;
    case "thinking":
      html = stepHead(step, "Super", "Thinking") + `<div class="step-text">${escapeHtml(step.text)}</div>`;
      break;
    case "tool_call": {
      item.className = "tool";
      const call = readableCall(step.text);
      state.focus = toolTarget(call);
      renderFocus();
      const title = call.startsWith("search_web(")
        ? `Web search (Tavily): <span class="code">${escapeHtml(call.slice(17, -1))}</span>`
        : `Runs <span class="code">${escapeHtml(call)}</span>`;
      html = stepHead(step, "Super", title);
      break;
    }
    case "report":
      html = stepHead(step, "Super", "Investigation report") + folded("Read the report", step.text);
      break;
    case "final_call":
      // Line 2 is the root cause; the options get their own cards in the next section.
      html = stepHead(step, "Ultra", "Final call") +
             `<div class="step-text">${escapeHtml((lines[1] || "").trim())}</div>` +
             folded("Read Ultra's full answer", step.text);
      break;
    case "safety":
      item.className = step.text.startsWith("BLOCKED") ? "warn" : "ok";
      html = stepHead(step, "Guardrail", "Safety check") + `<div class="step-text">${escapeHtml(step.text)}</div>`;
      break;
    case "rule": {
      item.className = "warn";
      const rule = ruleView(step.text);
      html = stepHead(step, "Guardrail", escapeHtml(rule.title)) + `<div class="step-text">${escapeHtml(rule.text)}</div>`;
      break;
    }
    case "apply":
      item.className = "ok";
      html = stepHead(step, "", "Applying the fix") + `<div class="step-text">${escapeHtml(step.text)}</div>`;
      break;
    case "verdict":
      item.className = lines[0] === "FIXED" ? "ok" : lines[0] === "NOT FIXED" ? "bad" : "warn";
      // Folded: the Result section below shows the same checks in big.
      html = stepHead(step, "", `Verdict: ${escapeHtml(lines[0])}`) + folded("Details", lines.slice(1).join("\n").trim());
      break;
    default:
      html = stepHead(step, "", escapeHtml(step.kind)) + `<div class="step-text">${escapeHtml(step.text)}</div>`;
  }
  item.innerHTML = html;
  timeline.appendChild(item);
}

/** The agent finished: keep its results for the next section and show the cost. */
function finishIncident(view) {
  stopFollowing();
  state.incident = view;
  state.focus = null;  // the agent has stopped looking around
  renderFocus();
  document.getElementById("agent-clock").textContent = view.cost || "";
  if (view.state === "failed") {
    showAgentMessage(`The agent stopped. ${escapeHtml(view.error)} To run it again: Reset server, then break it again.`, "bad");
  }
  // The way to the next step: choose a fix, or after a decision, see how it went.
  const next = document.getElementById("next-decide");
  next.hidden = view.state !== "done";
  document.getElementById("next-decide-text").textContent = view.decision
    ? "You've already picked a fix." : "The agent is done. It found the cause and has fixes for you.";
  document.getElementById("go-decide").textContent = view.decision ? "See the result →" : "See the fixes →";
  updateAgentPanel();
  updateBreakPanel();
  if (view.state === "done") renderDecision(view);
  updateStepper();
}

/**
 * Show the agent's work. A finished incident is drawn at once from GET /incident; a running one
 * is followed live over /incident/stream, which first sends every step so far, then each new one.
 */
async function followIncident() {
  stopFollowing();
  let view;
  try {
    view = await api("GET", "/incident");
  } catch {
    // 404: no investigation on this server (or the server restarted and forgot it).
    clearAgent();
    return;
  }
  state.incident = view;
  updateAgentPanel();
  updateStepper();
  document.getElementById("timeline").innerHTML = "";
  document.getElementById("next-decide").hidden = true;
  showAgentMessage("");

  if (view.state !== "running") {
    view.steps.forEach(addStep);
    finishIncident(view);
    return;
  }

  const started = new Date(view.started);
  const clock = document.getElementById("agent-clock");
  clockTimer = setInterval(() => {
    clock.textContent = `Working... ${Math.round((Date.now() - started) / 1000)} s`;
  }, 1000);

  stream = new EventSource("/incident/stream");
  stream.addEventListener("step", (event) => addStep(JSON.parse(event.data)));
  stream.addEventListener("done", (event) => finishIncident(JSON.parse(event.data)));
  stream.addEventListener("failed", (event) => finishIncident(JSON.parse(event.data)));
  stream.addEventListener("reset", () => clearAgent());
  stream.addEventListener("timeout", () => {
    stopFollowing();
    showAgentMessage("The agent is taking unusually long. Reload the page to check on it.", "bad");
  });
  // The connection broke (network, server restart). The browser would reconnect by itself and
  // get every step again, drawn twice. So close it, and start over cleanly in a moment.
  stream.onerror = () => {
    stopFollowing();
    setTimeout(followIncident, 2000);
  };
}

/** Go to step 2 and start the agent there, so the visitor watches it from the first step. */
function startInvestigation() {
  goStep(2);
  runAction(async () => {
    await api("POST", "/investigate");
    await followIncident();
  }, showAgentMessage);
}

document.getElementById("investigate").addEventListener("click", startInvestigation);
document.getElementById("go-investigate").addEventListener("click", () => {
  if (state.incident && state.incident.verdict) goStep(4);  // decided: show how it ended
  else if (state.incident) goStep(2);
  else startInvestigation();
});
document.getElementById("go-decide").addEventListener("click", () => {
  goStep(state.incident && state.incident.decision ? 4 : 3);
});


// ---------- 3. You decide, 4. Result ----------

/** One fix option as a card. A blocked option is greyed out with the safety check's reason, no button. */
function optionCard(option, chosenNumber) {
  const tags = [
    option.recommended ? `<span class="tag accent">Recommended</span>` : "",
    option.can_be_undone ? `<span class="tag ok">Can be undone</span>` : `<span class="tag bad">Can't be undone</span>`,
    `<span class="tag ${option.risk === "low" ? "ok" : option.risk === "high" ? "bad" : "warn"}">Risk: ${escapeHtml(option.risk)}</span>`,
  ].join("");
  const list = (items) => items.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  const chosen = chosenNumber === option.number;
  let button = `<button type="button" class="approve" data-number="${option.number}">Approve option ${option.number}</button>`;
  if (chosenNumber !== null) button = chosen ? `<span class="tag ok">Approved</span>` : "";

  return `
    <div class="option ${option.recommended ? "recommended" : ""} ${chosen ? "chosen" : ""}">
      <h4>Option ${option.number}: ${escapeHtml(option.title)}</h4>
      <div class="tags">${tags}</div>
      <div class="label">Steps</div>
      <ol class="steps">${list(option.steps)}</ol>
      <ul class="pros">${list(option.pros)}</ul>
      <ul class="cons">${list(option.cons)}</ul>
      <div class="muted">Undo: ${escapeHtml(option.undo)}</div>
      ${button}
    </div>`;
}

/** An option the safety check stopped: shown so the visitor sees what the AI wanted and why it was refused. */
function blockedCard(item) {
  return `
    <div class="option blocked">
      <h4>${escapeHtml(item.title)}</h4>
      <div class="tags"><span class="tag bad">Blocked by the safety check</span></div>
      <div class="muted">${escapeHtml(item.reason)}</div>
    </div>`;
}

/**
 * Draw sections 3 and 4 from the incident: the option cards, and after a decision the verdict.
 * The options come from "final_call" in the API's answer (number, title, steps, pros, cons, risk...).
 */
function renderDecision(view) {
  const panel = document.getElementById("decide-panel");
  const final = view.final_call;
  panel.hidden = false;

  // "approved option 2: ..." -> 2. A rejection (or no decision yet) has no number.
  const match = /approved option (\d+)/.exec(view.decision || "");
  const chosen = view.decision ? (match ? Number(match[1]) : 0) : null;

  if (!final) {
    document.getElementById("root-cause").innerHTML =
      "Ultra's answer couldn't be read, so there are no fix options. Reject, and fix it by hand.";
    document.getElementById("options").innerHTML = "";
  } else {
    document.getElementById("root-cause").innerHTML = `
      <strong>Root cause</strong> <span class="muted">(${escapeHtml(final.confidence)} confidence)</span>
      <p>${escapeHtml(final.root_cause)}</p>
      <p class="muted">${escapeHtml(final.why_recommended || "")}</p>`;
    document.getElementById("options").innerHTML =
      final.options.map((option) => optionCard(option, chosen)).join("") + final.blocked.map(blockedCard).join("");
  }

  const reject = document.getElementById("reject");
  reject.classList.remove("armed");
  reject.hidden = chosen !== null && chosen !== 0;
  reject.disabled = chosen !== null;
  reject.textContent = chosen === 0 ? "Rejected: nothing was changed" : "None of these, leave it as it is";

  for (const button of document.querySelectorAll("#options .approve")) {
    button.addEventListener("click", () => confirmThen(button, Number(button.dataset.number)));
  }
  if (view.verdict) renderResult(view);
}

/**
 * Approving takes two clicks, like the terminal's second "Apply these steps? (y/n)":
 * the first click asks, the second (within 5 seconds) sends the decision.
 */
function confirmThen(button, number) {
  if (button.classList.contains("armed")) {
    decide(number);
    return;
  }
  const original = button.textContent;
  button.classList.add("armed");
  button.textContent = number === 0 ? "Click again to reject all" : "Click again to apply these steps";
  setTimeout(() => {
    // Only if it's still waiting: after a decision the button already shows something new.
    if (!button.classList.contains("armed")) return;
    button.classList.remove("armed");
    button.textContent = original;
  }, 5000);
}

/** Send the decision. The answer has the verdict first, the whole incident, and the server after the fix. */
function decide(number) {
  runAction(async () => {
    const view = await api("POST", `/decide/${number}`);
    state.incident = view;
    // Redraw the timeline so the new "apply" and "verdict" steps are in it too.
    document.getElementById("timeline").innerHTML = "";
    view.steps.forEach(addStep);
    showAgentMessage("");
    renderDecision(view);
    // The cards turn green right away, no need to wait for the next refresh. The server part of
    // this answer has no alert of its own (only /status adds it), so it's taken from the incident.
    renderStatus({ alert: view.alert, ...view.server });
    finishIncident(view);  // the "next" button in step 2 now says "See the result"
    goStep(4);
  }, showAgentMessage);
}

// A request and its answer code in a log line, in our api's style ("POST /api/orders 201")
// and in nginx's ('"GET /products HTTP/1.1" 200'). The same pattern as REQUEST in validator.py.
const REQUEST = /\b(GET|POST|PUT|PATCH|DELETE)\s+(\/[^\s"]*)(?:\s+HTTP\/[\d.]+")?\s+(\d{3})/;

// What the shop's addresses are for, in a customer's words. The "Fixed" message is built from the
// server's own recovery logs, so it names what really works again in THIS incident. An address
// that isn't listed (a custom fault can invent one) is shown as it is, e.g. "GET /api/search".
const PAGE_NAMES = {
  "/api/orders": "placing orders",
  "/api/checkout": "checkout",
  "/api/cart": "the shopping cart",
  "/api/products": "product pages",
  "/products": "product pages",
  "/": "the home page",
};

/** "api: INFO POST /api/orders 201 in 58ms" -> {container: "api", method, path, code, success}, or null. */
function parseRequest(line) {
  const match = line.match(REQUEST);
  if (!match) return null;
  const [, method, path, code] = match;
  // 2xx = the request worked (200 OK, 201 Created...).
  return { container: line.split(":")[0], method, path, code, success: code.startsWith("2") };
}

/** ["a"] -> "a", ["a", "b"] -> "a and b", ["a", "b", "c"] -> "a, b and c". */
function joinWords(words) {
  return words.length < 2 ? words.join("") : `${words.slice(0, -1).join(", ")} and ${words.at(-1)}`;
}

/**
 * What the fix changed for the customers, in words, built from this incident's own data
 * (nothing here is the same sentence for every fault):
 *   back:  what works again, from the recovery logs (or the server checks that passed, if the logs have no request)
 *   kept:  for FIXED, why it stays fixed (the checks that passed, e.g. "job db-backup is off")
 *   still: for FIXED FOR NOW, what's still wrong (the checks that failed), so it will come back
 *   watch: side effects to keep an eye on, e.g. another disk nearly full after moving files there
 */
function outcome(view) {
  const checks = view.checks || { users_ok: [], stays_fixed: [], warnings: [] };
  // The check lines read "OK  text" or "NO  text"; a line in brackets says how they combine.
  const lines = (list, start) => list.filter((line) => line.startsWith(start)).map((line) => line.slice(3).trim());
  const pages = [];
  for (const line of view.proof || []) {
    const request = parseRequest(line);
    if (!request || !request.success) continue;
    const name = PAGE_NAMES[request.path] || `${request.method} ${request.path}`;
    if (!pages.includes(name)) pages.push(name);
  }
  const passed = lines(checks.users_ok, "OK");
  let back = "";
  if (pages.length) back = `Working again: ${joinWords(pages)}.`;
  else if (passed.length) back = `Checked on the server: ${joinWords(passed)}.`;
  // "/ is 90% full now" -> "the main disk (/) is 90% full now": the same words as the Disks list.
  const warnings = checks.warnings.map((text) => {
    const mount = text.split(" ")[0];
    return DISK_NAMES[mount] ? `the ${DISK_NAMES[mount]} (${mount})${text.slice(mount.length)}` : text;
  });
  return {
    back,
    kept: view.verdict === "FIXED" ? `Stays fixed: ${joinWords(lines(checks.stays_fixed, "OK"))}.` : "",
    still: view.verdict === "FIXED FOR NOW" ? `The cause is still there: ${joinWords(lines(checks.stays_fixed, "NO"))}.` : "",
    watch: warnings.length ? `Watch out: ${joinWords(warnings)}.` : "",
  };
}

/** The green banner after a fix that worked: what works again, and anything left to watch. */
function resolvedText(view) {
  const result = outcome(view);
  const extra = [result.still, result.watch].filter(Boolean)
    .map((text) => `<span class="detail warn">${escapeHtml(text)}</span>`).join("");
  return `<strong>${view.verdict === "FIXED" ? "Fixed." : "Fixed for now."}</strong> ${escapeHtml(result.back)}${extra}`;
}

/** The big verdict and the checks behind it, from the last "verdict" step. */
function renderResult(view) {
  const verdictStep = [...view.steps].reverse().find((step) => step.kind === "verdict");
  const level = view.verdict === "FIXED" ? "ok" : view.verdict === "NOT FIXED" ? "bad" : "warn";
  const result = outcome(view);
  // Not fixed: the customers' own complaint, which is still true. Otherwise what changed for them.
  const meaning = view.verdict === "NOT FIXED"
    ? `Customers still report: "${view.alert}"`
    : [result.back, result.kept, result.still].filter(Boolean).join(" ");
  const box = document.getElementById("verdict");
  box.className = `verdict ${level}`;
  box.innerHTML = `${escapeHtml(view.verdict)}<small>${escapeHtml(meaning)}</small>` +
                  `<small>Decision: ${escapeHtml(view.decision || "")}</small>`;
  // The checks behind the verdict, folded (the sentences above say the same in words),
  // without the "Decision:" line already shown above.
  const details = verdictStep
    ? verdictStep.text.split("\n").slice(1).filter((line) => !line.startsWith("Decision:")).join("\n").trim()
    : "";
  document.getElementById("verdict-details").innerHTML = details ? folded("How the server checked it", details) : "";
  renderProof(view.proof || []);
  document.getElementById("result-panel").hidden = false;
}

/**
 * "api: INFO POST /api/orders 201 in 58ms" -> "api: POST /api/orders answered 201 (success)".
 * Only a request line is reworded; other log lines are shown as they are.
 */
function proofLine(line) {
  const request = parseRequest(line);
  if (!request) return { success: false, text: line };
  const { container, method, path, code, success } = request;
  return { success, text: `${container}: ${method} ${path} answered ${code}${success ? " (success)" : ""}` };
}

/** The log lines that show the fix worked, e.g. a new order going through. Hidden when there are none. */
function renderProof(lines) {
  const box = document.getElementById("proof");
  box.hidden = lines.length === 0;
  const items = lines.map(proofLine)
    .sort((a, b) => b.success - a.success)  // the requests that worked first: they're what customers feel
    .map((line) => `<li class="${line.success ? "success" : ""}">${escapeHtml(line.text)}</li>`);
  box.innerHTML = `<div class="label">Proof from the server: what the logs show now</div><ul>${items.join("")}</ul>`;
}

document.getElementById("reject").addEventListener("click", (event) => confirmThen(event.currentTarget, 0));


// ---------- The four steps (tabs) ----------

/** How far this visitor has got: a step can only be opened once everything before it is done. */
function reachedStep() {
  const incident = state.incident;
  if (incident && incident.verdict) return 4;           // decided: the result is there
  if (incident && incident.state === "done") return 3;  // the agent has fix options
  if (incident || state.broken) return 2;               // broken: the agent can be called
  return 1;
}

/**
 * Draw the step tabs: done ones get a tick, the open one is highlighted, the next one glows
 * when it's ready (e.g. the agent finished while the visitor watched), later ones are locked.
 * If the visitor is on a step that's no longer possible (after Reset), go back.
 */
function updateStepper() {
  const reached = reachedStep();
  if (state.view > reached) goStep(reached);
  for (const tab of document.querySelectorAll("#stepper button")) {
    const step = Number(tab.dataset.step);
    tab.disabled = step > reached;
    tab.classList.toggle("active", step === state.view);
    tab.classList.toggle("done", step < reached);
    tab.classList.toggle("ready", step === reached && step > state.view);
    tab.querySelector(".num").textContent = step < reached ? "✓" : step;
  }
}

// The same width as the phone layout in style.css, where the step tabs sit at the bottom of the screen.
const PHONE = matchMedia("(max-width: 640px)");

/**
 * Show one step and bring it into view, just under the header bar.
 * On a laptop only when the visitor has scrolled past the steps (the tabs stay in sight anyway);
 * on a phone always, like switching tabs in an app. scroll=false for the first load, so a new
 * visitor still sees the intro at the top.
 */
function goStep(step, scroll = true) {
  state.view = step;
  const main = document.getElementById("main");
  main.dataset.step = step;
  main.classList.remove("show-server");  // the full server folds back into the strip
  document.getElementById("strip").setAttribute("aria-expanded", "false");
  const toggle = document.querySelector("#strip .strip-toggle");
  if (toggle) toggle.textContent = "Show server ▾";
  updateStepper();
  if (!scroll) return;
  // Where <main> starts on the page, minus the header bar (58 px) and a little room.
  const top = main.getBoundingClientRect().top + window.scrollY - 66;
  if (PHONE.matches || window.scrollY > top) window.scrollTo({ top, behavior: "smooth" });
}

for (const tab of document.querySelectorAll("#stepper button")) {
  tab.addEventListener("click", () => goStep(Number(tab.dataset.step)));
}

// The status strip: a click shows or hides the full server under it.
document.getElementById("strip").addEventListener("click", (event) => {
  const open = document.getElementById("main").classList.toggle("show-server");
  event.currentTarget.setAttribute("aria-expanded", String(open));
  event.currentTarget.querySelector(".strip-toggle").textContent = open ? "Hide server ▴" : "Show server ▾";
});


// ---------- Light / dark theme ----------

/**
 * The sun/moon button. index.html already picked the theme before the page was drawn;
 * a click switches it and remembers the choice in this browser. Until the visitor clicks,
 * the page follows the computer's own setting, even if that changes while the page is open.
 */
function setupThemeSwitch() {
  const root = document.documentElement;
  const button = document.getElementById("theme");
  const label = () => {
    const next = root.dataset.theme === "dark" ? "light" : "dark";
    button.title = `Switch to the ${next} theme`;
    button.setAttribute("aria-label", button.title);  // what a screen reader says for the icon
  };
  label();
  button.addEventListener("click", () => {
    root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
    try { localStorage.setItem("theme", root.dataset.theme); } catch { /* private window: just not remembered */ }
    label();
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (event) => {
    let saved = null;
    try { saved = localStorage.getItem("theme"); } catch { /* nothing saved */ }
    if (saved) return;  // the visitor chose one themselves: keep it
    root.dataset.theme = event.matches ? "dark" : "light";
    label();
  });
}


// ---------- Start ----------

/** When the page opens: buttons, the dashboard, and anything still going on from before a reload. */
async function start() {
  setupThemeSwitch();
  await loadPresets().catch((error) => showBreakMessage(escapeHtml(error.message), "bad"));
  // The incident first: the dashboard needs its verdict to show a fixed alert as resolved.
  await followIncident();
  await refresh();
  // After a reload, open the furthest step this visitor has reached.
  goStep(reachedStep(), false);
  setInterval(refresh, REFRESH_EVERY);
  try {
    const build = await api("GET", "/break/status");
    if (build.state === "building") startWatching(build);
  } catch {
    // 404: no custom or random fault was started. Nothing to pick up.
  }
}

start();
