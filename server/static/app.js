// Minimal chat UI (Loop 14). Model/tool text is only ever inserted as text (never innerHTML); artifact content
// runs in a sandboxed iframe on the renderer's origin (see server/render.py).
"use strict";

const $ = (id) => document.getElementById(id);
const state = { user: "", project: "", session: "", artifact: null, streaming: false, follow: null,
                files: [], folder: "", closed: new Set() };  // folder: where uploads and new folders go ("" = top)

// One followed run at a time: switching conversation/project stops the old stream (else its events land here).
function stopFollow() { state.follow?.abort(); state.follow = null; }

function store(key, value) {
  try { value === undefined ? (value = localStorage.getItem(key)) : localStorage.setItem(key, value); } catch (e) { value = null; }
  return value;
}

// The login cookie (HttpOnly) goes along by itself; a 401 means it expired or was revoked: back to the login form.
async function api(path, opts = {}) {
  const r = await fetch(path, { ...opts, headers: { "Content-Type": "application/json", ...(opts.headers || {}) } });
  if (r.status === 401 && path !== "/login") showLogin();
  if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 200)}`);
  return r.headers.get("content-type")?.includes("json") ? r.json() : r;
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function scrollDown() { const m = $("messages"); m.scrollTop = m.scrollHeight; }

function addMsg(role, text) {
  $("messages").append(el("div", `msg ${role}`, text));
  scrollDown();
}

function addArtifactRef(id, version, title) {
  const b = el("button", "artifact-ref");
  b.append(el("span", null, title || id), el("span", "meta", version ? `v${version}` : "아티팩트"));
  b.onclick = () => openArtifact(id, version);
  $("messages").append(b);
  scrollDown();
}

// ---- sidebar ------------------------------------------------------------------------------------
async function loadProjects() {
  const sel = $("project");
  sel.replaceChildren();
  if (!state.user) return;
  const { projects } = await api("/projects");
  for (const p of projects) sel.append(new Option(p, p));
  state.project = projects.includes(store("project")) ? store("project") : (projects[0] || "");
  sel.value = state.project;
  await loadProject();
}

async function loadProject() {
  $("art-close").onclick();  // an artifact of the previous project must not stay open
  $("sessions").replaceChildren();
  $("artifacts").replaceChildren();
  $("messages").replaceChildren();
  $("files").replaceChildren();
  stopFollow();
  state.session = "";
  state.files = [];
  state.folder = "";
  state.closed.clear();
  if (!state.project) return;
  store("project", state.project);
  const { sessions } = await api(`/projects/${state.project}/sessions`);
  for (const s of sessions) {
    const li = el("li", null, s);
    li.onclick = () => openSession(s);
    li.dataset.id = s;
    $("sessions").append(li);
  }
  await Promise.all([loadArtifacts(), loadFiles()]);
  if (sessions.length) await openSession(sessions[0]);
}

async function loadArtifacts() {
  const { artifacts } = await api(`/projects/${state.project}/artifacts`);
  const ul = $("artifacts");
  ul.replaceChildren();
  for (const a of artifacts) {
    const li = el("li", null, a.title);
    li.append(el("span", "meta", `v${a.version}`));
    li.title = `${a.id} (${a.type})`;
    li.dataset.id = a.id;
    li.onclick = () => openArtifact(a.id);
    ul.append(li);
  }
  markActive();
}

function markActive() {
  for (const li of $("sessions").children) li.classList.toggle("active", li.dataset.id === state.session);
  for (const li of $("artifacts").children) li.classList.toggle("active", li.dataset.id === state.artifact?.id);
}

async function openSession(sid) {
  stopFollow();
  state.session = sid;
  markActive();
  const s = await api(`/sessions/${sid}`);
  if (state.session !== sid) return;  // the user already switched again; a late answer must not paint over it
  $("messages").replaceChildren();
  for (const m of s.messages) m.role === "artifact" ? addArtifactRef(m.id) : addMsg(m.role, m.content);
  // History already has what was said: draw only the card that is waiting now, follow only newer events
  // (replaying from 0 duplicated messages and redrew answered cards with live buttons).
  const r = s.active_run;
  if (r?.pending) (r.pending.type === "question" ? showQuestion : showApproval)(r.run_id, r.pending, r.seq);
  else if (r) follow(r.run_id, r.seq);
}

// ---- files: the user's own folders. The model is shown the same tree with every message (agent/userstore.py), so
// "the files in the 카드 folder" means what is drawn here. Names come from the user: textContent only.
const MAX_FILE = 5 * 1024 * 1024;  // agent/userstore.py MAX_UPLOAD
const parentOf = (p) => p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "";
const baseName = (p) => p.slice(p.lastIndexOf("/") + 1);
const joinPath = (dir, name) => dir ? `${dir}/${name}` : name;
const fileUrl = (project, path) => `/projects/${project}/files?path=${encodeURIComponent(path)}`;
const fmtSize = (n) => n < 1024 ? `${n} B` : n < 1024 ** 2 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1024 ** 2).toFixed(1)} MB`;

function fileError(e) {
  const s = String(e.message || e);
  if (s.startsWith("409")) return "같은 이름이 이미 있어요";
  if (s.startsWith("413")) return "너무 커요 (파일 5MB, 프로젝트 전체 200MB·2,000개까지)";
  if (s.startsWith("422")) return "쓸 수 없는 이름이에요 (. - 공백으로 시작하거나 \\ 가 들어간 이름, 8단계보다 깊은 폴더)";
  if (s.startsWith("404")) return "이미 없어요";
  return s;
}

async function loadFiles() {
  const project = state.project;
  const { files } = await api(`/projects/${project}/files`);
  if (project !== state.project) return;  // switched meanwhile
  state.files = files;
  if (!files.some((f) => f.type === "folder" && f.path === state.folder)) state.folder = "";
  drawFiles();
}

function drawFiles() {
  const ul = $("files");
  const focused = document.activeElement?.closest?.("#files li")?.dataset.path;  // keyboard users keep their place
  ul.replaceChildren(...ul.querySelectorAll(".busy"));  // an upload still running keeps its progress line
  const kids = new Map();
  for (const f of state.files) {
    const p = parentOf(f.path);
    if (!kids.has(p)) kids.set(p, []);
    kids.get(p).push(f);
  }
  const order = (a, b) => (a.type === b.type ? 0 : a.type === "folder" ? -1 : 1) || baseName(a.path).localeCompare(baseName(b.path), "ko");
  const walk = (dir, depth) => {
    for (const f of (kids.get(dir) || []).sort(order)) {
      ul.append(fileRow(f, depth));
      if (f.type === "folder" && !state.closed.has(f.path)) walk(f.path, depth + 1);
    }
  };
  if (state.files.length) {  // the top as a row of its own: select it, drop on it (with only folders there is no other spot)
    const top = el("li", "top");
    top.append(el("span", "twist", ""), el("span", "name", "맨 위"));
    top.title = "맨 위에 올리기·만들기·옮기기";
    top.classList.toggle("active", state.folder === "");
    top.tabIndex = 0;
    top.dataset.path = "";
    top.onclick = (e) => { e.stopPropagation(); state.folder = ""; drawFiles(); };
    top.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); top.click(); } };
    dropTarget(top, "");
    ul.append(top);
  }
  walk("", 0);
  if (!state.files.length) {
    ul.append(el("li", "empty", "파일을 여기에 끌어 놓거나 [올리기]를 누르세요. 폴더로 정리해 두면 \"카드 폴더의 9월 파일 참고해서…\"처럼 물을 수 있어요."));
  }
  $("file-upload").title = `${state.folder ? state.folder + "/" : "맨 위"}에 파일 올리기`;
  $("file-folder").title = `${state.folder ? state.folder + "/" : "맨 위"} 안에 새 폴더`;
  if (focused !== undefined) [...ul.children].find((li) => li.dataset.path === focused)?.focus();
}

function fileRow(f, depth) {
  const folder = f.type === "folder";
  const li = el("li");
  li.style.paddingLeft = `${8 + depth * 14}px`;
  li.title = folder ? `${f.path}/` : `${f.path} (${fmtSize(f.size)})`;
  li.classList.toggle("active", folder && f.path === state.folder);
  const empty = folder && !state.files.some((g) => g.path.startsWith(f.path + "/"));
  li.append(el("span", "twist", folder && !empty ? (state.closed.has(f.path) ? "▸" : "▾") : ""),
            el("span", "name", baseName(f.path) + (folder ? "/" : "")));
  if (empty) li.append(el("span", "meta", "비어 있음"));
  const acts = el("span", "acts");
  const ren = el("button", null, "이름");
  ren.title = "이름 바꾸기 (/은행/9월.csv 처럼 /로 시작하는 경로를 쓰면 그 폴더로 옮겨져요)";
  ren.onclick = (e) => { e.stopPropagation(); renameFile(f.path); };
  const del = el("button", null, "삭제");
  del.onclick = (e) => { e.stopPropagation(); deleteFile(f); };
  acts.append(ren, del);
  li.append(acts);
  li.onclick = (e) => {  // a folder: select it (where uploads and new folders go); again: fold or unfold
    e.stopPropagation();
    if (!folder) return;
    if (state.folder !== f.path) state.folder = f.path;
    else if (!state.closed.delete(f.path)) state.closed.add(f.path);
    drawFiles();
  };
  li.dataset.path = f.path;
  li.tabIndex = 0;  // keyboard: Tab to a row shows its buttons, Enter selects/folds a folder
  li.onkeydown = (e) => { if (e.target === li && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); li.click(); } };
  li.draggable = true;
  li.ondragstart = (e) => { e.dataTransfer.setData("application/x-vanila-path", f.path); e.dataTransfer.effectAllowed = "move"; };
  dropTarget(li, folder ? f.path : parentOf(f.path));  // on a file: into the folder it is in
  return li;
}

// A row of ours dropped on a folder moves it; files or folders from the computer dropped there are uploaded into it.
function dropTarget(node, dir) {
  node.ondragover = (e) => { e.preventDefault(); e.stopPropagation(); node.classList.add("drop"); };
  node.ondragleave = (e) => { if (!node.contains(e.relatedTarget)) node.classList.remove("drop"); };
  node.ondrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    node.classList.remove("drop");
    const src = e.dataTransfer.getData("application/x-vanila-path");
    if (src) return moveFile(src, joinPath(dir, baseName(src)));
    // entries must be taken now: the browser empties the drop's item list once this handler returns
    const entries = [...e.dataTransfer.items].map((i) => i.webkitGetAsEntry?.()).filter(Boolean);
    if (entries.length) dropped(entries, dir);
    else uploadFiles(state.project, [...e.dataTransfer.files].map((f) => [joinPath(dir, f.name), f]));
  };
}

async function dropped(entries, dir) {
  const project = state.project;
  const files = [], folders = [];
  const read = (reader) => new Promise((ok, no) => reader.readEntries(ok, no));
  const walk = async (entry, at) => {
    if (entry.name.startsWith(".")) return;  // .DS_Store, .git: hidden names are not accepted
    const path = joinPath(at, entry.name);
    if (entry.isFile) {
      files.push([path, await new Promise((ok, no) => entry.file(ok, no))]);
    } else if (entry.isDirectory) {
      folders.push(path);
      const reader = entry.createReader();
      for (let batch; (batch = await read(reader)).length;) for (const c of batch) await walk(c, path);
    }
  };
  try {
    for (const e of entries) await walk(e, dir);
  } catch (e) {
    addMsg("error", `끌어 놓은 파일을 읽지 못했어요: ${e}`);
    return;
  }
  await uploadFiles(project, files, folders);
}

// One at a time, into the project it was started in (switching projects meanwhile must not move the rest).
async function uploadFiles(project, files, folders = []) {
  const errs = [];
  const busy = el("li", "busy");
  $("files").prepend(busy);
  for (const path of folders) {  // empty ones too; existing folders are fine
    try { await api(`/projects/${project}/folders`, { method: "POST", body: JSON.stringify({ path }) }); }
    catch (e) { errs.push(`${path}/: ${fileError(e)}`); }
  }
  let i = 0;
  for (const [path, file] of files) {
    busy.textContent = `올리는 중… ${++i}/${files.length}`;
    if (file.size > MAX_FILE) { errs.push(`${path}: ${fileError("413")}`); continue; }
    try {
      await api(fileUrl(project, path), { method: "PUT", body: file, headers: { "Content-Type": "application/octet-stream" } });
    } catch (e) {
      errs.push(`${path}: ${fileError(e)}`);
    }
  }
  busy.remove();
  if (errs.length) addMsg("error", `올리지 못했어요\n${errs.join("\n")}`);
  if (project === state.project) await loadFiles();
}

async function moveFile(src, dst) {
  if (!dst || dst === src || dst.startsWith(src + "/")) return;  // dropped on itself or into itself
  try {
    await api(`/projects/${state.project}/files/move`, { method: "POST", body: JSON.stringify({ src, dst }) });
    if (state.folder === src || state.folder.startsWith(src + "/")) state.folder = dst + state.folder.slice(src.length);
  } catch (e) {
    addMsg("error", `${src}: 옮기지 못했어요. ${fileError(e)}`);
  }
  await loadFiles();
}

// A plain name stays in the same folder; a path starting with / is from the top (that is how to move elsewhere).
function renameFile(path) {
  const dst = prompt("새 이름 (다른 폴더로 옮기려면 /로 시작하는 경로: /은행/9월.csv)", baseName(path))?.trim();
  if (!dst) return;
  moveFile(path, (dst.startsWith("/") ? dst : joinPath(parentOf(path), dst)).replace(/^\/+|\/+$/g, ""));
}

async function deleteFile(f) {
  const inside = state.files.filter((g) => g.path.startsWith(f.path + "/")).length;
  const what = f.type === "folder" ? `${f.path}/ 폴더${inside ? `와 그 안의 ${inside}개` : ""}` : f.path;
  if (!confirm(`${what}를 지울까요? 되돌릴 수 없어요.`)) return;
  try {
    await api(fileUrl(state.project, f.path), { method: "DELETE" });
  } catch (e) {
    addMsg("error", `${f.path}: 지우지 못했어요. ${fileError(e)}`);
  }
  await loadFiles();
}

// ---- chat -----------------------------------------------------------------------------------------
async function send(text) {
  if (!state.session) {
    const { session_id } = await api("/sessions", { method: "POST", body: JSON.stringify({ project_id: state.project }) });
    await loadProject();
    await openSession(session_id);
  }
  addMsg("user", text);
  const { run_id } = await api(`/sessions/${state.session}/messages`, { method: "POST", body: JSON.stringify({ content: text }) });
  await follow(run_id);
}

// SSE over fetch (EventSource cannot send Last-Event-ID on the first request). Resumes with Last-Event-ID.
async function follow(rid, after = 0) {
  stopFollow();
  const ctl = state.follow = new AbortController();
  state.streaming = true;
  $("send").disabled = true;
  const status = el("div", "tool", "작업 중…");
  $("messages").append(status);
  try {
    const r = await api(`/runs/${rid}/events`, { headers: { "Last-Event-ID": String(after) }, signal: ctl.signal });
    const reader = r.body.pipeThrough(new TextDecoderStream()).getReader();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += value;
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, i);
        buf = buf.slice(i + 2);
        const id = block.match(/^id: (\d+)/m);
        const data = block.match(/^data: (.*)$/m);
        if (id) after = Number(id[1]);
        if (data) onEvent(rid, JSON.parse(data[1]), after, status);
      }
    }
  } catch (e) {
    if (e.name !== "AbortError") addMsg("error", String(e));
  } finally {
    status.remove();
    if (state.follow === ctl) {
      state.follow = null;
      state.streaming = false;
      $("send").disabled = false;
    }
  }
}

function onEvent(rid, ev, seq, status) {
  switch (ev.type) {
    case "text": case "final":
      if (ev.content) addMsg("assistant", ev.content);
      break;
    case "tool_call": {
      let name = ev.name;
      try { if (ev.name === "bash") name += ": " + JSON.parse(ev.arguments).command.split("\n")[0].slice(0, 80); } catch (e) {}
      status.textContent = name;
      break;
    }
    case "effort":
      status.textContent = ev.reason === "correction" ? "다시 꼼꼼히 확인하는 중…" : "오류가 이어져 더 깊게 생각하는 중…";
      break;
    case "agent_progress":
      status.textContent = `${ev.agent}: ${ev.message}`;
      break;
    case "artifact":
      addArtifactRef(ev.id, ev.version, ev.title);
      loadArtifacts();
      openArtifact(ev.id, ev.version);
      break;
    case "approval_request": showApproval(rid, ev, seq); break;
    case "question": showQuestion(rid, ev, seq); break;
    case "warning": case "error": addMsg("error", ev.message); break;
  }
}

function showApproval(rid, ev, seq) {
  const box = el("div", "approval");
  box.append(el("div", null, `승인 필요: ${ev.tool}`), el("pre", null, JSON.stringify(ev.arguments, null, 2)));
  const row = el("div", "row");
  const yes = el("button", "primary", "승인");
  const no = el("button", null, "거절");
  const decide = async (approved) => {
    yes.disabled = no.disabled = true;
    await api(`/runs/${rid}/approval`, { method: "POST", body: JSON.stringify({ call_id: ev.call_id, approved }) });
    box.replaceChildren(el("div", "tool", `${ev.tool}: ${approved ? "승인함" : "거절함"}`));
    follow(rid, seq);
  };
  yes.onclick = () => decide(true);
  no.onclick = () => decide(false);
  row.append(yes, no);
  box.append(row);
  $("messages").append(box);
  scrollDown();
}

// ask_user: options as buttons (label + one-line description) and a free-text answer. Model text: textContent only.
function showQuestion(rid, ev, seq) {
  const a = ev.arguments || {};
  const box = el("div", "question");
  box.append(el("div", "q", String(a.question || "")));
  const opts = el("div", "options");
  const answer = async (text) => {
    for (const b of box.querySelectorAll("button, input")) b.disabled = true;
    try {
      await api(`/runs/${rid}/approval`, { method: "POST", body: JSON.stringify({ call_id: ev.call_id, answer: text }) });
    } catch (e) {
      addMsg("error", String(e));
      for (const b of box.querySelectorAll("button, input")) b.disabled = false;
      return;
    }
    box.replaceChildren(el("div", "q", String(a.question || "")), el("div", "answered", text));
    follow(rid, seq);
  };
  for (const o of Array.isArray(a.options) ? a.options : []) {
    const b = el("button", "option");
    b.append(el("span", "label", String(o.label || "")));
    if (o.description) b.append(el("span", "desc", String(o.description)));
    b.onclick = () => answer(String(o.label || ""));
    opts.append(b);
  }
  const other = el("form", "other");
  const input = el("input");
  input.placeholder = "직접 입력";
  const go = el("button", null, "보내기");
  other.append(input, go);
  other.onsubmit = (e) => { e.preventDefault(); if (input.value.trim()) answer(input.value.trim()); };
  box.append(opts, other);
  $("messages").append(box);
  scrollDown();
}

// ---- artifact panel -------------------------------------------------------------------------------
// The iframe attributes are the isolation contract: scripts yes, same-origin/forms/popups/top-navigation no.
function showFrame(url) {
  const f = document.createElement("iframe");
  f.setAttribute("sandbox", "allow-scripts");
  f.setAttribute("referrerpolicy", "no-referrer");
  f.setAttribute("allow", "");
  f.src = url;
  $("frame-wrap").replaceChildren(f);
  return f;
}

async function openArtifact(id, version) {
  const project = state.project;
  const a = await api(`/projects/${project}/artifacts/${id}` + (version ? `?version=${version}` : ""));
  if (state.project !== project) return;  // switched meanwhile: the old project's artifact must not open over the new one
  state.artifact = a;
  $("app").classList.add("with-panel");
  $("panel").hidden = false;
  savedWidth();
  $("art-title").textContent = a.title;
  $("art-title").title = `${a.id} · ${a.type}`;
  const sel = $("art-version");
  sel.replaceChildren(...a.versions.map((v) => new Option(`v${v}`, v)));
  sel.value = a.version;
  $("art-code").textContent = a.content;
  $("art-code").hidden = true;
  $("frame-wrap").hidden = false;
  $("art-source").textContent = "코드";
  const frame = showFrame(a.view_url);
  showSections(a);
  showCapture(a, frame);
  markActive();
}

// Slide images (docs/DESIGN.md): size preset (auto = the view's default; L full width / M half / S third of a 16:9
// slide) and whether the title is drawn. Remembered per browser.
function slideOptions() {
  const box = el("div", "sec");
  const size = el("select");
  size.title = "슬라이드에 넣을 크기";
  for (const [v, t] of [["auto", "크기 자동"], ["L", "전체 폭 (L)"], ["M", "반쪽 (M)"], ["S", "1/3 (S)"]]) size.append(new Option(t, v));
  size.value = ["auto", "L", "M", "S"].includes(store("slidePreset")) ? store("slidePreset") : "auto";
  size.onchange = () => store("slidePreset", size.value);
  const lab = el("label", "check");
  const title = el("input");
  title.type = "checkbox";
  title.checked = store("slideTitle") !== "0";
  title.onchange = () => store("slideTitle", title.checked ? "1" : "0");
  lab.append(title, el("span", null, "제목 포함"));
  box.append(size, lab);
  return { box, get: () => ({ preset: size.value, title: title.checked }) };
}

function download(blob, name) {
  const url = URL.createObjectURL(blob);
  const link = el("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function copyButton(label, blobPromise) {  // blobPromise() is called inside the click (Safari needs that)
  const b = el("button", null, label);
  b.onclick = async () => {
    b.disabled = true;
    try {
      await navigator.clipboard.write([new ClipboardItem({ "image/png": blobPromise() })]);
      b.textContent = "복사됨";
    } catch (e) {
      b.textContent = "실패";
      addMsg("error", `이미지 복사 실패: ${e}`);
    }
    setTimeout(() => { b.textContent = label; b.disabled = false; }, 1500);
  };
  return b;
}

// Reports: each section as a slide image (server-side capture); notes are text, copied as text.
function showSections(a) {
  const bar = $("art-sections");
  bar.replaceChildren();
  bar.hidden = !a.sections;
  if (!a.sections) return;
  const opts = slideOptions();
  bar.append(opts.box);
  const base = `/projects/${state.project}/artifacts/${a.id}/sections`;
  const url = (s) => {
    const o = opts.get();
    return `${base}/${s.id}.png?version=${a.version}&preset=${o.preset === "auto" ? s.preset || "" : o.preset}&title=${o.title ? 1 : 0}`;
  };
  const png = async (s) => (await api(url(s))).blob();
  for (const s of [...a.sections, { id: "all", title: "전체 화면", kind: "image" }]) {
    const g = el("div", "sec");
    g.append(el("span", null, s.title));
    if (s.kind === "text") {
      const b = el("button", null, "텍스트 복사");
      b.onclick = async () => {
        const notes = (JSON.parse(a.content).notes || []).map((n) => `• ${n}`).join("\n");
        try { await navigator.clipboard.writeText(notes); b.textContent = "복사됨"; } catch (e) { b.textContent = "실패"; }
        setTimeout(() => { b.textContent = "텍스트 복사"; }, 1500);
      };
      g.append(b);
    } else {
      const save = el("button", null, "저장");
      save.onclick = async () => {
        const r = await api(url(s));
        const name = /filename="([^"]+)"/.exec(r.headers.get("content-disposition") || "")?.[1] || `${a.id}-${s.id}.png`;
        download(await r.blob(), name);
      };
      g.append(copyButton("복사", () => png(s)), save);
    }
    bar.append(g);
  }
}

// Interactive views (shmoo, log): the user sets up the view inside the frame; the frame redraws it as a slide and
// renders the PNG itself (no network, opaque origin) and posts the bytes back. We only accept a reply from that
// frame's window, to our nonce, that is a PNG.
function frameCapture(frame, o = {}) {
  const nonce = crypto.randomUUID();
  return new Promise((resolve, reject) => {
    const done = (fn, v) => { removeEventListener("message", on); clearTimeout(timer); fn(v); };
    const on = (e) => {
      const d = e.data;
      if (e.source !== frame.contentWindow || !d || d.type !== "capture" || d.nonce !== nonce) return;
      if (d.error) return done(reject, new Error(d.error));
      const ok = d.png instanceof ArrayBuffer && d.png.byteLength < 20e6 &&
        new Uint8Array(d.png, 0, 8).every((b, i) => b === [137, 80, 78, 71, 13, 10, 26, 10][i]);
      if (!ok) return done(reject, new Error("not a PNG"));
      const name = String(d.name || "view").replace(/[^\w.-]+/g, "_").slice(0, 100);
      done(resolve, { blob: new Blob([d.png], { type: "image/png" }), name: name.endsWith(".png") ? name : name + ".png" });
    };
    const timer = setTimeout(() => done(reject, new Error("no answer from the view")), 8000);
    addEventListener("message", on);
    // opaque origin: "*" is the only target
    frame.contentWindow.postMessage({ type: "capture", nonce, preset: o.preset || "auto", title: o.title !== false }, "*");
  });
}

function showCapture(a, frame) {
  if (!a.capture) return;
  const bar = $("art-sections");
  bar.hidden = false;
  const opts = slideOptions();
  const g = el("div", "sec");
  const save = el("button", null, "저장");
  save.onclick = async () => {
    try {
      const r = await frameCapture(frame, opts.get());
      download(r.blob, r.name);
    } catch (e) {
      addMsg("error", `이미지 저장 실패: ${e}`);
    }
  };
  g.append(copyButton("슬라이드 이미지 복사", () => frameCapture(frame, opts.get()).then((r) => r.blob)), save);
  bar.append(opts.box, g);
}

// ---- wiring ---------------------------------------------------------------------------------------
// Nothing of the previous user may stay in the page, not even hidden: a 401 clears what is shown, and both logout
// and a new login reload the page, which also drops answers still in flight for the old user.
function showLogin() {
  if (!$("login").hidden) return;  // already shown (api() and its caller both react to a 401): keep what was typed
  stopFollow();
  $("art-close").onclick();
  for (const id of ["messages", "sessions", "artifacts", "files", "project", "frame-wrap", "art-sections", "art-version"]) $(id).replaceChildren();
  $("art-code").textContent = $("art-title").textContent = $("whoami").textContent = $("input").value = "";
  state.user = state.project = state.session = state.folder = "";
  state.files = [];
  $("app").hidden = true;
  $("login").hidden = false;
  $("login-password").value = "";
  $("login-user").focus();
}

async function enter(user) {
  state.user = user;
  $("whoami").textContent = user;
  $("login").hidden = true;
  $("app").hidden = false;
  await loadProjects().catch((e) => addMsg("error", String(e)));
}

$("login").onsubmit = async (e) => {
  e.preventDefault();
  $("login-error").textContent = "";
  try {
    await api("/login", { method: "POST", body: JSON.stringify(
      { user: $("login-user").value.trim(), password: $("login-password").value }) });
    $("login-password").value = "";
    location.reload();
  } catch (err) {
    $("login-error").textContent = String(err).startsWith("Error: 401") ? "사용자 또는 비밀번호가 틀렸어요." : String(err);
  }
};
$("logout").onclick = async () => {
  try {
    await api("/logout", { method: "POST" });
  } catch (err) {  // the login is still valid: do not look logged out (the next person would be let in)
    addMsg("error", `로그아웃하지 못했어요: ${err}`);
    return;
  }
  showLogin();
  location.reload();
};
// Another tab may have logged in as someone else (the cookie is shared): do not keep showing this user's data.
window.addEventListener("focus", () => {
  if (state.user) api("/me").then(({ user }) => { if (user !== state.user) location.reload(); }).catch(() => {});
});
$("project").onchange = () => { state.project = $("project").value; loadProject(); };
$("new-project").onclick = async () => {
  const id = prompt("프로젝트 id (소문자, 숫자, -, _)");
  if (!id) return;
  await api("/projects", { method: "POST", body: JSON.stringify({ project_id: id }) }).catch((e) => addMsg("error", String(e)));
  store("project", id);
  await loadProjects();
};
$("file-upload").onclick = () => { if (state.project) $("file-input").click(); };
$("file-input").onchange = () => {
  const input = $("file-input");
  uploadFiles(state.project, [...input.files].map((f) => [joinPath(state.folder, f.name), f]));
  input.value = "";
};
$("file-folder").onclick = async () => {
  if (!state.project) return;
  const name = prompt(`새 폴더 이름${state.folder ? ` (${state.folder}/ 안에 만들어요)` : ""}`);
  const path = name && joinPath(state.folder, name.trim().replace(/^\/+|\/+$/g, ""));
  if (!path) return;
  try {
    await api(`/projects/${state.project}/folders`, { method: "POST", body: JSON.stringify({ path }) });
    state.closed.delete(state.folder);  // show it; the selection stays, so siblings can be made one after another
  } catch (e) {
    addMsg("error", `${path}: 폴더를 만들지 못했어요. ${fileError(e)}`);
  }
  await loadFiles();
};
$("files").onclick = (e) => { if (e.target === $("files")) { state.folder = ""; drawFiles(); } };  // empty space: back to the top
dropTarget($("files"), "");
$("new-session").onclick = () => { state.session = ""; $("messages").replaceChildren(); markActive(); $("input").focus(); };
$("composer").onsubmit = (e) => {
  e.preventDefault();
  const text = $("input").value.trim();
  if (!text || state.streaming || !state.project) return;
  $("input").value = "";
  send(text).catch((err) => addMsg("error", String(err)));
};
$("input").onkeydown = (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("composer").requestSubmit(); }
};
$("art-version").onchange = () => openArtifact(state.artifact.id, $("art-version").value);
$("art-source").onclick = () => {
  const code = $("art-code");
  code.hidden = !code.hidden;
  $("frame-wrap").hidden = !code.hidden;
  $("art-source").textContent = code.hidden ? "코드" : "미리보기";
};
$("art-close").onclick = () => {
  $("panel").hidden = true;
  $("app").classList.remove("with-panel", "panel-full");
  for (const id of ["side", "chat", "resizer"]) $(id).inert = false;
  $("art-expand").textContent = "크게";
  $("art-expand").title = "크게 보기 (Esc로 돌아오기)";
  state.artifact = null;
  markActive();
};

// ---- layout: artifact width (drag the divider, arrow keys, double-click resets), full view, sidebar. Remembered per
// browser; the width is clamped so the chat keeps its minimum whatever the window size.
const CHAT_MIN = 360, PANEL_MIN = 320;
// save=true only for the user's own drag/keys: fitting a smaller window (or the sidebar coming back) must not
// overwrite the width they chose, so it is there again when the window grows.
function panelWidth(px, save = false) {
  const app = $("app");
  if (px === null) { app.style.removeProperty("--panel"); store("panelWidth", ""); return; }
  // hidden (login screen) or stacked (narrow window): nothing to measure, and clamping now would lose the saved width
  if (!app.clientWidth || matchMedia("(max-width: 900px)").matches) return;
  const side = app.classList.contains("side-hidden") ? 0 : $("side").offsetWidth;
  const max = Math.max(PANEL_MIN, app.clientWidth - side - CHAT_MIN - 6);
  px = Math.round(Math.min(Math.max(px, PANEL_MIN), max));
  app.style.setProperty("--panel", `${px}px`);
  if (save) store("panelWidth", String(px));
}
const savedWidth = () => { if (store("panelWidth")) panelWidth(Number(store("panelWidth"))); };
$("resizer").onpointerdown = (e) => {
  e.preventDefault();
  const r = $("resizer");
  r.setPointerCapture(e.pointerId);
  $("app").classList.add("resizing");
  r.onpointermove = (m) => panelWidth($("app").getBoundingClientRect().right - m.clientX - 3, true);
  r.onpointerup = r.onpointercancel = () => {
    r.onpointermove = r.onpointerup = r.onpointercancel = null;
    $("app").classList.remove("resizing");
  };
};
$("resizer").onkeydown = (e) => {
  const step = { ArrowLeft: 32, ArrowRight: -32 }[e.key];
  if (!step) return;
  e.preventDefault();
  panelWidth($("panel").offsetWidth + step, true);
};
$("resizer").ondblclick = () => panelWidth(null);
addEventListener("resize", savedWidth);
$("art-expand").onclick = () => {
  const full = $("app").classList.toggle("panel-full");
  for (const id of ["side", "chat", "resizer"]) $(id).inert = full;  // covered: out of Tab order too
  $("art-expand").textContent = full ? "원래대로" : "크게";
  $("art-expand").title = full ? "원래 크기로 (Esc)" : "크게 보기 (Esc로 돌아오기)";
};
const leaveFull = () => { if ($("app").classList.contains("panel-full")) $("art-expand").click(); };
addEventListener("keydown", (e) => { if (e.key === "Escape") leaveFull(); });
// Once the user clicks into the artifact, keys go to the frame (another origin), not to us: the renderer's prelude
// posts Escape up (server/render.py). Only the frame's own window counts; all it can do is leave the full view.
addEventListener("message", (e) => {
  if (e.source === $("frame-wrap").querySelector("iframe")?.contentWindow && e.data?.type === "escape") leaveFull();
});
function sidebar(show) {
  $("app").classList.toggle("side-hidden", !show);
  const label = show ? "사이드바 숨기기" : "사이드바 보이기";
  $("side-toggle").title = label;
  $("side-toggle").setAttribute("aria-label", label);
  store("sidebar", show ? "1" : "0");
  savedWidth();
}
$("side-toggle").onclick = () => sidebar($("app").classList.contains("side-hidden"));
sidebar(store("sidebar") !== "0");
api("/me").then(({ user }) => enter(user)).catch(() => showLogin());
