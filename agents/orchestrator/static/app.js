// Chat UI for the orchestrator agent. Reads the SSE stream from /api/chat and renders
// tokens, tool steps, the agent's plan and every call that went through APIM.

const $ = (id) => document.getElementById(id);
const messagesEl = $("messages");
const input = $("input");
const sendBtn = $("send");
const callsEl = $("calls");

let conversationId = null;
let busy = false;
const counts = { model: 0, mcp: 0, a2a: 0, blocked: 0 };
let maxBudget = 0;

marked.setOptions({ gfm: true, breaks: false });
const md = (text) => DOMPurify.sanitize(marked.parse(text || ""), { ADD_ATTR: ["target"] });

const KIND_LABEL = { model: "Model", mcp: "MCP", a2a: "A2A", other: "HTTP" };
const VIA_LABEL = { mcp: "MCP via APIM", a2a: "A2A via APIM", local: "built-in" };

fetch("/api/info").then((r) => r.json()).then((info) => {
  $("model-chip").textContent = `${info.model} · via APIM`;
}).catch(() => {});

// ---------- composer ----------
input.addEventListener("input", autosize);
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("composer").requestSubmit(); }
});
$("composer").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (text && !busy) send(text);
});
document.querySelectorAll(".suggestion").forEach((b) => b.addEventListener("click", () => !busy && send(b.dataset.prompt)));
$("new-chat").addEventListener("click", () => {
  if (busy) return;
  conversationId = null;
  messagesEl.querySelectorAll(".msg").forEach((m) => m.remove());
  $("welcome").hidden = false;
});
$("toggle-panel").addEventListener("click", () => $("panel").classList.toggle("open"));

// ---------- theme: follows the system until the toggle is used, then remembered per browser ----------
$("theme-toggle").addEventListener("click", () => {
  const current = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  const next = current === "light" ? "dark" : "light";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("theme", next); } catch {}
});

// ---------- views: chat or gateway map (the composer works in both) ----------
let view = "chat";
document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => showView(tab.dataset.view)));
function showView(name) {
  view = name;
  document.querySelectorAll(".tab").forEach((t) => {
    t.classList.toggle("active", t.dataset.view === name);
    t.setAttribute("aria-selected", String(t.dataset.view === name));
  });
  messagesEl.hidden = name !== "chat";
  document.body.classList.toggle("map-view", name === "map");
  $("panel").classList.remove("open");
  $("gmap").hidden = name !== "map";
  if (name === "map") GatewayMap.show();
  else scrollDown();
}

// ---------- policy knobs (APIM named values, changed through the gateway admin identity) ----------
const KNOB_TAG = { Model: "model", MCP: "mcp", A2A: "a2a" };
let knobList = [];

function knobNote(text, isError = false) {
  $("knobs-note").textContent = text;
  $("knobs-note").classList.toggle("err", isError);
}

async function loadKnobs() {
  try {
    const res = await fetch("/api/knobs");
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.statusText);
    if (!data.enabled) return;
    knobList = data.knobs;
    $("knobs").hidden = false;
    renderKnobs();
  } catch (err) {
    $("knobs").hidden = false;
    knobNote(`Knobs unavailable: ${err.message}`, true);
  }
}

function renderKnobs() {
  const groups = $("knob-groups");
  groups.innerHTML = "";
  for (const group of [...new Set(knobList.map((k) => k.group))]) {
    const box = el("div", "knob-group", `<span class="tag ${KNOB_TAG[group] || ""}">${esc(group)}</span>`);
    for (const knob of knobList.filter((k) => k.group === group)) box.append(knobRow(knob));
    groups.append(box);
  }
}

function knobRow(knob) {
  const row = el("div", "knob" + (knob.value !== knob.default ? " changed" : ""));
  row.innerHTML = `<div class="knob-head"><span>${esc(knob.label)}</span><span class="v" title="${esc(knob.name)}">${esc(knob.value ?? "missing")}</span></div>`;
  const ctl = el("div", "knob-ctl");
  if (knob.kind === "number") {
    for (const preset of knob.presets) {
      const b = el("button", "opt" + (preset === knob.value ? " on" : ""), Number(preset).toLocaleString());
      b.type = "button";
      b.title = preset === knob.default ? "default" : "";
      b.addEventListener("click", () => setKnob(knob, preset, row));
      ctl.append(b);
    }
    const custom = el("input");
    custom.type = "number"; custom.min = "1"; custom.placeholder = "custom";
    custom.addEventListener("keydown", (e) => { if (e.key === "Enter" && custom.value) setKnob(knob, custom.value, row); });
    ctl.append(custom);
  } else {
    // Allow-lists show who may pass; block-lists show what is refused.
    const current = new Set((knob.value || "").split(",").filter((v) => v && v !== "none"));
    const deny = knob.name.includes("blocked");
    for (const option of knob.options) {
      const b = el("button", "opt" + (current.has(option) ? " on" : "") + (deny ? " deny" : ""), esc(option));
      b.type = "button";
      b.addEventListener("click", () => {
        const next = new Set(current);
        next.has(option) ? next.delete(option) : next.add(option);
        setKnob(knob, [...next].join(",") || "none", row);
      });
      ctl.append(b);
    }
  }
  row.append(ctl);
  return row;
}

async function setKnob(knob, value, row) {
  row.classList.add("busy");
  knobNote(`Applying ${knob.name} = ${value}…`);
  try {
    const res = await fetch(`/api/knobs/${encodeURIComponent(knob.name)}`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ value }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.statusText);
    knob.value = data.value;
    knobNote(`${knob.name} = ${data.value} · policies pick it up within seconds`);
    window.dispatchEvent(new CustomEvent("knobs-changed", { detail: knob }));
  } catch (err) {
    knobNote(err.message, true);
  } finally {
    row.classList.remove("busy");
  }
  renderKnobs();
}

$("knobs-reset").addEventListener("click", async (e) => {
  e.preventDefault();  // a button inside <summary> would also toggle the section
  const changed = knobList.filter((k) => k.value !== k.default);
  knobNote(changed.length ? "Resetting…" : "All knobs are at their defaults");
  for (const knob of changed) {
    await setKnob(knob, knob.default, $("knob-groups"));
  }
});

loadKnobs();

function autosize() {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 200) + "px";
}

// ---------- rendering ----------
function el(tag, cls, html) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (html !== undefined) node.innerHTML = html;
  return node;
}
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const scrollDown = () => { messagesEl.scrollTop = messagesEl.scrollHeight; };

function addUser(text) {
  const msg = el("div", "msg user");
  msg.append(el("div", "avatar", "You"));
  const bubble = el("div", "bubble");
  bubble.append(el("div", "content", esc(text)));
  msg.append(bubble);
  messagesEl.append(msg);
}

function addBot() {
  const msg = el("div", "msg bot");
  msg.append(el("div", "avatar", "AI"));
  const bubble = el("div", "bubble");
  const plan = el("div", "plan"); plan.hidden = true;
  const steps = el("div", "steps");
  const content = el("div", "content cursor");
  bubble.append(plan, steps, content);
  msg.append(bubble);
  messagesEl.append(msg);
  return { plan, steps, content, text: "", stepById: {} };
}

function renderPlan(view, todos) {
  if (!todos?.length) return;
  view.plan.hidden = false;
  view.plan.innerHTML = "<b>Plan</b><ul>" +
    todos.map((t) => `<li class="${esc(t.status)}">${esc(t.content)}</li>`).join("") + "</ul>";
}

function addStep(view, call) {
  if (call.name === "write_todos") return; // shown as the plan instead
  const step = el("details", "step");
  const args = JSON.stringify(call.args, null, 2);
  step.innerHTML = `<summary><span class="tag ${call.via}">${VIA_LABEL[call.via]}</span>
    <span class="name">${esc(call.name)}</span>
    <span class="state"><span class="spinner"></span>running</span></summary><pre>${esc(args)}</pre>`;
  view.steps.append(step);
  view.stepById[call.id] = step;
}

function finishStep(view, result) {
  const step = view.stepById[result.id];
  if (!step) return;
  const ok = result.status !== "error";
  step.querySelector(".state").innerHTML = ok ? '<span class="ok">✓ done</span>' : '<span class="bad">✕ failed</span>';
  if (result.preview) step.append(el("pre", "", esc(result.preview) + (result.preview.length >= 400 ? "…" : "")));
}

// Steps still running when the turn ends (for example after an error) will never finish.
function stopSteps(view) {
  for (const step of Object.values(view.stepById)) {
    const state = step.querySelector(".state");
    if (state.querySelector(".spinner")) state.innerHTML = '<span class="bad">✕ stopped</span>';
  }
}

function addCall(call) {
  callsEl.querySelector(".empty")?.remove();
  // MCP streamable HTTP: the client probes GET (optional SSE stream) and sends DELETE (end session);
  // stateless servers such as Microsoft Learn answer 405. That is protocol housekeeping, not a block.
  const housekeeping = call.kind === "mcp" && call.status === 405 && (call.method === "GET" || call.method === "DELETE");
  // A blocked MCP tool comes back as 200 with an MCP tool error; APIM flags it with x-apim-blocked.
  const blocked = (call.status >= 400 && !housekeeping) || Boolean(call.blocked);
  if (counts[call.kind] !== undefined) counts[call.kind]++;
  if (blocked) counts.blocked++;
  for (const k of Object.keys(counts)) $(`stat-${k}`).textContent = counts[k];

  const item = el("li", "call" + (blocked ? " blocked" : "") + (housekeeping ? " housekeeping" : ""));
  const meta = [`<span><b>${call.ms}</b> ms</span>`];
  if (housekeeping) meta.unshift(`<span>${call.method === "GET" ? "no server stream" : "session close"} (stateless server)</span>`);
  if (call.caller) meta.push(`<span>caller <b>${esc(call.caller)}</b></span>`);
  if (call.tokensConsumed) meta.push(`<span>tokens <b>${esc(call.tokensConsumed)}</b></span>`);
  if (call.remainingTokens) meta.push(`<span>budget left <b>${esc(call.remainingTokens)}</b></span>`);
  if (call.blocked) meta.push(`<span>blocked <b>${esc(call.blocked)}</b></span>`);
  if (call.reason) meta.push(`<span title="${esc(call.reason)}">${esc(call.reason)}</span>`);
  if (call.requestId) meta.push(`<span title="APIM request id">id <b>${esc(call.requestId.slice(0, 8))}</b></span>`);
  item.innerHTML = `<div class="call-top"><span class="tag ${call.kind}">${KIND_LABEL[call.kind] || call.kind}</span>
      <span class="path" title="${esc(call.path)}">${esc(call.method)} ${esc(call.path)}</span>
      <span class="status s${housekeeping ? "h" : String(call.status)[0]}">${call.status}</span></div>
    <div class="call-meta">${meta.join("")}</div>`;
  callsEl.prepend(item);

  if (call.remainingTokens) {
    const left = Number(call.remainingTokens);
    maxBudget = Math.max(maxBudget, left + Number(call.tokensConsumed || 0));
    $("budget").hidden = false;
    $("budget-value").textContent = left.toLocaleString();
    $("budget-fill").style.width = `${Math.max(2, (left / maxBudget) * 100)}%`;
  }
}

// ---------- streaming ----------
async function send(text) {
  busy = true; sendBtn.disabled = true;
  $("tab-chat").classList.add("busy");
  $("welcome").hidden = true;
  input.value = ""; autosize();
  addUser(text);
  const view = addBot();
  scrollDown();

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, conversation_id: conversationId }),
    });
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf("\n\n")) >= 0) {
        handle(view, buffer.slice(0, idx));
        buffer = buffer.slice(idx + 2);
      }
    }
  } catch (err) {
    view.content.insertAdjacentHTML("beforeend", `<div class="error">Connection lost: ${esc(err.message)}</div>`);
  } finally {
    stopSteps(view);
    GatewayMap.reset();
    view.content.classList.remove("cursor");
    if (!view.text && !view.content.querySelector(".error")) view.content.innerHTML = '<p style="color:var(--muted)">No answer produced.</p>';
    busy = false; sendBtn.disabled = false; input.focus();
    $("tab-chat").classList.remove("busy");
  }
}

function handle(view, raw) {
  const event = /^event: (.*)$/m.exec(raw)?.[1];
  const data = JSON.parse(/^data: (.*)$/m.exec(raw)?.[1] || "{}");
  switch (event) {
    case "start": conversationId = data.conversation_id; break;
    case "token":
      view.text += data.text;
      view.content.innerHTML = md(view.text);
      view.content.querySelectorAll("a").forEach((a) => { a.target = "_blank"; a.rel = "noopener"; });
      break;
    case "tool": addStep(view, data); break;
    case "result": finishStep(view, data); break;
    case "todos": renderPlan(view, data.todos); break;
    case "gateway-start": GatewayMap.callStart(data); break;
    case "gateway": addCall(data); GatewayMap.callEnd(data); break;
    case "error":
      view.content.insertAdjacentHTML("beforeend", `<div class="error"><b>${data.status ? "HTTP " + data.status + " · " : ""}Gateway/agent error</b><br>${esc(data.message)}</div>`);
      break;
  }
  scrollDown();
}
