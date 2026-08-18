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

const parseKeywords = (value) =>
  value.split(",").map((k) => k.trim()).filter(Boolean);

// Editovatelná klíčová slova s výslovným tlačítkem Save. Ukládání při
// opuštění pole nedávalo nijak najevo, že se něco stalo — tady je tlačítko
// aktivní jen když jsou skutečně neuložené změny, takže je na řádku vidět,
// že něco čeká, a po uložení to potvrdí.
function keywordEditor(rule) {
  const box = document.createElement("div");
  box.className = "kw-editor";

  const input = document.createElement("input");
  input.type = "text";
  input.className = "kw-input";
  input.value = rule.keywords.join(", ");
  input.setAttribute("aria-label", `Keywords for /${rule.board}/`);

  const save = document.createElement("button");
  save.textContent = "Save";
  save.className = "kw-save";

  const status = document.createElement("span");
  status.className = "kw-status";

  // Porovnává rozparsované seznamy, aby "rust, zig" a "rust,zig " platily
  // za shodné a tlačítko nesvítilo kvůli mezerám.
  const changed = () =>
    JSON.stringify(parseKeywords(input.value)) !== JSON.stringify(rule.keywords);
  const sync = () => {
    const ok = changed() && parseKeywords(input.value).length > 0;
    save.disabled = !ok;
    box.classList.toggle("dirty", ok);
  };

  const commit = () => run(async () => {
    const list = parseKeywords(input.value);
    try {
      await patchJSON(`/api/rules/${rule.id}`, { keywords: list });
    } catch (err) {
      // Chyba už je vidět v hlášce nahoře; vrátíme pole na stav serveru.
      await refresh();
      throw err;
    }
    rule.keywords = list;
    input.value = list.join(", ");
    sync();
    status.textContent = "saved";
    status.classList.add("show");
    setTimeout(() => status.classList.remove("show"), 1500);
  });

  input.oninput = sync;
  input.onkeydown = (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    if (!save.disabled) commit();
  };
  save.onclick = commit;

  sync();
  box.append(input, save, status);
  return box;
}

function renderRule(rule) {
  const tr = document.createElement("tr");
  cell(tr, rule.board, "Board");

  const keywords = cell(tr, "", "Keywords");
  keywords.appendChild(keywordEditor(rule));

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
