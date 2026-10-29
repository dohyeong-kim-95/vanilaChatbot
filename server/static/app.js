// Minimal chat UI (Loop 14). Model/tool text is only ever inserted as text (never innerHTML); artifact content
// runs in a sandboxed iframe on the renderer's origin (see server/render.py).
"use strict";

const $ = (id) => document.getElementById(id);
const state = { user: "", project: "", session: "", artifact: null, streaming: false, follow: null };

// One followed run at a time: switching conversation/project stops the old stream (else its events land here).
function stopFollow() { state.follow?.abort(); state.follow = null; }

function store(key, value) {
  try { value === undefined ? (value = localStorage.getItem(key)) : localStorage.setItem(key, value); } catch (e) { value = null; }
  return value;
}

async function api(path, opts = {}) {
  const r = await fetch(path, { ...opts, headers: { "X-User": state.user, "Content-Type": "application/json", ...(opts.headers || {}) } });
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
  $("sessions").replaceChildren();
  $("artifacts").replaceChildren();
  $("messages").replaceChildren();
  stopFollow();
  state.session = "";
  if (!state.project) return;
  store("project", state.project);
  const { sessions } = await api(`/projects/${state.project}/sessions`);
  for (const s of sessions) {
    const li = el("li", null, s);
    li.onclick = () => openSession(s);
    li.dataset.id = s;
    $("sessions").append(li);
  }
  await loadArtifacts();
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

// SSE over fetch (EventSource cannot send the X-User header). Resumes with Last-Event-ID.
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
  const a = await api(`/projects/${state.project}/artifacts/${id}` + (version ? `?version=${version}` : ""));
  state.artifact = a;
  $("app").classList.add("with-panel");
  $("panel").hidden = false;
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
$("user").value = state.user = store("user") || "";
$("user").onchange = async () => {
  state.user = $("user").value.trim();
  store("user", state.user);
  await loadProjects().catch((e) => addMsg("error", String(e)));
};
$("project").onchange = () => { state.project = $("project").value; loadProject(); };
$("new-project").onclick = async () => {
  const id = prompt("프로젝트 id (소문자, 숫자, -, _)");
  if (!id) return;
  await api("/projects", { method: "POST", body: JSON.stringify({ project_id: id }) }).catch((e) => addMsg("error", String(e)));
  store("project", id);
  await loadProjects();
};
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
  $("app").classList.remove("with-panel");
  state.artifact = null;
  markActive();
};
if (state.user) loadProjects().catch((e) => addMsg("error", String(e)));
