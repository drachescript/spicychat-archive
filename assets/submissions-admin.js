const $ = (s) => document.querySelector(s);
const esc = (v="") => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmt = (v) => Number(v || 0).toLocaleString();
const date = (v) => {
  if (!v) return "—";
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? String(v) : d.toLocaleString();
};

const state = {
  endpoint: localStorage.getItem("scaSubmissionEndpoint") || "https://spicychat-archive-import.dragongraf.workers.dev",
  token: sessionStorage.getItem("scaSubmissionToken") || localStorage.getItem("scaSubmissionToken") || "",
  remember: !!localStorage.getItem("scaSubmissionToken"),
  tab: "pending",
  submissions: [],
  selectedId: null,
  detail: null,
  offset: 0,
  pageSize: 100,
};

$("#endpoint").value = state.endpoint;
$("#token").value = state.token;
$("#remember").checked = state.remember;

function toast(message) {
  const old = $(".toast");
  if (old) old.remove();
  const div = document.createElement("div");
  div.className = "toast";
  div.textContent = message;
  document.body.append(div);
  setTimeout(() => div.remove(), 4500);
}

function saveAuth() {
  state.endpoint = $("#endpoint").value.trim().replace(/\/+$/, "");
  state.token = $("#token").value.trim();
  state.remember = $("#remember").checked;
  localStorage.setItem("scaSubmissionEndpoint", state.endpoint);
  sessionStorage.setItem("scaSubmissionToken", state.token);
  if (state.remember) localStorage.setItem("scaSubmissionToken", state.token);
  else localStorage.removeItem("scaSubmissionToken");
}

async function api(path, options={}) {
  saveAuth();
  if (!state.token) throw new Error("Enter the existing archive import token.");
  const res = await fetch(`${state.endpoint}${path}`, {
    ...options,
    headers: {
      "Authorization": `Bearer ${state.token}`,
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
  return data;
}

function setAuthStatus(text, good=false) {
  const el = $("#auth-status");
  el.textContent = text;
  el.style.color = good ? "var(--good)" : "var(--muted)";
}

function breakdownHtml(b={}) {
  return `
    <div class="breakdown">
      <div class="review-stat"><b>${fmt(b.new)}</b><span>New</span></div>
      <div class="review-stat"><b>${fmt(b.known)}</b><span>Known / same</span></div>
      <div class="review-stat"><b>${fmt(b.changed)}</b><span>Changed / fills gaps</span></div>
      <div class="review-stat"><b>${fmt(b.exactDuplicate)}</b><span>Exact duplicate</span></div>
      <div class="review-stat warn"><b>${fmt(b.unavailableHistory)}</b><span>Unavailable-history flags</span></div>
      <div class="review-stat bad"><b>${fmt(b.problem)}</b><span>Problem / malformed</span></div>
    </div>`;
}

function renderList() {
  const list = $("#submission-list");
  $("#pending-count").textContent = state.tab === "pending" ? `(${state.submissions.length})` : "";
  if (!state.submissions.length) {
    list.innerHTML = `<div class="admin-empty">No ${esc(state.tab)} submissions.</div>`;
    return;
  }

  list.innerHTML = state.submissions.map(row => {
    const b = row.breakdown || {};
    const active = row.submissionId === state.selectedId ? "active" : "";
    const line = state.tab === "pending"
      ? `${fmt(row.botCount)} bots · ${row.analysisReady ? `${fmt(b.safe)} safe` : "analysis pending"}`
      : `${fmt(row.acceptedCount ?? row.botCount)} bots`;
    return `<button class="submission-row ${active}" data-id="${esc(row.submissionId)}" type="button">
      <strong>${date(row.submittedAt || row.reviewedAt)}</strong>
      <span>${line}</span>
      <span>${esc(row.submissionId)}</span>
    </button>`;
  }).join("");

  list.querySelectorAll("[data-id]").forEach(btn => {
    btn.addEventListener("click", () => openSubmission(btn.dataset.id));
  });
}

function statusLabels(row) {
  const a = row.assessment || {};
  const cls = a.classification || "pending";
  return `<span class="label ${esc(cls)}">${esc(cls.replaceAll("_", " "))}</span>
    ${a.unavailableHistory ? '<span class="label unavailable">unavailable history</span>' : ""}`;
}

function renderPendingDetail(data) {
  const sub = data.submission || {};
  const analysis = data.analysis;
  const b = analysis?.breakdown || sub.breakdown || {};
  const ready = !!analysis && sub.analysisReady;

  let rows = "";
  for (const row of data.records || []) {
    const a = row.assessment || {};
    const problems = [...(row.problems || []), ...(a.problems || [])];
    const rowClass = a.classification === "problem" ? "problem" : a.classification === "exact_duplicate" ? "duplicate" : "";
    const diff = (a.diffFields || []).join(", ");
    rows += `<tr class="${rowClass}" data-bot-row="${esc(row.botId)}">
      <td><input class="bot-select" type="checkbox" value="${esc(row.botId)}" ${a.safe && a.classification !== "exact_duplicate" ? "" : "disabled"}></td>
      <td><div class="bot-name">${esc(a.name || row.snapshot?.name || row.snapshot?.title || row.botId)}</div><div class="small">${esc(a.creator || "")}</div></td>
      <td>${statusLabels(row)}</td>
      <td>${diff ? esc(diff) : "—"}${problems.length ? `<div class="small">${esc(problems.join(", "))}</div>` : ""}</td>
      <td>${esc(a.existingStatus || "—")}</td>
      <td><button class="snapshot-toggle" data-snapshot="${esc(row.botId)}" type="button">inspect</button></td>
    </tr>
    <tr class="snapshot-row" data-snapshot-row="${esc(row.botId)}" hidden><td colspan="6"><pre>${esc(JSON.stringify(row.snapshot || {}, null, 2))}</pre></td></tr>`;
  }

  $("#submission-detail").innerHTML = `
    <div class="review-head">
      <div><h2>Pending submission</h2><p>${date(sub.submittedAt)} · ${fmt(sub.botCount)} bots · ${fmt(sub.sizeBytes)} bytes · ${esc(sub.submissionId)}</p></div>
      <span class="label ${ready ? "new" : "known"}">${ready ? "analysis ready" : "waiting for archive analysis"}</span>
    </div>
    ${breakdownHtml(b)}
    <div class="review-actions">
      <button id="approve-safe" class="primary-button" type="button" ${ready ? "" : "disabled"}>Approve all safe (${fmt(b.safe)})</button>
      <button id="approve-selected" class="secondary-button" type="button" ${ready ? "" : "disabled"}>Approve selected</button>
      <button id="reject" class="danger-button" type="button">Reject submission</button>
    </div>
    <div class="bot-review-tools">
      <input id="bot-filter" placeholder="Filter loaded bot names / IDs / creator">
      <button id="select-safe" class="secondary-button" type="button">Select loaded safe</button>
      <span class="small">Exact duplicates and problem records cannot be selected. Unavailable-history is a warning only; current archive status is still controlled by normal verification.</span>
    </div>
    <div class="bot-table-wrap"><table class="bot-table">
      <thead><tr><th></th><th>Bot</th><th>Review</th><th>Differences / problems</th><th>Archive status</th><th></th></tr></thead>
      <tbody>${rows || '<tr><td colspan="6">No records on this page.</td></tr>'}</tbody>
    </table></div>
    <div class="pager"><button id="prev" class="secondary-button" type="button" ${data.offset <= 0 ? "disabled" : ""}>Previous</button>
      <span>${fmt(data.offset + 1)}–${fmt(Math.min(data.totalRecords, data.offset + (data.records || []).length))} of ${fmt(data.totalRecords)}</span>
      <button id="next" class="secondary-button" type="button" ${data.hasMore ? "" : "disabled"}>Next</button></div>`;

  $("#approve-safe")?.addEventListener("click", () => act("approve-safe"));
  $("#approve-selected")?.addEventListener("click", () => {
    const ids = [...document.querySelectorAll(".bot-select:checked")].map(x => x.value);
    if (!ids.length) return toast("Select at least one safe bot first.");
    act("approve-selected", ids);
  });
  $("#reject")?.addEventListener("click", () => {
    if (confirm("Reject this entire public submission? The sanitized upload is retained for about 7 days, then deleted.")) act("reject");
  });
  $("#select-safe")?.addEventListener("click", () => {
    document.querySelectorAll(".bot-select:not(:disabled)").forEach(x => { x.checked = true; });
  });
  $("#prev")?.addEventListener("click", () => {
    state.offset = Math.max(0, state.offset - state.pageSize);
    openSubmission(state.selectedId, true);
  });
  $("#next")?.addEventListener("click", () => {
    state.offset += state.pageSize;
    openSubmission(state.selectedId, true);
  });
  $("#bot-filter")?.addEventListener("input", e => {
    const q = e.target.value.toLowerCase();
    document.querySelectorAll("[data-bot-row]").forEach(row => {
      const visible = !q || row.textContent.toLowerCase().includes(q) || row.dataset.botRow.toLowerCase().includes(q);
      row.hidden = !visible;
      const detail = document.querySelector(`[data-snapshot-row="${CSS.escape(row.dataset.botRow)}"]`);
      if (detail && !visible) detail.hidden = true;
    });
  });
  document.querySelectorAll("[data-snapshot]").forEach(btn => {
    btn.addEventListener("click", () => {
      const row = document.querySelector(`[data-snapshot-row="${CSS.escape(btn.dataset.snapshot)}"]`);
      if (row) row.hidden = !row.hidden;
    });
  });
}

function renderReviewedDetail(data) {
  const sub = data.submission || {};
  $("#submission-detail").innerHTML = `
    <div class="review-head"><div><h2>${esc(data.status)} submission</h2>
      <p>Submitted ${date(sub.submittedAt)} · reviewed ${date(sub.reviewedAt)} · ${esc(sub.submissionId || "")}</p></div></div>
    ${sub.breakdown ? breakdownHtml(sub.breakdown) : ""}
    <div class="section">
      <div class="field-block"><h3>Decision</h3><div class="pre">${esc(JSON.stringify(sub, null, 2))}</div></div>
    </div>`;
}

async function openSubmission(id, keepOffset=false) {
  if (!keepOffset) state.offset = 0;
  state.selectedId = id;
  renderList();
  $("#submission-detail").innerHTML = '<div class="admin-empty">Loading submission…</div>';
  try {
    const data = await api(`/api/admin/submissions/${encodeURIComponent(id)}?offset=${state.offset}&limit=${state.pageSize}`);
    state.detail = data;
    if (data.status === "pending") renderPendingDetail(data);
    else renderReviewedDetail(data);
  } catch (e) {
    $("#submission-detail").innerHTML = `<div class="admin-empty">${esc(e.message)}</div>`;
  }
}

async function loadTab(tab=state.tab) {
  state.tab = tab;
  state.selectedId = null;
  state.detail = null;
  state.offset = 0;
  document.querySelectorAll(".tab").forEach(btn => btn.classList.toggle("active", btn.dataset.tab === tab));
  $("#submission-detail").innerHTML = '<div class="admin-empty">Select a submission.</div>';
  $("#submission-list").innerHTML = '<div class="admin-empty">Loading…</div>';
  try {
    const data = await api(`/api/admin/submissions?status=${encodeURIComponent(tab)}&limit=150`);
    state.submissions = data.submissions || [];
    setAuthStatus("Connected", true);
    renderList();
  } catch (e) {
    state.submissions = [];
    renderList();
    setAuthStatus(e.message, false);
  }
}

async function act(action, selectedBotIds=[]) {
  if (!state.selectedId) return;
  try {
    const data = await api(`/api/admin/submissions/${encodeURIComponent(state.selectedId)}/action`, {
      method: "POST",
      body: JSON.stringify({ action, selectedBotIds }),
    });
    if (action === "reject") toast("Submission rejected.");
    else toast(`${fmt(data.queuedForImport)} bot snapshots queued for the existing archive importer.`);
    await loadTab(state.tab);
  } catch (e) {
    toast(`Action failed: ${e.message}`);
  }
}

document.querySelectorAll(".tab").forEach(btn => {
  btn.addEventListener("click", () => loadTab(btn.dataset.tab));
});
$("#connect").addEventListener("click", () => loadTab("pending"));
$("#refresh").addEventListener("click", () => loadTab(state.tab));
$("#remember").addEventListener("change", saveAuth);

if (state.token) loadTab("pending");
