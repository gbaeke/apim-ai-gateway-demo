// Gateway map: how API Management governs this app, read live from APIM (/api/gateway/map).
// Left to right: callers -> global policy -> one lane per API -> backends. Click a step to see the
// policy behind it; calls the agent makes light up their route while they are in flight.

const GatewayMap = (() => {
  const root = document.getElementById("gmap");
  const LANE_LABEL = { model: "Model", mcp: "MCP", a2a: "A2A" };
  const STATE_ICON = { pass: "✓", limit: "◔", deny: "✕", block: "⊘" };
  const SECTION_LABEL = { inbound: "", backend: "backend", outbound: "response", "on-error": "on error" };

  const ICONS = {
    agent: '<path d="M12 3v3M8 21h8M7 10h10a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2zM9.5 14.5h.01M14.5 14.5h.01"/><circle cx="12" cy="6" r="1.5"/>',
    unknown: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6V14M12 17h.01"/>',
    apim: '<path d="M12 2 3 7v10l9 5 9-5V7z"/><path d="M3 7l9 5 9-5M12 12v10"/>',
    model: '<path d="M12 3l2.4 4.9 5.4.8-3.9 3.8.9 5.4L12 15.4 7.2 17.9l.9-5.4L4.2 8.7l5.4-.8z"/>',
    mcp: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5z"/><path d="M4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5"/>',
    a2a: '<circle cx="9" cy="8" r="3"/><path d="M3 20a6 6 0 0 1 12 0M16 4a3 3 0 0 1 0 6M21 20a6 6 0 0 0-4-5.6"/>',
    "validate-token": '<path d="M12 3l7 3v5c0 5-3.5 8.5-7 10-3.5-1.5-7-5-7-10V6z"/><path d="M9 12l2 2 4-4"/>',
    "identify-caller": '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="12" r="2.5"/><path d="M14 10h4M14 14h3"/>',
    "token-limit": '<path d="M12 14l4-4"/><path d="M3.3 17a9 9 0 1 1 17.4 0"/>',
    "rate-limit": '<circle cx="12" cy="13" r="8"/><path d="M12 9v4l2 2M10 2h4"/>',
    "caller-gate": '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    "tool-block": '<circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/>',
    "strip-token": '<path d="M20 12H4M14 6l6 6-6 6"/>',
    "gateway-key": '<circle cx="7.5" cy="15.5" r="4.5"/><path d="M10.7 12.3L21 2M17 6l3 3M15 8l2 2"/>',
    "apim-identity": '<path d="M12 3l7 3v5c0 5-3.5 8.5-7 10-3.5-1.5-7-5-7-10V6z"/><circle cx="12" cy="10" r="2.5"/><path d="M8.5 16a4 4 0 0 1 7 0"/>',
    "token-metric": '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    step: '<circle cx="12" cy="12" r="3"/>',
  };
  const icon = (name, size = 16) =>
    `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ICONS.step}</svg>`;

  const EXPLAIN = {
    "validate-token": "Every call must carry an Entra ID token from your tenant, issued to one of the approved agent identities, for the audience shown. Anything else is rejected with 401 before an API policy runs, so a stolen key or a developer's own login gets nowhere.",
    "identify-caller": "Maps the token's client id to a friendly agent name. Every later step (access, budgets, metrics and logs) keys on this name, so adding an agent means one new identity, not new keys.",
    variable: "Reads the application id from the validated token.",
    trace: "Writes the caller into the gateway trace, so the logs show which agent made each call.",
    forward: "Sends the request on to the backend of the API that was called.",
    "stamp-headers": "Adds x-apim-caller and x-apim-request-id to every response, errors included. The agent, and this app's activity panel, can see who APIM thinks called and look the request up in the gateway logs.",
    base: "Runs the global policy, the identity checks shared by all APIs, at this point.",
    "token-limit": "Each agent gets its own tokens-per-minute budget, counted from the usage the model reports. Over budget, APIM answers 429 with a retry time and the model is never called. Callers without a budget are refused with 403.",
    "token-metric": "Emits token usage as a custom metric with the API and the calling agent as dimensions: chargeback and dashboards per agent.",
    route: "Points model traffic at the Foundry backend.",
    "apim-identity": "APIM requests a token for Foundry with its own managed identity. The agents have no rights on the Foundry account at all, so their tokens are useless anywhere but at the gateway.",
    "swap-token": "Replaces the agent's token with APIM's own, so the agent's credential never reaches the backend.",
    "caller-gate": "Only the agents in this list may use the API. Anyone else gets 403 at the gateway and the request never reaches the backend. Change the list live with the knob below.",
    "read-tool": "Reads the JSON-RPC body and remembers which tool a tools/call request asks for.",
    "tool-block": "Blocked tools are answered by APIM itself with an MCP tool error (HTTP 200, isError). The agent sees a failed tool call and carries on without it; the MCP server never sees the request.",
    "rate-limit": "Caps the number of requests per agent per window, independently of tokens. Over the cap, APIM answers 429. One MCP tool call is about five HTTP requests.",
    "strip-token": "Removes the agent's bearer token before forwarding. The backend doesn't need it, and the credential never leaves the gateway.",
    "gateway-key": "Adds a shared secret that the specialist checks, so it only accepts traffic that came through APIM. The value is a secret named value and is never shown.",
    header: "Passes the calling agent's name to the backend, so the specialist knows who delegated the task.",
    condition: "A conditional block in the policy.",
    other: "A policy step.",
  };

  let map = null;
  let viewAs = "orchestrator-agent";
  let allSteps = false;
  let open = null; // { scope, step }
  let loadedAt = 0;
  let loading = null;
  const inflight = new Map(); // callId -> kind, for calls that left the agent and have no answer yet
  const liveCount = (kind) => [...inflight.values()].filter((k) => !kind || k === kind).length;

  const visible = () => !root.hidden;
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const short = (name) => name.replace(/-agent$/, "");
  const host = (url) => { try { return new URL(url).host; } catch { return url || ""; } };
  const scopeOf = (id) => (id === "global" ? { id: "global", name: "All APIs", kind: "global", ...map.global } : map.lanes.find((l) => l.id === id));

  // ---------- data ----------
  async function load(force = false) {
    if (loading) return loading;
    if (!force && map && Date.now() - loadedAt < 15000) return;
    loading = (async () => {
      try {
        if (!map) root.innerHTML = '<div class="gm-empty"><span class="spinner"></span> Reading the gateway configuration from APIM…</div>';
        const res = await fetch("/api/gateway/map");
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || res.statusText);
        if (!data.enabled) {
          root.innerHTML = '<div class="gm-empty">The gateway map needs APIM_RESOURCE_ID (set in the Azure deployment).</div>';
          return;
        }
        map = data;
        loadedAt = Date.now();
        if (!map.callers.some((c) => c.name === viewAs)) viewAs = map.callers[0]?.name;
        render();
      } catch (err) {
        if (map) render();
        else root.innerHTML = `<div class="gm-empty err">Could not read the gateway configuration: ${esc(err.message)}</div>`;
      } finally {
        loading = null;
      }
    })();
    return loading;
  }

  // ---------- what a caller can reach ----------
  function shownSteps(steps) {
    return steps.filter((s) => allSteps || !s.minor);
  }

  // The first step that refuses this caller, walking the global policy and then the lane.
  function stopFor(caller, lane) {
    for (const scope of [{ id: "global", steps: map.global.steps }, lane]) {
      for (const step of scope.steps) {
        if (step.section !== "inbound") continue;
        if (step.effect?.[caller]?.state === "deny") return { scope: scope.id, step };
      }
    }
    return null;
  }

  // ---------- rendering ----------
  function render() {
    const lanes = map.lanes;
    const rows = Math.max(lanes.length, 1);
    root.innerHTML = `
      <div class="gm-head">
        <div class="gm-title">
          <h2>How API Management governs this app</h2>
          <p>Live from <b>${esc(map.service)}</b>. Click a step to see the policy behind it; calls light up as the agent makes them.</p>
        </div>
        <div class="gm-controls">
          <div class="gm-seg" role="group" aria-label="View as">
            <span>View as</span>
            ${map.callers.map((c) => `<button type="button" data-viewas="${esc(c.name)}" class="${c.name === viewAs ? "on" : ""}">${esc(c.name === "unknown" ? "unknown caller" : short(c.name))}</button>`).join("")}
          </div>
          <label class="gm-toggle"><input type="checkbox" id="gm-all" ${allSteps ? "checked" : ""}/> All steps</label>
          <button type="button" class="ghost small" id="gm-refresh" title="Read the configuration again">↻</button>
        </div>
      </div>
      <div class="gm-scroll">
        <div class="gm-board" id="gm-board" style="--rows:${rows}">
          <svg class="gm-wires" id="gm-wires" aria-hidden="true"></svg>
          <div class="gm-coltitle" style="grid-column:1">Callers</div>
          <div class="gm-coltitle gm-apim" style="grid-column:3 / span 3">${icon("apim", 15)} Azure API Management <span>${esc(map.service)}</span></div>
          <div class="gm-coltitle" style="grid-column:7">Backends</div>
          <div class="gm-frame"></div>
          <div class="gm-callers" style="grid-row:2 / span ${rows}">${map.callers.map(callerCard).join("")}</div>
          <div class="gm-global" id="gm-global" style="grid-row:2 / span ${rows}">
            <div class="gm-scope"><span class="tag global">ALL APIS</span><span>Global policy</span></div>
            <div class="gm-gsteps">${shownSteps(map.global.steps).map((s, i) => chip("global", s, i)).join("")}</div>
          </div>
          ${lanes.map((lane, i) => laneRow(lane, i + 2)).join("")}
        </div>
      </div>
      <div class="gm-legend">
        <span><i class="lg pass"></i>passes</span><span><i class="lg limit"></i>limited</span>
        <span><i class="lg deny"></i>refused</span><span><i class="lg live"></i>call in flight</span>
      </div>
      <aside class="gm-drawer" id="gm-drawer" aria-hidden="true"></aside>`;

    root.querySelectorAll("[data-viewas]").forEach((b) => b.addEventListener("click", () => setViewAs(b.dataset.viewas)));
    root.querySelectorAll(".gm-caller").forEach((b) => b.addEventListener("click", () => setViewAs(b.dataset.caller)));
    root.querySelectorAll(".gm-step").forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); openStep(b.dataset.scope, Number(b.dataset.step)); }));
    root.querySelector("#gm-all").addEventListener("change", (e) => { allSteps = e.target.checked; render(); });
    root.querySelector("#gm-refresh").addEventListener("click", () => load(true));
    root.querySelector("#gm-board").addEventListener("click", closeDrawer);

    applyViewAs();
    new ResizeObserver(drawWires).observe(root.querySelector("#gm-board"));
    if (open) openStep(open.scope, open.step);
  }

  function callerCard(caller) {
    const unknown = caller.name === "unknown";
    const id = (caller.clientId || "").slice(0, 8);
    return `<button type="button" class="gm-caller" data-caller="${esc(caller.name)}">
      <span class="gm-avatar ${unknown ? "unknown" : ""}">${icon(unknown ? "unknown" : "agent", 18)}</span>
      <span class="gm-cname" title="${esc(caller.name)}">${esc(unknown ? "unknown" : short(caller.name))}</span>
      <span class="gm-cmeta" title="${esc(caller.clientId || "")}">${unknown ? "any other token" : "agent · " + esc(id)}</span>
    </button>`;
  }

  function laneRow(lane, row) {
    const backendName = { model: "Azure AI Foundry", mcp: "Microsoft Learn", a2a: "Specialist agent" }[lane.kind] || lane.name;
    const backendSub = { model: "gpt model deployment", mcp: "public MCP server", a2a: "Container App · A2A" }[lane.kind] || "";
    return `
      <div class="gm-lane ${lane.kind}" data-lane="${esc(lane.id)}" style="grid-row:${row}">
        <div class="gm-scope"><span class="tag ${lane.kind}">${LANE_LABEL[lane.kind]}</span><span>${esc(lane.name)}</span><code>${esc(lane.path)}</code></div>
        <div class="gm-steps">${shownSteps(lane.steps).map((s, i) => chip(lane.id, s, i)).join("")}</div>
        <div class="gm-outcome" data-outcome="${esc(lane.id)}"></div>
      </div>
      <div class="gm-backend ${lane.kind}" data-backend="${esc(lane.id)}" style="grid-row:${row}">
        <span class="gm-bicon">${icon(lane.kind, 18)}</span>
        <span class="gm-bname">${esc(backendName)}</span>
        <span class="gm-bmeta" title="${esc(lane.backend?.url || "")}">${esc(host(lane.backend?.url))}</span>
        <span class="gm-bsub">${esc(backendSub)}</span>
      </div>`;
  }

  function chip(scope, step, index) {
    const section = SECTION_LABEL[step.section] ? `<span class="gm-sec">${SECTION_LABEL[step.section]}</span>` : "";
    return `<button type="button" class="gm-step k-${esc(step.kind)} ${step.minor ? "minor" : ""}" data-scope="${esc(scope)}" data-step="${step.id}" style="--i:${index}" title="${esc(step.title)}">
      <span class="gm-sicon">${icon(step.kind, 15)}</span>
      <span class="gm-stext"><span class="gm-stitle">${esc(step.title)}${section}</span>${step.minor ? "" : `<span class="gm-ssum">${esc(step.summary)}</span>`}</span>
      <span class="gm-badge"></span>
    </button>`;
  }

  function setViewAs(name) {
    viewAs = name;
    root.querySelectorAll("[data-viewas]").forEach((b) => b.classList.toggle("on", b.dataset.viewas === name));
    applyViewAs();
    if (open) openStep(open.scope, open.step, false);
  }

  // Colour every step for the selected caller and mark where it gets refused.
  function applyViewAs() {
    root.querySelectorAll(".gm-caller").forEach((c) => c.classList.toggle("on", c.dataset.caller === viewAs));
    const globalStop = stopFor(viewAs, { id: "none", steps: [] });
    paintScope("global", map.global.steps, globalStop);
    for (const lane of map.lanes) {
      const stop = stopFor(viewAs, lane);
      paintScope(lane.id, lane.steps, stop && stop.scope === lane.id ? stop : null, Boolean(stop && stop.scope === "global"));
      const outcome = root.querySelector(`[data-outcome="${CSS.escape(lane.id)}"]`);
      const reached = !stop;
      root.querySelector(`[data-lane="${CSS.escape(lane.id)}"]`).classList.toggle("blocked", !reached);
      root.querySelector(`[data-backend="${CSS.escape(lane.id)}"]`).classList.toggle("unreached", !reached);
      outcome.className = "gm-outcome " + (reached ? "ok" : "no");
      outcome.innerHTML = reached ? "✓" : `✕ ${esc(stop.step.effect[viewAs].label)}`;
      outcome.title = reached ? `${viewAs} reaches the backend` : `${viewAs} is refused at "${stop.step.title}"`;
    }
    drawWires();
  }

  function paintScope(scope, steps, stop, unreachedAll = false) {
    let after = unreachedAll;
    for (const step of steps) {
      const el = root.querySelector(`.gm-step[data-scope="${CSS.escape(scope)}"][data-step="${step.id}"]`);
      const effect = step.effect?.[viewAs];
      const isStop = stop && stop.step.id === step.id;
      if (el) {
        el.classList.remove("pass", "limit", "deny", "block", "unreached");
        const badge = el.querySelector(".gm-badge");
        badge.textContent = "";
        if (after) el.classList.add("unreached");
        else if (effect) {
          el.classList.add(effect.state);
          badge.textContent = effect.label ? `${STATE_ICON[effect.state] || ""} ${effect.label}` : STATE_ICON[effect.state] || "";
        }
      }
      if (isStop) after = true;
    }
  }

  // ---------- wires ----------
  function rel(el, board) {
    const r = el.getBoundingClientRect();
    const b = board.getBoundingClientRect();
    return { x: r.left - b.left, y: r.top - b.top, w: r.width, h: r.height, cx: r.left - b.left + r.width / 2, cy: r.top - b.top + r.height / 2 };
  }
  const curve = (x1, y1, x2, y2) => {
    const dx = Math.max(24, (x2 - x1) / 2);
    return `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`;
  };

  function drawWires() {
    const board = root.querySelector("#gm-board");
    const svg = root.querySelector("#gm-wires");
    if (!board || !svg || !visible() || !map) return;
    svg.setAttribute("width", board.scrollWidth);
    svg.setAttribute("height", board.scrollHeight);
    const g = rel(root.querySelector("#gm-global"), board);
    const gy = g.cy;
    const paths = [];
    // Callers into the global policy.
    for (const card of root.querySelectorAll(".gm-caller")) {
      const c = rel(card, board);
      const on = card.dataset.caller === viewAs;
      paths.push(`<path class="w caller ${on ? "on" : ""}" data-caller="${esc(card.dataset.caller)}" d="${curve(c.x + c.w, c.cy, g.x, gy)}"/>`);
    }
    // Global steps are stacked: a short track behind them.
    paths.push(`<path class="w track on" d="M${g.cx},${g.y + 40} V${g.y + g.h - 12}"/>`);
    for (const lane of map.lanes) {
      const laneEl = root.querySelector(`[data-lane="${CSS.escape(lane.id)}"]`);
      const steps = rel(laneEl.querySelector(".gm-steps"), board);
      const l = rel(laneEl, board);
      const b = rel(root.querySelector(`[data-backend="${CSS.escape(lane.id)}"]`), board);
      const reached = !laneEl.classList.contains("blocked");
      const y = steps.cy;
      const cls = `w ${lane.kind} ${reached ? "on" : "off"}`;
      paths.push(`<path class="${cls}" d="${curve(g.x + g.w, gy, l.x, y)}"/>`);
      paths.push(`<path class="${cls}" d="M${l.x},${y} H${l.x + l.w}"/>`);
      paths.push(`<path class="${cls}" d="${curve(l.x + l.w, y, b.x, b.cy)}"/>`);
      // The whole route of a call from the orchestrator, used for in-flight animation.
      const orch = root.querySelector('.gm-caller[data-caller="orchestrator-agent"]');
      if (orch) {
        const c = rel(orch, board);
        const d = `${curve(c.x + c.w, c.cy, g.x, gy)} L${g.x + g.w},${gy} ${curve(g.x + g.w, gy, l.x, y).replace(/^M[^C]+/, "")} L${l.x + l.w},${y} ${curve(l.x + l.w, y, b.x, b.cy).replace(/^M[^C]+/, "")}`;
        paths.push(`<path class="route ${lane.kind}" data-route="${lane.kind}" d="${d}"/>`);
      }
    }
    svg.innerHTML = paths.join("");
    for (const kind of new Set(inflight.values())) setLive(kind, true);
  }

  // ---------- live calls ----------
  function laneFor(kind) {
    return map?.lanes.find((l) => l.kind === kind);
  }

  function setLive(kind, on) {
    const lane = laneFor(kind);
    if (!lane) return;
    root.querySelector(`[data-route="${kind}"]`)?.classList.toggle("live", on);
    root.querySelector(`[data-lane="${CSS.escape(lane.id)}"]`)?.classList.toggle("live", on);
    root.querySelector(`[data-backend="${CSS.escape(lane.id)}"]`)?.classList.toggle("live", on);
    root.querySelector('.gm-caller[data-caller="orchestrator-agent"]')?.classList.toggle("live", liveCount() > 0);
    root.querySelector("#gm-global")?.classList.toggle("live", liveCount() > 0);
  }

  function dot(kind, cls, reverse = false) {
    const route = root.querySelector(`[data-route="${kind}"]`);
    const svg = root.querySelector("#gm-wires");
    if (!route || !svg) return;
    const ns = "http://www.w3.org/2000/svg";
    const circle = document.createElementNS(ns, "circle");
    circle.setAttribute("r", "4.5");
    circle.setAttribute("class", `pulse ${kind} ${cls}`);
    const motion = document.createElementNS(ns, "animateMotion");
    motion.setAttribute("dur", "0.9s");
    motion.setAttribute("fill", "freeze");
    motion.setAttribute("path", route.getAttribute("d"));
    if (reverse) { motion.setAttribute("keyPoints", "1;0"); motion.setAttribute("keyTimes", "0;1"); motion.setAttribute("calcMode", "linear"); }
    circle.append(motion);
    svg.append(circle);
    motion.beginElement?.();
    setTimeout(() => circle.remove(), 1000);
  }

  function flash(el, cls, ms = 2200) {
    if (!el) return;
    el.classList.remove(cls);
    void el.offsetWidth; // restart the animation
    el.classList.add(cls);
    setTimeout(() => el.classList.remove(cls), ms);
  }

  // Where a refused call stopped, from its status: 401 at token validation, 403 at the caller gate,
  // a blocked tool at the tool block, 429 at the limit.
  function stopStep(kind, call) {
    const lane = laneFor(kind);
    const find = (scope, kinds) => {
      const steps = scope === "global" ? map.global.steps : lane.steps;
      const step = steps.find((s) => kinds.includes(s.kind));
      return step && root.querySelector(`.gm-step[data-scope="${CSS.escape(scope === "global" ? "global" : lane.id)}"][data-step="${step.id}"]`);
    };
    if (call.blocked) return find(lane.id, ["tool-block"]);
    if (call.status === 401) return find("global", ["validate-token"]);
    if (call.status === 403) return find(lane.id, ["caller-gate", "token-limit"]);
    if (call.status === 429) return find(lane.id, kind === "model" ? ["token-limit"] : ["rate-limit", "token-limit"]);
    return null;
  }

  function callStart(call) {
    if (!["model", "mcp", "a2a"].includes(call.kind)) return;
    inflight.set(call.callId, call.kind);
    if (!visible() || !map) return;
    setLive(call.kind, true);
    dot(call.kind, "out");
  }

  function callEnd(call) {
    if (!["model", "mcp", "a2a"].includes(call.kind)) return;
    inflight.delete(call.callId);
    if (!visible() || !map) return;
    if (liveCount(call.kind) === 0) setLive(call.kind, false);
    const housekeeping = call.kind === "mcp" && call.status === 405;
    const refused = (call.status >= 400 && !housekeeping) || Boolean(call.blocked);
    const lane = laneFor(call.kind);
    if (!lane) return;
    if (refused) {
      const step = stopStep(call.kind, call);
      if (step) {
        step.dataset.hit = call.blocked ? "blocked" : String(call.status);
        flash(step, "hit-deny", 3000);
      }
      flash(root.querySelector(`[data-route="${call.kind}"]`), "fail", 1400);
    } else if (!housekeeping) {
      dot(call.kind, "back", true);
      flash(root.querySelector(`[data-backend="${CSS.escape(lane.id)}"]`), "hit-ok", 900);
    }
  }

  // ---------- drawer: the policy behind a step ----------
  function openStep(scopeId, stepId, scroll = true) {
    const scope = scopeOf(scopeId);
    const step = scope?.steps.find((s) => s.id === stepId);
    const drawer = root.querySelector("#gm-drawer");
    if (!step || !drawer) return;
    open = { scope: scopeId, step: stepId };
    root.querySelectorAll(".gm-step.selected").forEach((s) => s.classList.remove("selected"));
    root.querySelector(`.gm-step[data-scope="${CSS.escape(scopeId)}"][data-step="${stepId}"]`)?.classList.add("selected");

    const kindTag = scopeId === "global" ? '<span class="tag global">ALL APIS</span>' : `<span class="tag ${scope.kind}">${LANE_LABEL[scope.kind]}</span>`;
    const effects = step.effect
      ? `<div class="gd-effects">${map.callers.map((c) => {
          const e = step.effect[c.name];
          return `<span class="gd-eff ${e.state} ${c.name === viewAs ? "me" : ""}"><b>${esc(c.name === "unknown" ? "unknown" : short(c.name))}</b>${STATE_ICON[e.state] || ""} ${esc(e.label || { pass: "passes", block: "tools blocked" }[e.state] || "")}</span>`;
        }).join("")}</div>`
      : "";
    const [a, b] = step.lines;
    drawer.innerHTML = `
      <div class="gd-head">
        <div class="gd-icon k-${esc(step.kind)}">${icon(step.kind, 18)}</div>
        <div class="gd-titles">${kindTag}<h3>${esc(step.title)}</h3><p>${esc(step.summary)}</p></div>
        <button type="button" class="ghost icon gd-close" aria-label="Close">✕</button>
      </div>
      <p class="gd-explain">${esc(EXPLAIN[step.kind] || EXPLAIN.other)}</p>
      ${effects}
      <div class="gd-knobs" id="gd-knobs"></div>
      <div class="gd-code-head"><span>Policy</span><code>${esc(scope.name)}</code><span class="gd-lines">${a === b ? `line ${a}` : `lines ${a}–${b}`}</span></div>
      <div class="gd-code" id="gd-code">${renderXml(scope.xml, a, b)}</div>`;
    drawer.querySelector(".gd-close").addEventListener("click", closeDrawer);
    drawer.addEventListener("click", (e) => e.stopPropagation());

    // The knobs this step reads, with the same controls as the knob panel.
    const box = drawer.querySelector("#gd-knobs");
    const editable = (step.knobs || []).map((name) => (typeof knobList !== "undefined" ? knobList : []).find((k) => k.name === name)).filter(Boolean);
    if (editable.length) {
      box.append(Object.assign(document.createElement("div"), { className: "gd-knobs-title", textContent: "Turn the knob" }));
      for (const knob of editable) box.append(knobRow(knob));
    }
    drawer.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
    if (scroll) {
      const target = drawer.querySelector(".gd-code .ln.hl");
      if (target) drawer.querySelector("#gd-code").scrollTop = Math.max(0, target.offsetTop - 60);
    }
  }

  function closeDrawer() {
    open = null;
    root.querySelector("#gm-drawer")?.classList.remove("open");
    root.querySelectorAll(".gm-step.selected").forEach((s) => s.classList.remove("selected"));
  }

  // ---------- APIM policy XML highlighting ----------
  // APIM's raw policy text is not strict XML: expressions (@(...) and @{...}) may hold quotes,
  // generics and comparisons, so they are scanned by bracket balance, skipping C# string literals.
  function codeEnd(src, open) {
    let depth = 0;
    for (let i = open; i < src.length; i++) {
      const ch = src[i];
      if (ch === '"' || ch === "'") {
        for (i++; i < src.length && src[i] !== ch; i++) if (src[i] === "\\") i++;
        continue;
      }
      if (ch === "(" || ch === "{") depth++;
      else if (ch === ")" || ch === "}") { depth--; if (depth === 0) return i + 1; }
    }
    return src.length;
  }

  function tokenize(src) {
    const out = [];
    const push = (cls, text) => text && out.push([cls, text]);
    const isExpr = (j) => src[j] === "@" && (src[j + 1] === "(" || src[j + 1] === "{");
    let i = 0;
    while (i < src.length) {
      if (src.startsWith("<!--", i)) {
        const j = src.indexOf("-->", i);
        const end = j < 0 ? src.length : j + 3;
        push("c", src.slice(i, end)); i = end; continue;
      }
      if (src[i] === "<" && /[/A-Za-z]/.test(src[i + 1] || "")) {
        const name = /^<\/?[\w:.-]+/.exec(src.slice(i))[0];
        push("t", name); i += name.length;
        while (i < src.length) {
          const ws = /^\s+/.exec(src.slice(i, i + 200));
          if (ws) { push("", ws[0]); i += ws[0].length; continue; }
          if (src.startsWith("/>", i)) { push("t", "/>"); i += 2; break; }
          if (src[i] === ">") { push("t", ">"); i += 1; break; }
          const attr = /^[\w:.-]+/.exec(src.slice(i, i + 200));
          if (attr) { push("a", attr[0]); i += attr[0].length; continue; }
          if (src[i] === "=") { push("p", "="); i += 1; continue; }
          if (src[i] === '"') {
            if (isExpr(i + 1)) {
              push("s", '"'); i += 1;
              const end = codeEnd(src, i + 1);
              push("x", src.slice(i, end)); i = end;
              if (src[i] === '"') { push("s", '"'); i += 1; }
            } else {
              const j = src.indexOf('"', i + 1);
              const end = j < 0 ? src.length : j + 1;
              push("s", src.slice(i, end)); i = end;
            }
            continue;
          }
          push("", src[i]); i += 1;
        }
        continue;
      }
      const lead = /^\s*/.exec(src.slice(i, i + 400))[0];
      if (isExpr(i + lead.length)) {
        push("", lead); i += lead.length;
        const end = codeEnd(src, i + 1);
        push("x", src.slice(i, end)); i = end; continue;
      }
      const j = src.indexOf("<", i + 1);
      const end = j < 0 ? src.length : j;
      push("v", src.slice(i, end)); i = end;
    }
    return out;
  }

  // Named values ({{name}}) get their current value next to them; secrets stay hidden.
  function withNamedValues(text) {
    return text.split(/(\{\{[\w-]+\}\})/).map((part) => {
      const m = /^\{\{([\w-]+)\}\}$/.exec(part);
      if (!m) return esc(part);
      const name = m[1];
      const secret = map.secrets.includes(name);
      const value = map.namedValues[name];
      const shown = secret ? "•••• secret" : value == null ? "?" : value.length > 18 ? value.slice(0, 8) + "…" : value;
      const knob = map.knobs.includes(name) ? " nv-knob" : "";
      return `<span class="nv${knob}" title="${esc(secret ? "secret named value" : value)}">${esc(part)}</span><span class="nvv${secret ? " secret" : ""}">${esc(shown)}</span>`;
    }).join("");
  }

  function renderXml(src, from, to) {
    const lines = [""];
    for (const [cls, text] of tokenize(src.replace(/\t/g, "  "))) {
      text.split("\n").forEach((piece, n) => {
        if (n > 0) lines.push("");
        if (piece) lines[lines.length - 1] += cls ? `<span class="x-${cls}">${withNamedValues(piece)}</span>` : withNamedValues(piece);
      });
    }
    return lines.map((html, n) => `<div class="ln${n + 1 >= from && n + 1 <= to ? " hl" : ""}"><i>${n + 1}</i><span>${html || " "}</span></div>`).join("");
  }

  // ---------- wiring ----------
  window.addEventListener("knobs-changed", () => visible() && load(true));
  window.addEventListener("resize", () => visible() && drawWires());
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && visible()) closeDrawer(); });

  // The turn is over: nothing it started is still in flight, even if an answer never arrived
  // (a timed-out or cancelled request, or one that finished after the event stream closed).
  function reset() {
    const kinds = new Set(inflight.values());
    inflight.clear();
    for (const kind of kinds) setLive(kind, false);
  }

  return {
    show() { load(); requestAnimationFrame(drawWires); },
    reset,
    callStart,
    callEnd,
  };
})();
