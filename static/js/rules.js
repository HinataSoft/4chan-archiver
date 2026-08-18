import { del, formatTime, getJSON, patchJSON, postJSON } from "./api.js";

const tbody = document.getElementById("rules");
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

// `label` nese jméno sloupce do data-label; na úzkém displeji se tabulka
// překlápí na karty a CSS ho vypisuje před hodnotu místo hlavičky tabulky.
function cell(row, text, label) {
  const td = document.createElement("td");
  td.textContent = text;
  if (label) td.dataset.label = label;
  row.appendChild(td);
  return td;
}

function renderRule(rule) {
  const tr = document.createElement("tr");
  cell(tr, rule.board, "Board");

  const keywords = cell(tr, "", "Keywords");
  const input = document.createElement("input");
  input.type = "text";
  input.value = rule.keywords.join(", ");
  input.onchange = () => run(async () => {
    const list = input.value.split(",").map((k) => k.trim()).filter(Boolean);
    try {
      await patchJSON(`/api/rules/${rule.id}`, { keywords: list });
    } finally {
      // I po selhání znovu načíst, aby pole zobrazovalo skutečný stav na serveru.
      await refresh();
    }
  });
  keywords.appendChild(input);

  const toggle = document.createElement("input");
  toggle.type = "checkbox";
  toggle.checked = rule.enabled;
  toggle.onchange = () => run(async () => {
    try {
      await patchJSON(`/api/rules/${rule.id}`, { enabled: toggle.checked });
    } finally {
      await refresh();
    }
  });
  cell(tr, "", "Enabled").appendChild(toggle);

  cell(tr, formatTime(rule.last_scan_at), "Last scan");
  cell(tr, rule.last_error || "—", "Error");

  const remove = document.createElement("button");
  remove.textContent = "Delete";
  remove.onclick = () => run(async () => {
    if (!confirm(`Delete the rule for /${rule.board}/?`)) return;
    await del(`/api/rules/${rule.id}`);
    await refresh();
  });
  cell(tr, "").appendChild(remove);
  return tr;
}

async function refresh() {
  const { rules } = await getJSON("/api/rules");
  tbody.replaceChildren(...rules.map(renderRule));
}

document.getElementById("add-form").onsubmit = (event) => {
  event.preventDefault();
  const board = document.getElementById("board");
  const keywords = document.getElementById("keywords");
  run(async () => {
    await postJSON("/api/rules", {
      board: board.value,
      keywords: keywords.value.split(",").map((k) => k.trim()).filter(Boolean),
    });
    board.value = "";
    keywords.value = "";
    await refresh();
  });
};

run(refresh);
