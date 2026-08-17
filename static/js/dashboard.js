import { del, formatBytes, formatTime, getJSON, postJSON } from "./api.js";

const tbody = document.getElementById("threads");
const addError = document.getElementById("add-error");

async function loadStats() {
  const s = await getJSON("/api/stats");
  document.getElementById("stats").innerHTML = "";
  const items = [
    `live: <b>${s.threads.live}</b>`,
    `dead: <b>${s.threads.dead}</b>`,
    `error: <b>${s.threads.error}</b>`,
    `média: <b>${formatBytes(s.media_bytes)}</b>`,
    `ke stažení: <b>${s.media_pending}</b>`,
    `selhalo: <b>${s.media_failed}</b>`,
    `poslední poll: <b>${formatTime(s.last_polled)}</b>`,
  ];
  for (const html of items) {
    const span = document.createElement("span");
    span.innerHTML = html;
    document.getElementById("stats").appendChild(span);
  }
}

function cell(row, text) {
  const td = document.createElement("td");
  td.textContent = text;
  row.appendChild(td);
  return td;
}

function renderThread(t) {
  const tr = document.createElement("tr");
  cell(tr, t.board);
  const link = document.createElement("a");
  link.href = `/thread.html?b=${encodeURIComponent(t.board)}&no=${t.no}`;
  link.textContent = t.no;
  cell(tr, "").appendChild(link);
  cell(tr, t.subject || "—");
  cell(tr, t.status).className = `status-${t.status}`;
  cell(tr, t.post_count);
  cell(tr, formatBytes(t.bytes));
  cell(tr, formatTime(t.last_polled));
  cell(tr, t.source);

  const actions = cell(tr, "");
  const remove = document.createElement("button");
  remove.textContent = "Smazat";
  remove.onclick = async () => {
    if (!confirm(`Smazat ${t.board}/${t.no} včetně médií?`)) return;
    await del(`/api/threads/${t.id}`);
    await refresh();
  };
  actions.appendChild(remove);
  if (t.last_error) {
    const retry = document.createElement("button");
    retry.textContent = "Retry médií";
    retry.title = t.last_error;
    retry.onclick = async () => {
      await postJSON(`/api/threads/${t.id}/retry`);
      await refresh();
    };
    actions.appendChild(retry);
  }
  return tr;
}

async function refresh() {
  const params = new URLSearchParams();
  const q = document.getElementById("filter-q").value.trim();
  const status = document.getElementById("filter-status").value;
  if (q) params.set("q", q);
  if (status) params.set("status", status);
  const { threads } = await getJSON(`/api/threads?${params}`);
  tbody.replaceChildren(...threads.map(renderThread));
  await loadStats();
}

document.getElementById("add-form").onsubmit = async (event) => {
  event.preventDefault();
  addError.textContent = "";
  const input = document.getElementById("url");
  try {
    await postJSON("/api/threads", { url: input.value });
    input.value = "";
    await refresh();
  } catch (err) {
    addError.textContent = err.message;
  }
};

document.getElementById("refresh").onclick = refresh;
document.getElementById("filter-q").oninput = refresh;
document.getElementById("filter-status").onchange = refresh;
refresh();
