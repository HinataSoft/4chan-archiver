import { del, formatTime, getJSON, patchJSON, postJSON } from "./api.js";

const tbody = document.getElementById("rules");
const addError = document.getElementById("add-error");

function cell(row, text) {
  const td = document.createElement("td");
  td.textContent = text;
  row.appendChild(td);
  return td;
}

function renderRule(rule) {
  const tr = document.createElement("tr");
  cell(tr, rule.board);

  const keywords = cell(tr, "");
  const input = document.createElement("input");
  input.type = "text";
  input.value = rule.keywords.join(", ");
  input.onchange = async () => {
    const list = input.value.split(",").map((k) => k.trim()).filter(Boolean);
    try {
      await patchJSON(`/api/rules/${rule.id}`, { keywords: list });
    } catch (err) {
      alert(err.message);
    }
    await refresh();
  };
  keywords.appendChild(input);

  const toggle = document.createElement("input");
  toggle.type = "checkbox";
  toggle.checked = rule.enabled;
  toggle.onchange = async () => {
    await patchJSON(`/api/rules/${rule.id}`, { enabled: toggle.checked });
    await refresh();
  };
  cell(tr, "").appendChild(toggle);

  cell(tr, formatTime(rule.last_scan_at));
  cell(tr, rule.last_error || "—");

  const remove = document.createElement("button");
  remove.textContent = "Smazat";
  remove.onclick = async () => {
    if (!confirm(`Smazat pravidlo pro /${rule.board}/?`)) return;
    await del(`/api/rules/${rule.id}`);
    await refresh();
  };
  cell(tr, "").appendChild(remove);
  return tr;
}

async function refresh() {
  const { rules } = await getJSON("/api/rules");
  tbody.replaceChildren(...rules.map(renderRule));
}

document.getElementById("add-form").onsubmit = async (event) => {
  event.preventDefault();
  addError.textContent = "";
  const board = document.getElementById("board");
  const keywords = document.getElementById("keywords");
  try {
    await postJSON("/api/rules", {
      board: board.value,
      keywords: keywords.value.split(",").map((k) => k.trim()).filter(Boolean),
    });
    board.value = "";
    keywords.value = "";
    await refresh();
  } catch (err) {
    addError.textContent = err.message;
  }
};

refresh();
