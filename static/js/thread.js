import { appUrl, del, formatBytes, postJSON } from "./api.js";
import { quotedNumbers, renderComment } from "./comment.js";

const params = new URLSearchParams(location.search);
const board = params.get("b");
const no = Number(params.get("no"));
const postsEl = document.getElementById("posts");
const previewEl = document.getElementById("preview");

const VIDEO = new Set([".webm", ".mp4"]);

// Archiv boardu je pseudo-thread s číslem 0. Nula proto, že skutečná 4chan ID
// jsou velká čísla, takže projde všude, kde se číslo threadu očekává.
const ARCHIVE_NO = 0;
const viewingArchive = no === ARCHIVE_NO;

function postAction(post) {
  const wrap = document.createElement("span");
  wrap.className = "post-action";
  const button = document.createElement("button");
  button.textContent = viewingArchive ? "Remove" : "Archive";
  button.title = viewingArchive
    ? "remove this post from the board archive"
    : "keep a copy of this post, with its media, in the board archive";
  const say = (text, ok) => {
    const note = document.createElement("span");
    note.className = ok ? "post-action-ok" : "post-action-err";
    note.textContent = text;
    wrap.replaceChildren(note);
  };
  button.onclick = async () => {
    button.disabled = true;
    try {
      if (viewingArchive) {
        await del(`/api/archive/${board}/${post.no}`);
        document.getElementById(`p${post.no}`)?.remove();
        return;
      }
      await postJSON(`/api/archive/${board}`, { thread_no: no, post_no: post.no });
      say("archived", true);
    } catch (err) {
      say(err.message, false);
    }
  };
  wrap.appendChild(button);
  return wrap;
}

function mediaBase(post) {
  return appUrl(`archive/${board}/${no}/${post.tim}`);
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
      // Vlastní ovládání videa spolkne klik i dvojklik, takže bez tohohle
      // odkazu není přehrané video jak zavřít — stejně jako Close na 4chanu.
      const close = document.createElement("a");
      close.className = "media-close";
      close.href = "#";
      close.textContent = "Close";
      close.onclick = (e) => {
        e.preventDefault();
        video.pause();
        box.replaceChildren(thumb);
      };
      box.replaceChildren(close, video);
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
  header.appendChild(postAction(post));
  el.appendChild(header);

  if (viewingArchive && post._source_thread) {
    const origin = document.createElement("div");
    origin.className = "post-origin";
    origin.append("From ");
    const link = document.createElement("a");
    link.href = appUrl(`thread.html?b=${encodeURIComponent(board)}&no=${post._source_thread}`);
    link.textContent = `/${board}/${post._source_thread}`;
    origin.appendChild(link);
    if (post._source_subject) origin.append(` — ${post._source_subject}`);
    el.appendChild(origin);
  }

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
    const resp = await fetch(appUrl(`archive/${board}/${no}/thread.json`), { cache: "no-cache" });
    if (resp.status === 404 && viewingArchive) {
      document.getElementById("title").textContent = `Archived posts from /${board}/ (0)`;
      document.getElementById("error").textContent =
        "Nothing archived from this board yet. Use the Archive button on any post.";
      return;
    }
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

  if (viewingArchive) {
    document.title = `/${board}/ archive — 4chan archive`;
    document.getElementById("title").textContent =
      `Archived posts from /${board}/ (${posts.length})`;
  } else {
    const subject = posts[0]?.sub || `/${board}/ ${no}`;
    document.title = `${subject} — 4chan archive`;
    document.getElementById("title").textContent =
      `${subject} (/${board}/${no}, ${posts.length} posts${doc.status === "dead" ? ", deleted" : ""})`;
    const nav = document.querySelector("nav");
    const link = document.createElement("a");
    link.href = appUrl(`thread.html?b=${encodeURIComponent(board)}&no=${ARCHIVE_NO}`);
    link.textContent = `/${board}/ archive`;
    nav.append(" · ", link);
  }

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
