import { appUrl, del, formatBytes, formatTime, getJSON, postJSON } from "./api.js";

const tbody = document.getElementById("threads");
const addError = document.getElementById("add-error");

function showError(err) {
  addError.textContent = err instanceof Error ? err.message : String(err);
}

function clearError() {
  addError.textContent = "";
}

// Spouští akci a promítne selhání do sdílené chybové hlášky, aby žádná
// mutace ani refresh() neselhaly potichu; úspěch chybu smaže.
async function run(action) {
  clearError();
  try {
    await action();
  } catch (err) {
    showError(err);
  }
}

async function loadStats() {
  const s = await getJSON("/api/stats");
  document.getElementById("stats").innerHTML = "";
  const items = [
    `live: <b>${s.threads.live}</b>`,
    `dead: <b>${s.threads.dead}</b>`,
    `error: <b>${s.threads.error}</b>`,
    `media: <b>${formatBytes(s.media_bytes)}</b>`,
    `pending: <b>${s.media_pending}</b>`,
    `failed: <b>${s.media_failed}</b>`,
    `last poll: <b>${formatTime(s.last_polled)}</b>`,
  ];
  for (const html of items) {
    const span = document.createElement("span");
    span.innerHTML = html;
    document.getElementById("stats").appendChild(span);
  }
}

// `label` nese jméno sloupce do data-label; na úzkém displeji se tabulka
// překlápí na karty a CSS ho vypisuje před hodnotu místo hlavičky tabulky.
function cell(row, text, label) {
  const td = document.createElement("td");
  td.textContent = text;
  if (label) td.dataset.label = label;
  row.appendChild(td);
  return td;
}

function renderThread(t) {
  const tr = document.createElement("tr");
  cell(tr, t.board, "Board");
  const link = document.createElement("a");
  link.href = appUrl(`thread.html?b=${encodeURIComponent(t.board)}&no=${t.no}`);
  link.textContent = t.no;
  cell(tr, "", "Thread").appendChild(link);
  cell(tr, t.subject || "—", "Subject");
  cell(tr, t.status, "Status").className = `status-${t.status}`;
  cell(tr, t.post_count, "Posts");
  cell(tr, formatBytes(t.bytes), "Size");
  cell(tr, formatTime(t.last_polled), "Last poll");
  cell(tr, t.source, "Source");

  const actions = cell(tr, "");
  const remove = document.createElement("button");
  remove.textContent = "Delete";
  remove.onclick = () => run(async () => {
    if (!confirm(`Delete ${t.board}/${t.no} including its media?`)) return;
    await del(`/api/threads/${t.id}`);
    await refresh();
  });
  actions.appendChild(remove);
  // Selhání médií se nikdy nepropíše do threads.last_error, takže gate jen na
  // last_error nechal zdravý live thread s 404 obrázkem navždy bez retry.
  if (t.media_failed > 0 || t.last_error) {
    const retry = document.createElement("button");
    retry.textContent = "Retry media";
    retry.title = t.media_failed > 0
      ? `${t.media_failed} media downloads failed`
      : t.last_error;
    retry.onclick = () => run(async () => {
      await postJSON(`/api/threads/${t.id}/retry`);
      await refresh();
    });
    actions.appendChild(retry);
  }
  return tr;
}

// Monotónní token: pokud odpověď dorazí až po tom, co byl vyžádán novější
// refresh(), zahodí se, aby pozdější dotaz nepřepsal výsledky tím dřívějším.
let requestToken = 0;

async function refresh() {
  const token = ++requestToken;
  const params = new URLSearchParams();
  const q = document.getElementById("filter-q").value.trim();
  const status = document.getElementById("filter-status").value;
  if (q) params.set("q", q);
  if (status) params.set("status", status);
  const { threads } = await getJSON(`/api/threads?${params}`);
  if (token !== requestToken) return;
  tbody.replaceChildren(...threads.map(renderThread));
  await loadStats();
}

document.getElementById("add-form").onsubmit = (event) => {
  event.preventDefault();
  const input = document.getElementById("url");
  run(async () => {
    await postJSON("/api/threads", { url: input.value });
    input.value = "";
    await refresh();
  });
};

let filterTimer = null;
function scheduleRefresh() {
  clearTimeout(filterTimer);
  filterTimer = setTimeout(() => run(refresh), 300);
}

document.getElementById("refresh").onclick = () => run(refresh);
document.getElementById("filter-q").oninput = scheduleRefresh;
document.getElementById("filter-status").onchange = () => run(refresh);
run(refresh);
