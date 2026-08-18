async function request(method, path, body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const resp = await fetch(path, options);
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      detail = (await resp.json()).detail || detail;
    } catch (e) { /* odpověď bez JSON těla */ }
    throw new Error(detail);
  }
  return resp.status === 204 ? null : resp.json();
}

export const getJSON = (path) => request("GET", path);
export const postJSON = (path, body) => request("POST", path, body ?? {});
export const patchJSON = (path, body) => request("PATCH", path, body);
export const del = (path) => request("DELETE", path);

export function formatBytes(n) {
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = n, i = 0;
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i += 1; }
  return `${value.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export function formatTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}
