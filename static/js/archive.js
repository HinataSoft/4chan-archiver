import { appUrl, getJSON } from "./api.js";

const tbody = document.getElementById("boards");
const errorEl = document.getElementById("error");

// Stejné číslo jako ARCHIVE_NO v thread.js — archiv boardu je pseudo-thread 0.
const ARCHIVE_NO = 0;

function renderBoard(entry) {
  const tr = document.createElement("tr");

  const boardCell = document.createElement("td");
  boardCell.dataset.label = "Board";
  const link = document.createElement("a");
  link.href = appUrl(`thread.html?b=${encodeURIComponent(entry.board)}&no=${ARCHIVE_NO}`);
  link.textContent = `/${entry.board}/`;
  boardCell.appendChild(link);
  tr.appendChild(boardCell);

  const countCell = document.createElement("td");
  countCell.dataset.label = "Posts";
  countCell.textContent = entry.posts;
  tr.appendChild(countCell);
  return tr;
}

async function refresh() {
  errorEl.textContent = "";
  try {
    const { boards } = await getJSON("/api/archive");
    tbody.replaceChildren(...boards.map(renderBoard));
    if (!boards.length) {
      errorEl.textContent = "Nothing archived yet. Open a thread and use the Archive button on a post.";
    }
  } catch (err) {
    errorEl.textContent = err.message;
  }
}

refresh();
