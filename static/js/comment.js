// 4chan posílá v poli `com` HTML. Nikdy ho nevkládáme přes innerHTML —
// procházíme parsovaný strom a stavíme vlastní z whitelistu značek.

const PARSER = new DOMParser();

export function quotedNumbers(html) {
  const doc = PARSER.parseFromString(html || "", "text/html");
  const out = [];
  for (const a of doc.querySelectorAll("a.quotelink")) {
    const m = /#p(\d+)/.exec(a.getAttribute("href") || "");
    if (m) out.push(Number(m[1]));
  }
  return out;
}

function convert(node, knownPosts, out) {
  if (node.nodeType === Node.TEXT_NODE) {
    out.appendChild(document.createTextNode(node.nodeValue));
    return;
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return;

  const tag = node.tagName.toLowerCase();
  const classes = node.className || "";

  if (tag === "br") {
    out.appendChild(document.createElement("br"));
    return;
  }

  if (tag === "a" && classes.includes("quotelink")) {
    const m = /#p(\d+)/.exec(node.getAttribute("href") || "");
    const target = m ? Number(m[1]) : null;
    const link = document.createElement("a");
    link.className = "quotelink";
    link.textContent = node.textContent;
    if (target !== null && knownPosts.has(target)) {
      link.href = `#p${target}`;
      link.dataset.target = String(target);
    } else {
      link.classList.add("dead");        // odkaz mimo tento thread
      link.title = "post není v tomto threadu";
    }
    out.appendChild(link);
    return;
  }

  let wrapper = null;
  if (tag === "span" && classes.includes("quote")) {
    wrapper = document.createElement("span");
    wrapper.className = "quote";
  } else if (tag === "s" || classes.includes("spoiler")) {
    wrapper = document.createElement("span");
    wrapper.className = "spoiler";
  } else if (tag === "pre") {
    wrapper = document.createElement("pre");
  } else if (tag === "b" || tag === "strong" || tag === "i" || tag === "em"
             || tag === "u") {
    wrapper = document.createElement(tag);
  }

  const sink = wrapper || out;
  for (const child of node.childNodes) convert(child, knownPosts, sink);
  if (wrapper) out.appendChild(wrapper);
}

export function renderComment(html, knownPosts) {
  const doc = PARSER.parseFromString(html || "", "text/html");
  const fragment = document.createDocumentFragment();
  for (const child of doc.body.childNodes) convert(child, knownPosts, fragment);
  return fragment;
}
