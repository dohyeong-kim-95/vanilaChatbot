// Markdown for the model's answers (Loop 36). Builds DOM nodes only: every piece of text goes in as a text node, raw
// HTML in the answer stays text, and a link is made only for http(s)/mailto. A small subset on purpose: what answers
// use (headings, lists with nesting and checkboxes, code blocks, tables, quotes, rules, bold/italic/strike/code/links).
// Emphasis closes on any non-space character, so Korean particles right after it work (`**"값"**은`), which strict
// CommonMark flanking rules refuse. Nesting is capped and every scan is bounded, so a degenerate answer (a model
// repeating "- " or "*a " for 100 KB) renders as text instead of throwing or freezing the page.
"use strict";

const MD = (() => {
  const FENCE = /^ {0,3}(`{3,}|~{3,})(.*)$/;
  const HEADING = /^ {0,3}(#{1,6})(?:[ \t](.*))?$/;
  const HR = /^ {0,3}([-*_])(\s*\1){2,}\s*$/;
  const QUOTE = /^ {0,3}> ?/;
  const ITEM = /^( *)([-*+]|\d{1,9}[.)])(\s+|$)(.*)$/;
  const TABLE_SEP = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;
  const SAFE_URL = /^(https?:\/\/|mailto:)/i;
  const MAX_DEPTH = 20, MAX_LABEL = 1000;

  const node = (tag, cls) => { const e = document.createElement(tag); if (cls) e.className = cls; return e; };
  const indent = (s) => s.match(/^ */)[0].length;
  const blank = (s) => !s.trim();
  const isTableStart = (lines, i) => i + 1 < lines.length && lines[i].includes("|") && TABLE_SEP.test(lines[i + 1])
    && lines[i + 1].includes("-") && (lines[i + 1].includes("|") || lines[i].trim().startsWith("|"));
  // a backtick fence's info string has no backtick (```bash npm i``` is inline text, not a block to the end)
  const fence = (line) => { const m = FENCE.exec(line); return m && !(m[1][0] === "`" && m[2].includes("`")) ? m : null; };
  // a list item; a year or other number from 1000 up is text ("2024. 11. 4. 기준")
  const item = (line) => { const m = ITEM.exec(line); return m && !(/^\d{4}/.test(m[2])) ? m : null; };
  // a line that ends a paragraph (answers often put a list or a table right under a sentence, with no blank line);
  // a numbered list does that only from 1, as in CommonMark
  const startsBlock = (lines, i) => {
    const m = item(lines[i]);
    return fence(lines[i]) || HEADING.test(lines[i]) || HR.test(lines[i]) || QUOTE.test(lines[i])
      || m && m[4].trim() !== "" && (!/\d/.test(m[2]) || parseInt(m[2], 10) === 1) || isTableStart(lines, i);
  };
  // the heading's text without the optional closing #s (which need a space before them: "C#" keeps its #)
  function headingText(s) {
    s = (s || "").trim();
    const t = s.replace(/#+$/, "");
    return t === "" || /\s$/.test(t) ? t.trim() : s;
  }

  function blocks(lines, out, tight, depth = 0) {
    if (depth > MAX_DEPTH) {  // past any real answer's nesting: the rest as plain lines
      lines.forEach((t, k) => { if (k) out.append(node("br")); out.append(document.createTextNode(t)); });
      return out;
    }
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (blank(line)) { i++; continue; }
      let m;
      if ((m = fence(line))) {
        const close = new RegExp(`^ {0,3}${m[1][0]}{${m[1].length},}\\s*$`), body = [];
        for (i++; i < lines.length && !close.test(lines[i]); i++) body.push(lines[i]);
        i++;  // the closing fence (an unclosed block runs to the end)
        out.append(codeBlock(body.join("\n"), m[2].trim().split(/\s+/)[0]));
      } else if ((m = HEADING.exec(line))) {
        const h = node(`h${m[1].length}`);
        inline(headingText(m[2]), h);
        out.append(h); i++;
      } else if (HR.test(line)) {
        out.append(node("hr")); i++;
      } else if (QUOTE.test(line)) {
        const body = [];
        for (; i < lines.length && QUOTE.test(lines[i]); i++) body.push(lines[i].replace(QUOTE, ""));
        const q = node("blockquote");
        blocks(body, q, false, depth + 1);
        out.append(q);
      } else if (item(line)) {
        i = list(lines, i, out, depth);
      } else if (isTableStart(lines, i)) {
        i = table(lines, i, out);
      } else {
        const para = [line.trim()];
        for (i++; i < lines.length && !blank(lines[i]) && !startsBlock(lines, i); i++) para.push(lines[i].trim());
        const p = tight ? document.createDocumentFragment() : node("p");
        para.forEach((t, k) => { if (k) p.append(node("br")); inline(t, p); });
        if (tight && out.lastChild && !["UL", "OL", "INPUT"].includes(out.lastChild.nodeName)) out.append(node("br"));
        out.append(p);
      }
    }
    return out;
  }

  function list(lines, i, out, depth) {
    const first = item(lines[i]), base = first[1].length, ordered = /\d/.test(first[2]);
    const l = node(ordered ? "ol" : "ul");
    if (ordered && parseInt(first[2], 10) !== 1) l.start = parseInt(first[2], 10);
    const items = [];
    let sawBlank = false;
    for (; i < lines.length; i++) {
      const line = lines[i], m = item(line);
      if (m && m[1].length <= base + 1 && /\d/.test(m[2]) === ordered && !HR.test(line)) {
        items.push({ col: m[1].length + m[2].length + Math.min(m[3].length || 1, 4), body: [m[4]], loose: false });
        sawBlank = false;
      } else if (blank(line)) {
        items[items.length - 1].body.push("");
        sawBlank = true;
      } else if (indent(line) > base) {
        const it = items[items.length - 1];
        if (sawBlank) it.loose = true;
        it.body.push(line.slice(Math.min(indent(line), it.col)));
        sawBlank = false;
      } else if (!sawBlank && !startsBlock(lines, i)) {
        items[items.length - 1].body.push(line.trim());  // a wrapped line of the item's text
      } else break;
    }
    for (const it of items) {
      while (it.body.length && blank(it.body[it.body.length - 1])) it.body.pop();
      const li = node("li");
      const task = /^\[([ xX])\]\s+/.exec(it.body[0]);
      if (task) {
        const c = node("input");
        c.type = "checkbox"; c.disabled = true; c.checked = task[1] !== " ";
        li.className = "task";
        li.append(c);
        it.body[0] = it.body[0].slice(task[0].length);
      }
      blocks(it.body, li, !it.loose, depth + 1);
      l.append(li);
    }
    out.append(l);
    return i;
  }

  function cells(line) {
    let s = line.trim();
    if (s.startsWith("|")) s = s.slice(1);
    if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
    const parts = [];
    let cur = "", code = false;
    for (let k = 0; k < s.length; k++) {
      const c = s[k];
      if (c === "\\" && s[k + 1] === "|") { cur += "|"; k++; }
      else if (c === "`") { code = !code; cur += c; }
      else if (c === "|" && !code) { parts.push(cur.trim()); cur = ""; }
      else cur += c;
    }
    parts.push(cur.trim());
    return parts;
  }

  function table(lines, i, out) {
    const head = cells(lines[i]);
    const align = cells(lines[i + 1]).map((c) => c.endsWith(":") ? (c.startsWith(":") ? "center" : "right") : (c.startsWith(":") ? "left" : ""));
    const wrap = node("div", "table-wrap"), t = node("table"), tr = node("tr");
    head.forEach((c, k) => { const th = node("th"); if (align[k]) th.style.textAlign = align[k]; inline(c, th); tr.append(th); });
    t.append(node("thead"));
    t.firstChild.append(tr);
    const tb = node("tbody");
    const piped = lines[i].trim().startsWith("|");  // then so must its rows (a sentence with a | after it is not one)
    for (i += 2; i < lines.length && !blank(lines[i]) && (piped ? lines[i].trim().startsWith("|") : lines[i].includes("|")); i++) {
      const row = node("tr"), cs = cells(lines[i]);
      for (let k = 0; k < head.length; k++) {
        const td = node("td");
        if (align[k]) td.style.textAlign = align[k];
        inline(cs[k] || "", td);
        row.append(td);
      }
      tb.append(row);
    }
    t.append(tb);
    wrap.append(t);
    const box = node("div", "table"), bar = node("div", "md-bar");
    bar.append(Object.assign(node("span"), { textContent: `표 · ${tb.rows.length}행` }),
               copyButton("표 복사", () => copyTable(t)));
    box.append(bar, wrap);
    out.append(box);
    return i;
  }

  // For Excel (and Sheets): the clipboard gets an HTML table, which pastes into cells, plus tab-separated text for
  // plain editors. Cells are the text as shown. Codes Excel would turn into numbers (leading zeros, more than 15
  // digits) are marked as text in the HTML.
  function copyTable(t) {
    const rows = [...t.rows].map((r) => [...r.cells].map((c) => c.textContent.replace(/\s+/g, " ").trim()));
    const tsv = rows.map((r) => r.join("\t")).join("\r\n");
    const esc = (v) => v.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    const cell = (v, tag) => /^[-+]?0\d+$|^\d{16,}$/.test(v)
      ? `<${tag} style="mso-number-format:'\\@'">${esc(v)}</${tag}>` : `<${tag}>${esc(v)}</${tag}>`;
    const body = rows.map((r, k) => `<tr>${r.map((v) => cell(v, k ? "td" : "th")).join("")}</tr>`).join("");
    const html = `<html><head><meta charset="utf-8"></head><body><table>${body}</table></body></html>`;
    if (typeof ClipboardItem === "undefined") return navigator.clipboard.writeText(tsv);
    return navigator.clipboard.write([new ClipboardItem({
      "text/html": new Blob([html], { type: "text/html" }), "text/plain": new Blob([tsv], { type: "text/plain" }) })]);
  }

  function copyButton(label, copy) {
    const b = node("button");
    b.type = "button"; b.textContent = label;
    b.onclick = async () => {
      try { await copy(); b.textContent = "복사됨"; } catch (e) { b.textContent = "실패"; }
      setTimeout(() => { b.textContent = label; }, 1500);
    };
    return b;
  }

  function codeBlock(text, lang) {
    const box = node("div", "code"), bar = node("div", "md-bar"), pre = node("pre"), code = node("code");
    bar.append(Object.assign(node("span"), { textContent: lang || "코드" }),
               copyButton("복사", () => navigator.clipboard.writeText(text)));
    code.textContent = text;
    pre.append(code);
    box.append(bar, pre);
    return box;
  }

  // Inline: `code`, **strong**, *em*, ~~del~~, [text](url), bare http(s) links, backslash escapes. Unmatched marks
  // stay as they are.
  function inline(s, out, depth = 0, inLink = false) {
    if (depth > MAX_DEPTH) { out.append(document.createTextNode(s)); return out; }
    let text = "";
    const none = {};  // mark -> a position from which that mark has no closer (keeps unmatched marks linear)
    const flush = () => { if (text) { out.append(document.createTextNode(text)); text = ""; } };
    for (let i = 0; i < s.length;) {
      const c = s[i];
      if (c === "\\" && i + 1 < s.length && /[!-/:-@[-`{-~]/.test(s[i + 1])) { text += s[i + 1]; i += 2; continue; }
      if (c === "`") {
        const run = s.slice(i).match(/^`+/)[0], end = s.indexOf(run, i + run.length);
        if (end > 0 && s[end + run.length] !== "`") {
          let body = s.slice(i + run.length, end);
          if (/^ .* $/.test(body) && body.trim()) body = body.slice(1, -1);
          flush();
          out.append(Object.assign(node("code"), { textContent: body }));
          i = end + run.length; continue;
        }
        text += run; i += run.length; continue;
      }
      if (c === "*" || c === "_" || c === "~") {
        const n = s[i + 1] !== c ? 1 : s[i + 2] === c && c !== "~" ? 3 : 2, mark = c.repeat(n);
        const tag = c === "~" ? (n === 2 ? "del" : null) : ["em", "strong", "strong"][n - 1];
        const end = tag && closing(s, i + n, mark, none);
        const wordy = c === "_" && (/[\p{L}\p{N}]/u.test(s[i - 1] || "") || /[\p{L}\p{N}]/u.test(s[end + n] || ""));
        if (end && !wordy) {
          flush();
          const e = node(tag);
          inline(s.slice(i + n, end), e, depth + 1, inLink);
          if (n === 3) { const em = node("em"); em.append(e); out.append(em); } else out.append(e);  // ***both***
          i = end + n; continue;
        }
        text += mark; i += mark.length; continue;
      }
      if (c === "[" && !inLink) {
        const m = link(s, i);
        if (m) {
          flush();
          if (SAFE_URL.test(m.url)) {
            const a = anchor(m.url);
            inline(m.label, a, depth + 1, true);
            out.append(a);
          } else inline(m.label, out, depth + 1, inLink);  // javascript: and the like: the words only
          i = m.end; continue;
        }
      }
      if ((c === "h" || c === "H") && !inLink && !/[\p{L}\p{N}]/u.test(s[i - 1] || "")) {
        // up to the first space or non-ASCII letter: "https://a.com/경로에서" links https://a.com/ only
        const m = /^https?:\/\/[!#-;=?-~]+/i.exec(s.slice(i, i + 2048));
        if (m) {
          let url = m[0].replace(/[.,;:!?'"*_~]+$/, "");
          while (url.endsWith(")") && (url.match(/\(/g) || []).length < (url.match(/\)/g) || []).length) url = url.slice(0, -1);
          flush();
          out.append(Object.assign(anchor(url), { textContent: url }));
          i += url.length; continue;
        }
      }
      text += c; i++;
    }
    flush();
    return out;
  }

  // where `mark` closes: not right after a space, with something in between, skipping code spans. A search that
  // found nothing from `from` finds nothing from any later place either: remembered in `none`.
  function closing(s, from, mark, none) {
    if (from >= s.length || /\s/.test(s[from]) || from >= (none[mark] ?? Infinity)) return 0;
    const one = mark.length === 1;
    for (let k = from + 1; k < s.length; k++) {
      if (s[k] === "\\") { k++; continue; }
      if (s[k] === "`") { const e = s.indexOf("`", k + 1); if (e < 0) break; k = e; continue; }
      if (s.startsWith(mark, k) && !/\s/.test(s[k - 1]) && (!one || s[k + 1] !== mark && s[k - 1] !== mark)) return k;
    }
    none[mark] = Math.min(none[mark] ?? Infinity, from);
    return 0;
  }

  function link(s, i) {
    let depth = 0, k = i;
    for (const stop = Math.min(s.length, i + MAX_LABEL); k < stop; k++) {
      if (s[k] === "\\") { k++; continue; }
      if (s[k] === "[") depth++;
      else if (s[k] === "]" && --depth === 0) break;
    }
    if (k >= s.length || s[k] !== "]" || s[k + 1] !== "(") return null;
    const m = /^\(\s*<?([^\s()<>]*(?:\([^\s()]*\)[^\s()<>]*)*)>?(?:\s+("[^"]*"|'[^']*'))?\s*\)/.exec(s.slice(k + 1));
    return m ? { label: s.slice(i + 1, k), url: m[1], end: k + 1 + m[0].length } : null;
  }

  function anchor(url) {
    const a = node("a");
    a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer";
    return a;
  }

  return { render: (src) => blocks(String(src).replace(/\r\n?/g, "\n").replace(/\t/g, "    ").split("\n"), document.createDocumentFragment()) };
})();
