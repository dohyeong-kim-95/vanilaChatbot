// Minimal chat UI (Loop 14). Model/tool text is only ever inserted as text (never innerHTML); artifact content
// runs in a sandboxed iframe on the renderer's origin (see server/render.py).
"use strict";

const $ = (id) => document.getElementById(id);
const state = { user: "", project: "", session: "", artifact: null, streaming: false, follow: null,
                files: [], folder: "", closed: new Set(),  // folder: where uploads and new folders go ("" = top)
                outOpen: new Set(),  // folders of 결과 파일 the user opened (closed at first: codegen leaves many)
                history: [], recall: -1, draft: "",  // last prompts sent (newest last); recall: index shown, -1 = none
                runStart: {}, opening: 0 };  // opening: the latest openArtifact call (an older answer is dropped)  // run id -> when it started (ms, this clock) for the elapsed time

// One followed run at a time: switching conversation/project stops the old stream (else its events land here).
// The stopped follow's own cleanup no longer runs (it is not state.follow): the composer is free again here.
function stopFollow() { state.follow?.abort(); state.follow = null; state.streaming = false; $("send").disabled = false; }

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
  // the model's answers as markdown (md.js builds nodes, never HTML); what the user typed and errors stay plain text
  const m = role === "assistant" ? el("div", "msg assistant md") : el("div", `msg ${role}`, text);
  if (role === "assistant") {
    try { m.append(MD.render(text)); } catch (e) { m.className = "msg assistant"; m.textContent = text; }  // never lose the rest of the history
  }
  $("messages").append(m);
  scrollDown();
}

// Line icons drawn as SVG nodes (no markup strings): the usual file and download (arrow into a tray) shapes.
const ICONS = {
  file: [["path", { d: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" }], ["path", { d: "M14 2v6h6" }]],
  download: [["path", { d: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" }], ["path", { d: "M7 10l5 5 5-5" }],
             ["path", { d: "M12 15V3" }]],
};
function icon(name, size = 18) {
  const ns = "http://www.w3.org/2000/svg", svg = document.createElementNS(ns, "svg");
  for (const [k, v] of Object.entries({ viewBox: "0 0 24 24", width: size, height: size, fill: "none", stroke: "currentColor",
    "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" })) svg.setAttribute(k, v);
  for (const [tag, attrs] of ICONS[name]) {
    const e = document.createElementNS(ns, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    svg.append(e);
  }
  return svg;
}

// A program write_code built (Loop 45): a file card like the chat apps' (type icon, name, kind, download icon),
// its file in outputs/, downloadable once the run has saved it.
function addDownloadRef(path) {
  const b = el("button", "file-card download-ref");
  const name = baseName(path), dot = name.lastIndexOf("."), ext = dot > 0 ? name.slice(dot + 1).toUpperCase() : "";
  const text = el("span", "file-text");
  text.append(el("span", "file-name", name), el("span", "meta", ext ? `${ext} 파일` : "파일"));
  const get = el("span", "file-get");
  get.append(icon("download"));
  const kind = el("span", "file-icon");
  kind.append(icon("file", 20));
  b.append(kind, text, get);
  b.title = `내려받기: outputs/${path}`;
  b.setAttribute("aria-label", `${name} 내려받기`);
  b.onclick = async () => {
    // outputs/ is saved when the run ends: before that the same path may still hold an older build (review 45.1)
    if (state.streaming) return addMsg("error", "작업이 끝나면 내려받을 수 있어요");
    try {
      const r = await fetch(`/projects/${state.project}/outputs/file?path=${encodeURIComponent(path)}`);
      if (!r.ok) throw new Error(r.status);  // not api(): a .json file would come back parsed, not as a blob
      download(await r.blob(), baseName(path));
    } catch (e) {
      addMsg("error", `내려받기 실패: ${e}`);
    }
  };
  $("messages").append(b);
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
  $("outputs").replaceChildren();
  stopFollow();
  state.session = "";
  state.files = [];
  state.folder = "";
  state.closed.clear();
  state.outOpen.clear();  // another project's folders start closed too (review 45.1)
  if (!state.project) return;
  store("project", state.project);
  const sessions = await loadSessions();
  await Promise.all([loadArtifacts(), loadFiles(), loadOutputs()]);
  if (sessions.length) await openSession(sessions[0]);
}

async function loadSessions() {
  const { sessions } = await api(`/projects/${state.project}/sessions`);
  $("sessions").replaceChildren(...sessions.map((s) => {
    const li = el("li", null, s);
    li.onclick = () => openSession(s);
    li.dataset.id = s;
    return li;
  }));
  return sessions;
}

// Deliverables the agent left in /workspace/outputs (saved when a run ends): download links.
async function loadOutputs() {
  const project = state.project;
  const { files } = await api(`/projects/${project}/outputs`);
  if (project !== state.project) return;
  state.outputs = files;
  drawOutputs();
}

// Files in subfolders go under folder rows (Loop 45): a click opens or closes one.
function drawOutputs() {
  const ul = $("outputs"), project = state.project;
  const key = (f) => f.path.split("/").map((x, i, all) => [i < all.length - 1 ? 0 : 1, x]);  // folders first
  const order = (a, b) => {
    const [x, y] = [key(a), key(b)];
    for (let i = 0; i < Math.min(x.length, y.length); i++) {
      const c = x[i][0] - y[i][0] || x[i][1].localeCompare(y[i][1], "ko");
      if (c) return c;
    }
    return x.length - y.length;
  };
  const files = [...(state.outputs || [])].sort(order);
  const focused = document.activeElement?.closest?.("#outputs li")?.dataset.path;  // keyboard users keep their place
  ul.replaceChildren();
  const drawn = new Set();
  for (const f of files) {
    const parts = f.path.split("/");
    let shown = true;
    for (let d = 1; d < parts.length && shown; d++) {  // the folders above it, each once, while they are open
      const dir = parts.slice(0, d).join("/");
      if (!drawn.has(dir)) {
        drawn.add(dir);
        const li = el("li", "folder");
        li.style.paddingLeft = `${8 + (d - 1) * 14}px`;
        li.append(el("span", "twist", state.outOpen.has(dir) ? "▾" : "▸"), el("span", "name", parts[d - 1] + "/"));
        li.title = `${dir}/`;
        li.dataset.path = dir;
        li.tabIndex = 0;
        li.onclick = () => { if (!state.outOpen.delete(dir)) state.outOpen.add(dir); drawOutputs(); };
        li.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); li.click(); } };
        ul.append(li);
      }
      shown = state.outOpen.has(dir);
    }
    if (!shown) continue;
    const li = el("li");
    li.style.paddingLeft = `${8 + (parts.length - 1) * 14}px`;
    const a = el("a", "name");
    a.append(el("span", "label", parts[parts.length - 1]));
    a.href = `/projects/${project}/outputs/file?path=${encodeURIComponent(f.path)}`;
    a.download = baseName(f.path);
    li.title = f.path;
    const get = el("span", "get");
    get.append(icon("download", 14));
    a.append(get);
    li.append(el("span", "twist", ""), a, el("span", "meta", fmtSize(f.size)));
    ul.append(li);
  }
  if (!files.length) ul.append(el("li", "empty", "아직 없어요"));
  if (focused !== undefined) [...ul.children].find((li) => li.dataset.path === focused)?.focus();
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

// A list the user made short scrolls by itself: bring the open conversation into it. Only that list moves
// (scrollIntoView would also scroll the sidebar or, in the stacked layout, the page away from the chat).
function showActiveSession() {
  const list = $("sessions"), li = list.querySelector(".active");
  if (!li || !list.classList.contains("sized")) return;
  const a = li.getBoundingClientRect(), b = list.getBoundingClientRect();
  if (a.top < b.top) list.scrollTop -= b.top - a.top;
  else if (a.bottom > b.bottom) list.scrollTop += a.bottom - b.bottom;
}

async function openSession(sid) {
  stopFollow();
  state.session = sid;
  markActive();
  showActiveSession();
  const s = await api(`/sessions/${sid}`);
  if (state.session !== sid) return;  // the user already switched again; a late answer must not paint over it
  $("messages").replaceChildren();
  for (const m of s.messages) {
    if (m.role === "artifact") addArtifactRef(m.id);
    else if (m.role === "download") addDownloadRef(m.path);
    else if (m.role === "plan") drawPlan(m.steps);
    else addMsg(m.role, m.content);
  }
  // History already has what was said: draw only the card that is waiting now, follow only newer events
  // (replaying from 0 duplicated messages and redrew answered cards with live buttons).
  const r = s.active_run;
  if (r && r.elapsed != null) state.runStart[r.run_id] = Date.now() - r.elapsed * 1000;
  if (r?.pending) (r.pending.type === "question" ? showQuestion : showApproval)(r.run_id, r.pending, r.seq);
  else if (r) follow(r.run_id, r.seq);
}

// ---- files: the user's own folders. The model is shown the same tree with every message (agent/userstore.py), so
// "the files in the 카드 folder" means what is drawn here. Names come from the user: textContent only.
const MAX_FILE = 5 * 1024 * 1024;  // agent/userstore.py MAX_UPLOAD: bigger files go up in parts (bigUpload)
const parentOf = (p) => p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "";
const baseName = (p) => p.slice(p.lastIndexOf("/") + 1);
const joinPath = (dir, name) => dir ? `${dir}/${name}` : name;
const fileUrl = (project, path) => `/projects/${project}/files?path=${encodeURIComponent(path)}`;
const fmtSize = (n) => n < 1024 ? `${n} B` : n < 1024 ** 2 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1024 ** 2).toFixed(1)} MB`;

function fileError(e) {
  const s = String(e.message || e);
  if (s.startsWith("409")) return "같은 이름이 이미 있어요";
  if (s.startsWith("413")) return /disk/.test(s) ? "서버 디스크 공간이 모자라요" : "너무 커요 (파일 4GB, 프로젝트 전체 20GB·2,000개까지)";
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
    try {
      if (file.size > MAX_FILE) {
        const n = i;
        await bigUpload(project, path, file, (f) => {
          busy.textContent = `올리는 중… ${n}/${files.length} · ${baseName(path)} ${Math.floor(f * 100)}% (${fmtSize(f * file.size)} / ${fmtSize(file.size)})`;
        });
      } else {
        await api(fileUrl(project, path), { method: "PUT", body: file, headers: { "Content-Type": "application/octet-stream" } });
      }
    } catch (e) {
      errs.push(`${path}: ${fileError(e)}`);
    }
  }
  busy.remove();
  if (errs.length) addMsg("error", `올리지 못했어요\n${errs.join("\n")}`);
  if (project === state.project) await loadFiles();
}

// A big file in parts (Loop 31): each part goes on from where the server says the upload stands, so a lost reply
// or a dropped connection costs one part, not the file. Gives up (and frees the space) after repeated failures.
async function bigUpload(project, path, file, show) {
  const base = `/projects/${project}/files/uploads`;
  // the same file again after a reload or a dropped connection: go on with the upload already there (1 day)
  const key = `upload:${state.user}:${project}:${path}:${file.size}:${file.lastModified}`;
  let begun = null;
  const known = store(key);
  if (known) begun = await api(`${base}/${known}`).catch(() => null);
  if (!begun) {
    begun = await api(base, { method: "POST", body: JSON.stringify({ path, size: file.size }) });
    store(key, begun.upload_id);
  }
  const id = begun.upload_id, part = begun.part_max;
  let received = begun.received, fails = 0;
  const retryable = (e) => e.name === "TypeError" || /^(408|409|5\d\d)/.test(String(e.message));  // network, offset, server
  const keep = (e) => /^401/.test(String(e.message));  // logged out: keep the id, the same file goes on after login
  while (received < file.size) {
    show(received / file.size);
    try {
      const r = await api(`${base}/${id}?offset=${received}`, {
        method: "PUT", body: file.slice(received, received + part), headers: { "Content-Type": "application/octet-stream" },
      });
      received = r.received;
      fails = 0;
    } catch (e) {
      if (!retryable(e)) {
        if (!keep(e)) {
          api(`${base}/${id}`, { method: "DELETE" }).catch(() => {});
          store(key, "");
        }
        throw e;
      }
      if (++fails > 6) throw e;  // still down: kept, the same file picks it up later
      await new Promise((ok) => setTimeout(ok, 500 * 2 ** fails));
      try { received = (await api(`${base}/${id}`)).received; } catch (e2) { /* still down: try again */ }
    }
  }
  show(1);
  try {
    await api(`${base}/${id}/finish`, { method: "POST" });
  } catch (e) {
    if (!/^5\d\d/.test(String(e.message)) && e.name !== "TypeError") {  // e.g. the name was taken meanwhile
      api(`${base}/${id}`, { method: "DELETE" }).catch(() => {});
      store(key, "");
    }
    throw e;
  }
  store(key, "");
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
    await loadSessions();  // the list only: the open artifact, folders and files stay as they are (Loop 38)
    await openSession(session_id);
  }
  addMsg("user", text);
  const sent = Date.now();
  const { run_id } = await api(`/sessions/${state.session}/messages`, { method: "POST", body: JSON.stringify({ content: text }) });
  state.runStart[run_id] = sent;
  await follow(run_id);
}

// Prompt history like a shell. ↑ with the caret at the very start brings back earlier prompts, ↓ at the very end goes
// forward and finally back to what was being typed. Elsewhere the arrows only move the caret: on the top (bottom) row,
// wrapped or not, the browser first moves it to the start (end), so a long draft is never swapped out by one press.
// A recalled prompt that was edited becomes the new "being typed".
const HISTORY = 4;
function remember(text) {
  state.history = state.history.filter((t) => t !== text).concat(text).slice(-HISTORY);
  state.recall = -1;
}

function recall(step) {
  const box = $("input"), h = state.history, { value, selectionStart: a, selectionEnd: b } = box;
  if (state.recall >= 0 && value !== h[state.recall]) state.recall = -1;  // edited: start over from this text
  if (a !== b || a !== (step < 0 ? 0 : value.length)) return false;
  if (state.recall < 0) {
    if (step > 0 || !h.length) return false;
    state.draft = value;
    state.recall = h.length;
  }
  const next = state.recall + step;
  if (next < 0) return true;  // at the oldest: stay
  box.value = next < h.length ? h[next] : state.draft;
  state.recall = next < h.length ? next : -1;
  box.selectionStart = box.selectionEnd = step < 0 ? 0 : box.value.length;  // the same key keeps stepping
  return true;
}

// SSE over fetch (EventSource cannot send Last-Event-ID on the first request). Resumes with Last-Event-ID.
async function follow(rid, after = 0) {
  stopFollow();
  const ctl = state.follow = new AbortController();
  state.streaming = true;
  $("send").disabled = true;
  const status = el("div", "tool status");
  status.now = "작업 중…";
  status.started = state.runStart[rid] ?? (state.runStart[rid] = Date.now());
  showStatus(status);
  const tick = setInterval(() => showStatus(status), 1000);
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
    clearInterval(tick);
    status.remove();
    if (state.project) loadOutputs().catch(() => {});  // the run's outputs are saved when it ends
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
      try {
        if (ev.name === "bash") name += ": " + JSON.parse(ev.arguments).command.split("\n")[0].slice(0, 80);
        if (ev.name === "run_long") name = JSON.parse(ev.arguments).label;
      } catch (e) {}
      if (ev.name !== "update_plan") status.now = name;
      break;
    }
    case "tool_result":
      status.now = "생각하는 중…";
      break;
    case "effort":
      status.now = ev.reason === "correction" ? "다시 꼼꼼히 확인하는 중…" : "오류가 이어져 더 깊게 생각하는 중…";
      break;
    case "agent_progress":
      status.now = `${ev.agent}: ${ev.message}`;
      break;
    case "plan":
      drawPlan(ev.steps);
      break;
    case "job":  // a long command (run_long): its queue place, then its progress
      status.now = ev.ahead != null ? `${ev.label}: 대기 중 (앞에 ${ev.ahead}개)`
        : ev.total ? `${ev.label} ${Math.min(100, Math.floor((100 * ev.done) / ev.total))}%${ev.note ? ` · ${ev.note}` : ""}`
        : ev.label;
      break;
    case "download":
      addDownloadRef(ev.path);
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
  showStatus(status);
}

// ---- plan and progress (Loop 30): the model's update_plan steps as one card per turn, updated in place; the status
// line under it says which step is running, what the agent is doing right now and for how long.
const MARK = { pending: "○", in_progress: "◐", done: "✓" };
function planCard() {  // this turn's card: after the last message of the user
  const all = [...$("messages").children];
  return all.slice(all.findLastIndex((e) => e.classList.contains("user")) + 1).find((e) => e.classList.contains("plan"));
}

function drawPlan(steps) {
  let card = planCard();
  if (!card) $("messages").append(card = el("div", "plan"));
  const done = steps.filter((s) => s.status === "done").length;
  const head = el("div", "plan-head");
  head.append(el("span", null, "계획"), el("span", "meta", `${done}/${steps.length} 완료`));
  const bar = el("div", "plan-bar");
  bar.setAttribute("role", "progressbar");
  bar.setAttribute("aria-valuemin", "0");
  bar.setAttribute("aria-valuemax", String(steps.length));
  bar.setAttribute("aria-valuenow", String(done));
  bar.append(el("span"));
  bar.firstChild.style.width = `${(100 * done) / steps.length}%`;
  const ol = el("ol");
  for (const s of steps) {
    const li = el("li", s.status.replace("_", "-"));
    li.append(el("span", "mark", MARK[s.status]), el("span", null, s.title));
    ol.append(li);
  }
  card.replaceChildren(head, bar, ol);
  card.step = steps.findIndex((s) => s.status === "in_progress");
  card.stepTitle = card.step >= 0 ? steps[card.step].title : "";
  card.total = steps.length;
  scrollDown();
}

const clock = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
function showStatus(status) {
  const card = planCard(), parts = [];
  if (card?.stepTitle) parts.push(`${card.step + 1}/${card.total} ${card.stepTitle}`);
  parts.push(status.now, clock((Date.now() - status.started) / 1000));
  status.textContent = parts.join(" · ");
  const box = $("messages");
  if (status.isConnected && status !== box.lastElementChild) box.append(status);  // stays under what came last
}

function showApproval(rid, ev, seq) {
  const box = el("div", "approval");
  box.append(el("div", null, `승인 필요: ${ev.tool}`), el("pre", null, JSON.stringify(ev.arguments, null, 2)));
  const row = el("div", "row");
  const yes = el("button", "primary", "승인");
  const no = el("button", null, "거절");
  const decide = async (approved) => {
    yes.disabled = no.disabled = true;
    try {
      await api(`/runs/${rid}/approval`, { method: "POST", body: JSON.stringify({ call_id: ev.call_id, approved }) });
    } catch (e) {  // e.g. answered in another tab: say so, the card stays usable
      addMsg("error", String(e));
      yes.disabled = no.disabled = false;
      return;
    }
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
  f.ready = new Promise((ok) => f.addEventListener("load", ok, { once: true }));
  f.setAttribute("sandbox", "allow-scripts");
  f.setAttribute("referrerpolicy", "no-referrer");
  f.setAttribute("allow", "");
  f.src = url;
  $("frame-wrap").replaceChildren(f);
  return f;
}

async function openArtifact(id, version) {
  const project = state.project, seq = ++state.opening;
  const a = await api(`/projects/${project}/artifacts/${id}` + (version ? `?version=${version}` : ""));
  // switched meanwhile: the old project's artifact must not open over the new one; and the last click wins
  if (state.project !== project || seq !== state.opening) return false;
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
  return true;
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
  return MD.copyButton(label, () => navigator.clipboard.write([new ClipboardItem({ "image/png": blobPromise() })])
    .catch((e) => { addMsg("error", `이미지 복사 실패: ${e}`); throw e; }));
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
  for (const id of ["messages", "sessions", "artifacts", "files", "outputs", "project", "frame-wrap", "art-sections", "art-version"]) $(id).replaceChildren();
  $("art-code").textContent = $("art-title").textContent = $("whoami").textContent = $("input").value = "";
  state.user = state.project = state.session = state.folder = "";
  state.files = [];
  state.history = [];  // memory only (never stored in the browser): gone with the user
  state.recall = -1;
  state.draft = "";
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
$("new-session").onclick = () => {
  stopFollow();  // else the running answer of the other conversation keeps landing here
  state.session = ""; $("messages").replaceChildren(); markActive(); $("input").focus();
};
$("composer").onsubmit = (e) => {
  e.preventDefault();
  const text = $("input").value.trim();
  if (!text || state.streaming || !state.project) return;
  $("input").value = "";
  remember(text);
  send(text).catch((err) => addMsg("error", String(err)));
};
$("input").onkeydown = (e) => {
  if (e.isComposing || e.keyCode === 229) return;  // a Hangul syllable being composed (Safari: keyCode only)
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("composer").requestSubmit(); }
  if ((e.key === "ArrowUp" || e.key === "ArrowDown") && !e.shiftKey && !e.altKey && !e.ctrlKey && !e.metaKey
      && recall(e.key === "ArrowUp" ? -1 : 1)) e.preventDefault();
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
// A citation chip in an answer ([그림 2](artifact:id@v#ref), md.js): open that artifact (unless it is already the
// one shown) and ask its page to bring the part into view (the renderer's prelude scrolls to it and outlines it).
$("messages").addEventListener("click", async (e) => {
  const b = e.target.closest("button.cite");
  if (!b) return;
  const { artifact: id, version, ref } = b.dataset, a = state.artifact;
  try {
    // a chip with a version that is open already only moves in it; without one it means the latest: fetched again
    const shown = version && a && a.id === id && String(a.version) === version && !$("panel").hidden && !$("frame-wrap").hidden;
    if (!shown && !(await openArtifact(id, version))) return;  // a later click or a project switch took over
    const f = $("frame-wrap").querySelector("iframe"), seq = state.opening;
    if (!ref || !f) return;
    await f.ready;
    if (seq !== state.opening || f !== $("frame-wrap").querySelector("iframe")) return;
    // "*": the sandboxed frame's origin is opaque ("null" cannot be a target); the message is only a part name
    f.contentWindow?.postMessage({ type: "focus", ref }, "*");
  } catch (err) {
    addMsg("error", `근거 artifact(${id}${version ? " v" + version : ""})를 열지 못했어요. ${String(err).startsWith("Error: 404") ? "없거나 지워진 artifact예요." : err}`);
  }
});
function sidebar(show) {
  $("app").classList.toggle("side-hidden", !show);
  const label = show ? "사이드바 숨기기" : "사이드바 보이기";
  $("side-toggle").title = label;
  $("side-toggle").setAttribute("aria-label", label);
  store("sidebar", show ? "1" : "0");
  savedWidth();
}
// Sidebar lists (Loop 29): the divider under a list sets its height (drag, ↑/↓, double-click = as tall as its
// content again). Remembered per browser; CSS keeps it between 48px and 60vh whatever the window.
// save=true only for the user's own drag/keys: the saved height is set as it is (CSS fits it to a low window), so
// loading the page in a low window does not lose it.
const LIST_MIN = 48;
function listHeight(list, px, save = false) {
  const key = `listHeight:${list.id}`;
  if (px === null) { list.classList.remove("sized"); list.style.removeProperty("--h"); store(key, ""); return; }
  if (save) px = Math.round(Math.min(Math.max(px, LIST_MIN), innerHeight * 0.6));
  list.classList.add("sized");
  list.style.setProperty("--h", `${px}px`);
  if (save) store(key, String(px));
}
for (const split of document.querySelectorAll(".split")) {
  const list = $(split.dataset.list);
  const saved = Number(store(`listHeight:${list.id}`));
  if (saved > 0) listHeight(list, saved);  // (NaN or 0 when missing or garbled)
  split.onpointerdown = (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    split.setPointerCapture(e.pointerId);
    split.classList.add("dragging");
    $("app").classList.add("resizing-v");
    const y0 = e.clientY, h0 = list.offsetHeight;
    split.onpointermove = (m) => listHeight(list, h0 + m.clientY - y0, true);
    split.onpointerup = split.onpointercancel = () => {
      split.onpointermove = split.onpointerup = split.onpointercancel = null;
      split.classList.remove("dragging");
      $("app").classList.remove("resizing-v");
    };
  };
  split.onkeydown = (e) => {
    const step = { ArrowUp: -24, ArrowDown: 24 }[e.key];
    if (!step) return;
    e.preventDefault();
    listHeight(list, list.offsetHeight + step, true);
  };
  split.ondblclick = () => listHeight(list, null);
}
$("side-toggle").onclick = () => sidebar($("app").classList.contains("side-hidden"));
sidebar(store("sidebar") !== "0");
api("/me").then(({ user }) => enter(user)).catch(() => showLogin());
