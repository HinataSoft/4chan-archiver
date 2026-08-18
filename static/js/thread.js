import { formatBytes } from "./api.js";
import { quotedNumbers, renderComment } from "./comment.js";

const params = new URLSearchParams(location.search);
const board = params.get("b");
const no = Number(params.get("no"));
const postsEl = document.getElementById("posts");
const previewEl = document.getElementById("preview");

const VIDEO = new Set([".webm", ".mp4"]);

function mediaBase(post) {
  return `/archive/${board}/${no}/${post.tim}`;
}

function fileInfo(post) {
  const name = `${post.filename ?? "file"}${post.ext}`;
  const size = post.fsize ? formatBytes(post.fsize) : "?";
  const dims = post.w && post.h ? `, ${post.w}x${post.h}` : "";
  const info = document.createElement("div");
  info.className = "file-info";
  info.append("File: ");
  const link = document.createElement("a");
  link.href = `${mediaBase(post)}${post.ext}`;
  link.download = name;
  link.textContent = name;
  info.appendChild(link);
  info.append(` (${size}${dims})`);
  return info;
}

function renderMedia(post, mediaState) {
  const box = document.createElement("div");
  box.className = "media";
  const state = mediaState[String(post.tim)] || {};
  if (state.file === "failed" && state.thumb === "failed") {
    box.textContent = "[media could not be downloaded]";
    return box;
  }

  const thumb = document.createElement("img");
  thumb.className = "thumb";
  thumb.src = `${mediaBase(post)}s.jpg`;
  thumb.alt = post.filename || "";
  thumb.loading = "lazy";
  box.appendChild(thumb);

  if (state.file === "failed") return box;   // originál nemáme, expandovat není co

  thumb.onclick = () => {
    if (VIDEO.has(post.ext)) {
      const video = document.createElement("video");
      video.src = `${mediaBase(post)}${post.ext}`;
      video.controls = true;
      video.loop = true;
      video.autoplay = true;
      video.onclick = (e) => { e.stopPropagation(); };
      video.ondblclick = () => box.replaceChildren(thumb);
      box.replaceChildren(video);
    } else {
      const full = document.createElement("img");
      full.src = `${mediaBase(post)}${post.ext}`;
      full.onclick = () => box.replaceChildren(thumb);
      box.replaceChildren(full);
    }
  };
  return box;
}

function renderPost(post, index, knownPosts, backlinks, mediaState) {
  const el = document.createElement("div");
  el.className = index === 0 ? "post op" : "post";
  if (post._deleted) el.classList.add("deleted");
  el.id = `p${post.no}`;

  const header = document.createElement("div");
  header.className = "post-header";
  if (post.sub) {
    const sub = document.createElement("span");
    sub.className = "post-subject";
    sub.textContent = post.sub;
    header.append(sub, " ");
  }
  const name = document.createElement("span");
  name.className = "post-name";
  name.textContent = post.name || "Anonymous";
  header.append(name, " ");
  header.append(new Date((post.time || 0) * 1000).toLocaleString(), " ");
  const number = document.createElement("span");
  number.className = "post-no";
  number.textContent = `No.${post.no}`;
  header.appendChild(number);
  if (post._deleted) {
    const note = document.createElement("span");
    note.className = "deleted-note";
    note.textContent = "[deleted by moderator]";
    header.appendChild(note);
  }
  el.appendChild(header);

  const replies = backlinks.get(post.no);
  if (replies && replies.length) {
    const box = document.createElement("div");
    box.className = "backlinks";
    box.append("Replies: ");
    for (const target of replies) {
      const link = document.createElement("a");
      link.href = `#p${target}`;
      link.dataset.target = String(target);
      link.textContent = `>>${target}`;
      box.appendChild(link);
    }
    el.appendChild(box);
  }

  // Soubor smazaný moderátorem zůstává v archivu — bajty na disku většinou jsou
  // z dřívějšího pollu a často jsou právě tím, kvůli čemu se thread archivoval.
  // Vykreslíme ho tedy stejně jako _deleted post: viditelně označený, ne tiše
  // zmizelý. Když ho archiv nemá, chování zůstává původní (nic).
  const archived = mediaState[String(post.tim)]?.file === "ok";
  if (post.tim && post.ext && (!post.filedeleted || archived)) {
    const info = fileInfo(post);
    if (post.filedeleted) {
      const note = document.createElement("span");
      note.className = "deleted-note";
      note.textContent = "[file deleted by moderator]";
      info.appendChild(note);
    }
    el.appendChild(info);
    const box = renderMedia(post, mediaState);
    if (post.filedeleted) box.classList.add("file-deleted");
    el.appendChild(box);
  }

  const comment = document.createElement("blockquote");
  comment.className = "comment";
  comment.appendChild(renderComment(post.com || "", knownPosts));
  el.appendChild(comment);
  return el;
}

// Zobrazí se místo postu, který se nepodařilo vykreslit (viz try/catch v main),
// aby chyba u jednoho postu neshodila celý thread.
function renderFailedPost(post) {
  const el = document.createElement("div");
  el.className = "post render-failed";
  if (post && post.no != null) el.id = `p${post.no}`;
  el.textContent = `[post ${post && post.no != null ? post.no : "?"} could not be rendered]`;
  return el;
}

function flash(target) {
  target.classList.add("flash");
  setTimeout(() => target.classList.remove("flash"), 1200);
}

function wireQuoteLinks() {
  postsEl.addEventListener("click", (event) => {
    const link = event.target.closest("a[data-target]");
    if (!link) return;
    const target = document.getElementById(`p${link.dataset.target}`);
    if (!target) return;
    event.preventDefault();
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    flash(target);
  });

  postsEl.addEventListener("mouseover", (event) => {
    const link = event.target.closest("a[data-target]");
    if (!link) return;
    const source = document.getElementById(`p${link.dataset.target}`);
    if (!source) return;
    previewEl.replaceChildren(source.cloneNode(true));
    previewEl.hidden = false;
    const rect = link.getBoundingClientRect();
    previewEl.style.left = `${window.scrollX + rect.left}px`;
    previewEl.style.top = `${window.scrollY + rect.bottom + 4}px`;
  });

  postsEl.addEventListener("mouseout", (event) => {
    if (event.target.closest("a[data-target]")) previewEl.hidden = true;
  });
}

async function main() {
  if (!board || !Number.isInteger(no)) {
    document.getElementById("error").textContent = "missing ?b= and ?no= parameters";
    return;
  }
  let doc;
  try {
    const resp = await fetch(`/archive/${board}/${no}/thread.json`, { cache: "no-cache" });
    if (!resp.ok) throw new Error(`thread is not in the archive (HTTP ${resp.status})`);
    doc = await resp.json();
  } catch (err) {
    document.getElementById("error").textContent = err.message;
    return;
  }

  const posts = doc.posts || [];
  const knownPosts = new Set(posts.map((p) => p.no));
  const backlinks = new Map();
  for (const post of posts) {
    for (const target of quotedNumbers(post.com || "")) {
      if (!knownPosts.has(target)) continue;
      if (!backlinks.has(target)) backlinks.set(target, []);
      backlinks.get(target).push(post.no);
    }
  }

  const subject = posts[0]?.sub || `/${board}/ ${no}`;
  document.title = `${subject} — 4chan archive`;
  document.getElementById("title").textContent =
    `${subject} (/${board}/${no}, ${posts.length} posts${doc.status === "dead" ? ", deleted" : ""})`;

  const mediaState = doc.media || {};
  const rendered = posts.map((p, i) => {
    try {
      return renderPost(p, i, knownPosts, backlinks, mediaState);
    } catch (err) {
      console.error(`failed to render post ${p.no}`, err);
      return renderFailedPost(p);
    }
  });
  postsEl.replaceChildren(...rendered);
  wireQuoteLinks();

  if (location.hash) {
    const target = document.getElementById(location.hash.slice(1));
    if (target) { target.scrollIntoView({ block: "center" }); flash(target); }
  }
}

main();
