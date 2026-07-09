const state = {
  incidents: [],
  currentIncident: null,
  ws: null,
};

function el(id) { return document.getElementById(id); }

function specialistTagClass(specialist) {
  return "tag purple";
}

function fmt(n, digits = 3) {
  if (n === null || n === undefined) return "-";
  return Number(n).toFixed(digits);
}

async function init() {
  const health = await fetch("/api/health").then((r) => r.json());
  const badge = el("mode-badge");
  badge.textContent = health.mock_mode ? "mock mode (no Qwen Cloud key set)" : "live: Qwen Cloud";
  badge.classList.toggle("live", !health.mock_mode);

  state.incidents = await fetch("/api/incidents").then((r) => r.json());
  const select = el("incident-select");
  select.innerHTML = state.incidents
    .map((i) => `<option value="${i.id}">${i.id} — ${i.title}${i.cross_cutting ? " (cross-cutting)" : ""}</option>`)
    .join("");

  el("run-btn").addEventListener("click", runSelectedIncident);
  el("eval-btn").addEventListener("click", runFullEval);

  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      el(`tab-${btn.dataset.tab}`).classList.add("active");
    });
  });

  if (state.incidents.length) {
    select.value = state.incidents.find((i) => i.cross_cutting)?.id || state.incidents[0].id;
  }
}

function resetLivePanels(incident) {
  const toolList = Object.entries(incident.tools || {})
    .map(([specialist, desc]) => `${specialist}: ${desc}`)
    .join(" | ");
  el("incident-text").innerHTML = `<div>${incident.alert}</div>` +
    (toolList ? `<div style="margin-top:8px;color:var(--text-dim);font-size:12px;">Available tools — ${toolList}</div>` : "");
  el("bids").innerHTML = "";
  el("allocation").innerHTML = "";
  el("allocation").classList.add("empty");
  el("negotiation-wrap").classList.add("hidden");
  el("negotiation").innerHTML = "";
  el("resolution").innerHTML = "";
  el("resolution").classList.add("empty");
  el("baseline").innerHTML = "";
  el("baseline").classList.add("empty");
  el("compare").innerHTML = "";
  el("compare").classList.add("empty");
}

function renderBids(bids) {
  const sorted = [...bids].sort((a, b) => b.confidence / b.estimated_cost - a.confidence / a.estimated_cost);
  el("bids").innerHTML = sorted
    .map((b) => {
      const score = b.confidence / b.estimated_cost;
      return `<div class="bid-card">
        <div class="specialist">${b.specialist}</div>
        <div class="metric">confidence <b>${fmt(b.confidence, 2)}</b> &middot; cost <b>${fmt(b.estimated_cost, 2)}</b></div>
        <div class="metric score">score ${fmt(score, 2)}</div>
        ${b.tool_checked ? `<div class="metric" style="color:var(--teal-text);">✓ checked own dashboard</div>` : `<div class="metric" style="color:var(--text-dim);">no relevant dashboard</div>`}
        <div class="metric" style="margin-top:6px;">${b.reasoning}</div>
      </div>`;
    })
    .join("");
}

function renderAllocation(allocation) {
  const panel = el("allocation");
  panel.classList.remove("empty");
  if (allocation.contested) {
    panel.innerHTML = `<span class="tag coral">Overlapping claims</span> ${allocation.contested_specialists.join(", ")} are within the conflict threshold — entering negotiation.`;
  } else {
    panel.innerHTML = `<span class="tag teal">Clear winner</span> <b>${allocation.winner}</b> has the highest confidence/cost score.`;
  }
}

function renderNegotiation(round) {
  el("negotiation-wrap").classList.remove("hidden");
  const wrap = document.createElement("div");
  wrap.className = "round-block";
  let html = `<h3>Round ${round.round_number}</h3>`;
  for (const c of round.claims || []) {
    html += `<div class="negotiation-line"><b>${c.specialist}</b> claims: ${c.claim} <span style="color:var(--text-dim)">(confidence ${fmt(c.confidence, 2)})</span><br/><span style="color:var(--text-dim)">Evidence: ${c.evidence.join("; ")}</span></div>`;
  }
  for (const r of round.rebuttals || []) {
    html += `<div class="negotiation-line"><b>${r.specialist}</b> → <b>${r.target}</b>: ${r.rebuttal}</div>`;
  }
  wrap.innerHTML = html;
  el("negotiation").appendChild(wrap);
}

function outcomeTag(outcome) {
  if (outcome === "escalated") return `<span class="tag amber">Escalated to human</span>`;
  if (outcome === "consensus") return `<span class="tag teal">Consensus reached</span>`;
  return `<span class="tag teal">Clear winner</span>`;
}

function renderResolution(resolution) {
  const panel = el("resolution");
  panel.classList.remove("empty");
  if (resolution.outcome === "escalated") {
    panel.innerHTML = `${outcomeTag(resolution.outcome)}<p>${resolution.escalation_reason}</p>
      ${resolution.judge_reasoning ? `<p style="color:var(--text-dim)"><b>Judge's reasoning:</b> ${resolution.judge_reasoning}</p>` : ""}
      <button class="secondary" id="resolve-manually-btn">Resolve manually</button>`;
    el("resolve-manually-btn")?.addEventListener("click", () => {
      panel.innerHTML = `<span class="tag amber">Resolved by human reviewer</span><p>Flagged for manual root-cause review; case handed off outside the automated flow.</p>`;
    });
    return;
  }
  panel.innerHTML = `${outcomeTag(resolution.outcome)}
    <p><b>Owner:</b> ${resolution.winning_specialist} &middot; <b>Confidence:</b> ${fmt(resolution.confidence, 2)}</p>
    <p><b>Root cause:</b> ${resolution.root_cause}</p>
    <p><b>Remediation:</b> ${resolution.remediation}</p>
    ${resolution.judge_reasoning ? `<p style="color:var(--text-dim)"><b>Judge's reasoning:</b> ${resolution.judge_reasoning}</p>` : ""}`;
}

function renderBaseline(resolution) {
  const panel = el("baseline");
  panel.classList.remove("empty");
  const checked = resolution.tools_checked || [];
  panel.innerHTML = `<p><b>Guess:</b> ${resolution.winning_specialist} &middot; <b>Confidence:</b> ${fmt(resolution.confidence, 2)}</p>
    <p style="color:var(--text-dim);font-size:12px;">Investigated under a limited budget — checked: ${checked.length ? checked.join(", ") : "(none)"}</p>
    <p><b>Root cause:</b> ${resolution.root_cause}</p>
    <p><b>Remediation:</b> ${resolution.remediation}</p>`;
}

function renderCompare(payload, incident) {
  const panel = el("compare");
  panel.classList.remove("empty");
  const ma = payload.multi_agent;
  const bl = payload.baseline;
  const maCorrect = ma.resolution.winning_specialist === incident.ground_truth_specialist;
  const blCorrect = bl.resolution.winning_specialist === incident.ground_truth_specialist;
  panel.innerHTML = `
    <div class="compare-row"><span>Expected owner</span><b>${incident.ground_truth_specialist}</b></div>
    <div class="compare-row"><span>Multi-agent correct?</span><b class="${maCorrect ? "pill-ok" : "pill-bad"}">${maCorrect ? "yes" : "no"}</b></div>
    <div class="compare-row"><span>Baseline correct?</span><b class="${blCorrect ? "pill-ok" : "pill-bad"}">${blCorrect ? "yes" : "no"}</b></div>
    <div class="compare-row"><span>Multi-agent tokens</span><b>${ma.usage.tokens_used}</b></div>
    <div class="compare-row"><span>Baseline tokens</span><b>${bl.usage.tokens_used}</b></div>
    <div class="compare-row"><span>Multi-agent latency</span><b>${fmt(ma.usage.latency_ms, 0)} ms</b></div>
    <div class="compare-row"><span>Baseline latency</span><b>${fmt(bl.usage.latency_ms, 0)} ms</b></div>
  `;
}

function runSelectedIncident() {
  const id = el("incident-select").value;
  const incident = state.incidents.find((i) => i.id === id);
  if (!incident) return;
  resetLivePanels(incident);
  el("run-btn").disabled = true;

  if (state.ws) state.ws.close();
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/run/${id}`);
  state.ws = ws;

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    switch (msg.type) {
      case "bids":
        renderBids(msg.payload.bids);
        break;
      case "allocation":
        renderAllocation(msg.payload);
        break;
      case "negotiation_round":
        renderNegotiation(msg.payload);
        break;
      case "resolution":
        renderResolution(msg.payload);
        break;
      case "baseline_resolution":
        renderBaseline(msg.payload);
        break;
      case "run_complete":
        renderCompare(msg.payload, incident);
        el("run-btn").disabled = false;
        ws.close();
        break;
      case "error":
        alert(msg.payload.message);
        el("run-btn").disabled = false;
        break;
    }
  };
  ws.onerror = () => { el("run-btn").disabled = false; };
  ws.onclose = () => { el("run-btn").disabled = false; };
}

function summaryCard(title, summary) {
  return `<div class="summary-card">
    <h3>${title}</h3>
    <div class="stat"><span>Domain accuracy (label only)</span><b>${fmt(summary.accuracy * 100, 1)}%</b></div>
    <div class="stat"><span>Mechanism accuracy (actual explanation matches)</span><b>${fmt(summary.mechanism_accuracy * 100, 1)}%</b></div>
    <div class="stat"><span>Coverage (committed to an answer)</span><b>${fmt(summary.coverage * 100, 1)}%</b></div>
    <div class="stat"><span>Precision (correct when committed)</span><b>${fmt(summary.precision * 100, 1)}%</b></div>
    <div class="stat"><span>Confidently wrong</span><b>${fmt(summary.confidently_wrong_rate * 100, 1)}%</b></div>
    <div class="stat"><span>Escalated</span><b>${fmt(summary.escalation_rate * 100, 1)}%</b></div>
    <div class="stat"><span>Utility score (+1/0/-1)</span><b>${fmt(summary.utility_score, 3)}</b></div>
    <div class="stat"><span>Avg judge score (/5)</span><b>${fmt(summary.avg_judge_score, 2)}</b></div>
    <div class="stat"><span>Total tokens</span><b>${summary.total_tokens}</b></div>
    <div class="stat"><span>Total latency</span><b>${fmt(summary.total_latency_ms, 0)} ms</b></div>
    <div class="stat"><span>Est. cost</span><b>$${fmt(summary.total_cost_usd, 4)}</b></div>
    <div class="stat"><span>Accuracy / 1k tokens</span><b>${fmt(summary.accuracy_per_1k_tokens, 3)}</b></div>
  </div>`;
}

async function runFullEval() {
  const btn = el("eval-btn");
  btn.disabled = true;
  el("eval-status").textContent = "Running all incidents through both systems…";
  el("eval-summary").innerHTML = "";
  el("eval-table-wrap").innerHTML = "";

  try {
    const data = await fetch("/api/eval/run", { method: "POST" }).then((r) => r.json());
    el("eval-status").textContent = `Scored ${data.per_incident.length} incidents.`;
    el("eval-summary").innerHTML =
      summaryCard("Multi-agent (auction + consensus)", data.summary.multi_agent) +
      summaryCard("Baseline (single generalist agent)", data.summary.baseline);

    const outcomeLabel = (cell) => {
      if (!cell.correct && cell.outcome === "escalated") return { cls: "pill-escalated", text: "escalated" };
      if (!cell.correct) return { cls: "pill-bad", text: `wrong domain (${cell.outcome})` };
      if (!cell.mechanism_correct) return { cls: "pill-escalated", text: `right domain, wrong mechanism` };
      return { cls: "pill-ok", text: `correct (${cell.outcome})` };
    };

    const rows = data.per_incident
      .map((row) => {
        const ma = outcomeLabel(row.multi_agent);
        const bl = outcomeLabel(row.baseline);
        return `<tr>
          <td>${row.incident_id}${row.cross_cutting ? " ⚡" : ""}</td>
          <td>${row.title}</td>
          <td>${row.ground_truth_specialist}</td>
          <td class="${ma.cls}">${ma.text}</td>
          <td class="${bl.cls}">${bl.text}</td>
        </tr>`;
      })
      .join("");
    el("eval-table-wrap").innerHTML = `<table>
      <thead><tr><th>ID</th><th>Incident</th><th>Expected owner</th><th>Multi-agent</th><th>Baseline</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <p style="color:var(--text-dim);font-size:12px;margin-top:8px;">⚡ = deliberately cross-cutting incident (triggers negotiation) &middot;
      <span class="pill-bad">confidently wrong</span> = committed to an answer and got it wrong (the risky outcome) &middot;
      <span class="pill-escalated">escalated</span> = correctly deferred to a human instead of guessing</p>`;
  } finally {
    btn.disabled = false;
  }
}

init();
