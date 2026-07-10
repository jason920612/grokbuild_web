"use strict";

/* ---------- helpers ---------- */
const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, html) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html != null) n.innerHTML = html;
  return n;
};
const escapeHtml = (s) =>
  (s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function md(text) {
  const raw = text || "";
  if (window.marked && window.DOMPurify) {
    try { return DOMPurify.sanitize(marked.parse(raw)); } catch (_) {}
  }
  return "<p>" + escapeHtml(raw).replace(/\n/g, "<br>") + "</p>";
}

function textOf(content) {
  if (content == null) return "";
  if (typeof content === "string") return content;
  if (Array.isArray(content)) return content.map(textOf).join("");
  if (typeof content === "object") {
    if (content.type === "image") return "🖼️ [圖片]";
    if (typeof content.text === "string") return content.text;
    if (content.content) return textOf(content.content);
  }
  return "";
}

/* ---------- app state ---------- */
const app = {
  ws: null,
  sessionId: null,
  thread: null,
  refs: { assistant: null, thought: null, tools: new Map(), plan: null },
  userSeg: null,
  pendingEcho: null,
  active: false,
};

/* ---------- session picker ---------- */
async function loadSessions() {
  const list = $("#session-list");
  list.innerHTML = '<p class="hint">載入中…</p>';
  try {
    const res = await fetch("/api/sessions");
    const sessions = await res.json();
    list.innerHTML = "";
    if (!sessions.length) {
      list.innerHTML = '<p class="hint">尚無對話。請先在終端機執行 <code>grok</code> 建立一個。</p>';
      return;
    }
    for (const s of sessions) {
      const item = el("button", "session-item");
      item.appendChild(el("div", "s-title", escapeHtml(s.title)));
      const meta = el("div", "s-meta");
      meta.appendChild(el("span", null, escapeHtml((s.cwd || "").split(/[\\/]/).pop() || s.cwd)));
      if (s.model) meta.appendChild(el("span", null, escapeHtml(s.model)));
      if (s.updated_at) meta.appendChild(el("span", null, s.updated_at.slice(0, 10)));
      meta.appendChild(el("span", null, (s.num_messages || 0) + " 則"));
      item.appendChild(meta);
      item.onclick = () => openChat(s.id, s.title);
      list.appendChild(item);
    }
  } catch (e) {
    list.innerHTML = '<p class="hint">無法載入對話列表。後端有在跑嗎？</p>';
  }
}

/* ---------- new chat ---------- */
async function openNewChat() {
  $("#nc-error").textContent = "";
  $("#nc-cwd").value = "";
  $("#newchat").classList.remove("hidden");
  $("#newchat-backdrop").classList.remove("hidden");

  // recent working directories as quick-pick chips
  const recent = $("#nc-recent");
  recent.innerHTML = "";
  try {
    const { dirs } = await (await fetch("/api/recent-dirs")).json();
    if (dirs && dirs.length) $("#nc-cwd").value = dirs[0];
    for (const d of (dirs || []).slice(0, 8)) {
      const chip = el("button", "chip", escapeHtml(d.split(/[\\/]/).pop() || d));
      chip.title = d;
      chip.onclick = () => { $("#nc-cwd").value = d; };
      recent.appendChild(chip);
    }
  } catch (_) {}

  // model options from global status (agent model catalog)
  const sel = $("#nc-model");
  sel.innerHTML = "";
  try {
    const status = await (await fetch("/api/status")).json();
    const ms = status.agent?.modelState;
    for (const m of ms?.availableModels || []) {
      const o = document.createElement("option");
      o.value = m.modelId; o.textContent = m.name || m.modelId;
      if (m.modelId === ms.currentModelId) o.selected = true;
      sel.appendChild(o);
    }
  } catch (_) {}
  if (!sel.options.length) {
    const o = document.createElement("option");
    o.value = ""; o.textContent = "預設模型";
    sel.appendChild(o);
  }
}

function closeNewChat() {
  $("#newchat").classList.add("hidden");
  $("#newchat-backdrop").classList.add("hidden");
}

async function createSession() {
  const cwd = $("#nc-cwd").value.trim();
  const model = $("#nc-model").value || undefined;
  if (!cwd) { $("#nc-error").textContent = "請輸入工作目錄"; return; }
  const btn = $("#nc-create");
  btn.disabled = true; btn.textContent = "建立中…";
  $("#nc-error").textContent = "";
  try {
    const res = await fetch("/api/sessions/new", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cwd, model }),
    });
    const j = await res.json();
    if (!res.ok || j.error) throw new Error(j.error || ("HTTP " + res.status));
    closeNewChat();
    openChat(j.id, j.title || "新對話");
  } catch (e) {
    $("#nc-error").textContent = String(e.message || e);
  } finally {
    btn.disabled = false; btn.textContent = "建立並開始";
  }
}

/* ---------- view switching ---------- */
function show(view) {
  $("#picker").classList.toggle("hidden", view !== "picker");
  $("#chat").classList.toggle("hidden", view !== "chat");
}

function openChat(id, title) {
  app.closing = true;
  clearTimeout(app.reconnectTimer);
  app.reconnectTimer = null;
  if (app.ws) { try { app.ws.close(); } catch (_) {} app.ws = null; }
  stopHeartbeat();

  app.sessionId = id;
  app.reconnectDelay = 1000;
  $("#chat-title").textContent = title || "對話";
  const messages = $("#messages");
  messages.innerHTML = "";
  app.thread = el("div", "thread");
  messages.appendChild(app.thread);
  app.refs = { assistant: null, thought: null, tools: new Map(), plan: null };
  app.userSeg = null;
  app.pendingEcho = null;
  setActive(false);
  show("chat");
  connect(id);
}

function backToPicker() {
  app.closing = true;
  clearTimeout(app.reconnectTimer);
  app.reconnectTimer = null;
  if (app.ws) { app.ws.close(); app.ws = null; }
  app.sessionId = null;
  show("picker");
  loadSessions();
}

/* ---------- websocket (heartbeat + auto-reconnect) ---------- */
const HEARTBEAT_MS = 25000;      // keep the connection warm through proxies/CF tunnel
const RECONNECT_MAX_MS = 15000;

function connect(id) {
  app.closing = false;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/${id}`);
  app.ws = ws;
  setConn("connecting");

  ws.onopen = () => {
    setConn("on");
    app.reconnectDelay = 1000;
    startHeartbeat();
  };
  ws.onclose = () => {
    stopHeartbeat();
    setActive(false);
    if (app.closing || app.sessionId !== id) return;
    setConn("off");
    scheduleReconnect(id);
  };
  ws.onerror = () => {};
  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch (_) { return; }
    handle(msg);
  };
}

function scheduleReconnect(id) {
  if (app.reconnectTimer || app.closing) return;
  const delay = app.reconnectDelay || 1000;
  setConn("reconnecting");
  app.reconnectTimer = setTimeout(() => {
    app.reconnectTimer = null;
    if (!app.closing && app.sessionId === id) connect(id);
  }, delay);
  app.reconnectDelay = Math.min(delay * 2, RECONNECT_MAX_MS);
}

function startHeartbeat() {
  stopHeartbeat();
  app.hb = setInterval(() => {
    if (app.ws && app.ws.readyState === WebSocket.OPEN) {
      try { app.ws.send(JSON.stringify({ type: "ping" })); } catch (_) {}
    }
  }, HEARTBEAT_MS);
}
function stopHeartbeat() { if (app.hb) { clearInterval(app.hb); app.hb = null; } }

// Phones freeze timers on backgrounded tabs, so the socket silently dies while
// away. Reconnect the instant the tab is shown again or the network returns.
function ensureConnected() {
  if (!app.sessionId || app.closing) return;
  const st = app.ws && app.ws.readyState;
  if (st === WebSocket.OPEN || st === WebSocket.CONNECTING) return;
  clearTimeout(app.reconnectTimer);
  app.reconnectTimer = null;
  app.reconnectDelay = 1000;
  connect(app.sessionId);
}
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") ensureConnected();
});
window.addEventListener("online", ensureConnected);
window.addEventListener("focus", ensureConnected);

function setConn(state) {
  const c = $("#conn");
  c.classList.toggle("on", state === "on");
  c.classList.toggle("off", state === "off");
  c.classList.toggle("reconnecting", state === "reconnecting" || state === "connecting");
  c.title = { on: "已連線", off: "已斷線", reconnecting: "重新連線中…", connecting: "連線中…" }[state] || "";
}

function resetThread() {
  app.thread.innerHTML = "";
  app.refs = { assistant: null, thought: null, tools: new Map(), plan: null };
  app.userSeg = null;
  app.pendingEcho = null;
  interactions.clear();
}

function handle(msg) {
  switch (msg.type) {
    case "history":
      // full transcript — rebuild so this is idempotent on reconnect
      resetThread();
      for (const item of msg.items) renderHistoryItem(item);
      scrollToBottom(true);
      break;
    case "session_update":
      onUpdate(msg.update);
      break;
    case "permission_request":
      renderPermission(msg);
      break;
    case "question_request":
      renderQuestions(msg);
      break;
    case "interaction_done":
      resolveInteraction(msg.requestId);
      break;
    case "session_state":
      if (window.settings) settings.lastState = msg.state;
      break;
    case "turn_end":
      if (app.refs.assistant) finalizeAssistant(app.refs.assistant);
      app.refs.assistant = null;
      app.refs.thought = null;
      setActive(false);
      break;
    case "error":
      addNotice("⚠ " + msg.message, "err");
      setActive(false);
      break;
    case "agent_exit":
      addNotice("代理程式已離線。", "err");
      setActive(false);
      break;
  }
}

/* ---------- history rendering (coalesced items) ---------- */
function renderHistoryItem(item) {
  switch (item.role) {
    case "user": addUserBubble(item.text); break;
    case "assistant": addAssistantBubble(item.text); break;
    case "thought": addThoughtBubble(item.text, false); break;
    case "tool": addToolCard(item); break;
    case "plan": renderPlan(item.entries); break;
  }
}

/* ---------- live update reducer ---------- */
function onUpdate(u) {
  const k = u.sessionUpdate;
  if (k !== "user_message_chunk") app.userSeg = null;

  switch (k) {
    case "user_message_chunk": {
      const text = textOf(u.content);
      if (!app.userSeg) {
        startNewTurn();
        const t = text.trim();
        const p = (app.pendingEcho || "").trim();
        if (p && (p.startsWith(t) || t.startsWith(p) || t === p)) {
          app.userSeg = { echo: true };
          app.pendingEcho = null;
        } else {
          app.userSeg = { echo: false, el: addUserBubble(""), raw: "" };
        }
      }
      if (!app.userSeg.echo) {
        app.userSeg.raw += text;
        app.userSeg.el.querySelector(".bubble").textContent = app.userSeg.raw;
        scrollToBottom();
      }
      break;
    }
    case "agent_message_chunk": {
      setActive(true);
      if (!app.refs.assistant) app.refs.assistant = addAssistantBubble("");
      const box = app.refs.assistant;
      box.dataset.raw = (box.dataset.raw || "") + textOf(u.content);
      box.querySelector(".bubble").innerHTML = md(box.dataset.raw);
      scrollToBottom();
      break;
    }
    case "agent_thought_chunk": {
      setActive(true);
      if (!app.refs.thought) app.refs.thought = addThoughtBubble("", true);
      const body = app.refs.thought.querySelector(".t-body");
      body.textContent += textOf(u.content);
      scrollToBottom();
      break;
    }
    case "tool_call":
      upsertTool(u, false);
      break;
    case "tool_call_update":
      upsertTool(u, true);
      break;
    case "plan":
      renderPlan(u.entries || []);
      break;
  }
}

function startNewTurn() {
  app.refs.assistant = null;
  app.refs.thought = null;
}

/* ---------- file/image previews in messages ---------- */
const IMG_EXT = /\.(png|jpe?g|webp|gif)$/i;
// [附加圖片: path] / [附加檔案: path (mime, N bytes)] notes generated by the bridge
const NOTE_RE = /\[附加(圖片|檔案):\s*([^\]\n(]+?)(?:\s*\([^)]*\))?\]\s*(?:請用你的檔案工具讀取。)?/g;
// bare absolute Windows paths with a file extension, e.g. C:\dir\file.png
const PATH_RE = /[A-Za-z]:\\[^\s"'`<>|*?\][)(]+\.[A-Za-z0-9]{1,8}/g;

function fileUrl(p) {
  return `/api/sessions/${app.sessionId}/file?path=${encodeURIComponent(p)}`;
}

function fileWidget(p) {
  // image -> inline preview; anything else -> collapsible viewer, loaded on open
  if (IMG_EXT.test(p)) {
    const a = document.createElement("a");
    a.href = fileUrl(p); a.target = "_blank";
    const img = document.createElement("img");
    img.src = fileUrl(p); img.loading = "lazy";
    img.onerror = () => a.replaceWith(el("span", "file-tag", "🖼️ " + escapeHtml(p.split("\\").pop())));
    a.appendChild(img);
    return a;
  }
  const d = el("details", "file-fold");
  const name = p.split("\\").pop();
  d.appendChild(el("summary", null, "📄 " + escapeHtml(name)));
  const body = el("div", "file-fold-body", "載入中…");
  d.appendChild(body);
  d.addEventListener("toggle", async () => {
    if (!d.open || d.dataset.loaded) return;
    d.dataset.loaded = "1";
    try {
      const m = await (await fetch(fileUrl(p) + "&meta=1")).json();
      if (m.error) { body.textContent = "無法讀取（檔案不存在或不在工作目錄內）"; return; }
      if (m.isText && m.size <= 512 * 1024) {
        const text = await (await fetch(fileUrl(p))).text();
        body.innerHTML = "";
        const pre = el("pre", null, escapeHtml(text));
        body.appendChild(pre);
      } else {
        body.innerHTML = "";
        const a = document.createElement("a");
        a.href = fileUrl(p); a.target = "_blank";
        a.textContent = `下載 ${m.name}（${(m.size / 1024).toFixed(1)} KB）`;
        body.appendChild(a);
      }
    } catch (_) { body.textContent = "讀取失敗"; }
  });
  return d;
}

function extractFiles(text) {
  // returns {clean, paths[]} — strips attachment notes, collects file paths
  const paths = [];
  let clean = text.replace(NOTE_RE, (_, _kind, p) => {
    paths.push(p.trim());
    return "";
  }).trim();
  const seen = new Set(paths);
  for (const m of clean.match(PATH_RE) || []) {
    if (!seen.has(m) && IMG_EXT.test(m) && seen.size < 8) { seen.add(m); paths.push(m); }
  }
  return { clean, paths };
}

function appendFileWidgets(bubble, paths) {
  if (!paths.length) return;
  let wrap = bubble.querySelector(".msg-att");
  if (!wrap) { wrap = el("div", "msg-att"); bubble.appendChild(wrap); }
  for (const p of paths.slice(0, 8)) wrap.appendChild(fileWidget(p));
}

/* ---------- DOM builders ---------- */
function addUserBubble(text) {
  const m = el("div", "msg msg-user");
  const { clean, paths } = extractFiles(text || "");
  const b = el("div", "bubble", escapeHtml(clean || (paths.length ? "（附件）" : text)));
  m.appendChild(b);
  appendFileWidgets(b, paths);
  app.thread.appendChild(m);
  scrollToBottom();
  return m;
}
function addAssistantBubble(text) {
  const m = el("div", "msg msg-assistant");
  m.dataset.raw = text || "";
  m.appendChild(el("div", "bubble", md(text)));
  if (text) finalizeAssistant(m); // history has full text; streaming finalizes at turn_end
  app.thread.appendChild(m);
  scrollToBottom();
  return m;
}
function finalizeAssistant(m) {
  if (m.dataset.finalized) return;
  m.dataset.finalized = "1";
  // inline image previews for image paths the agent mentions
  const { paths } = extractFiles(m.dataset.raw || "");
  const imgs = paths.filter((p) => IMG_EXT.test(p));
  if (imgs.length) appendFileWidgets(m.querySelector(".bubble"), imgs);
}
function addThoughtBubble(text, open) {
  const d = el("details", "msg thought");
  if (open) d.open = true;
  d.appendChild(el("summary", null, "思考過程"));
  d.appendChild(el("div", "t-body", escapeHtml(text)));
  app.thread.appendChild(d);
  scrollToBottom();
  return d;
}
function addNotice(text, cls) {
  const n = el("div", "msg", `<div class="bubble" style="color:var(--${cls || "text-dim"})">${escapeHtml(text)}</div>`);
  app.thread.appendChild(n);
  scrollToBottom();
}

function toolStatusLabel(s) {
  return { pending: "執行中…", in_progress: "執行中…", completed: "完成", failed: "失敗" }[s] || s || "";
}
function addToolCard(u) {
  const card = el("div", "msg tool-card");
  const head = el("div", "tool-head");
  head.appendChild(el("span", "tool-icon " + (u.status || "")));
  head.appendChild(el("span", "tool-name", escapeHtml(u.title || u.kind || "tool")));
  head.appendChild(el("span", "tool-status", toolStatusLabel(u.status)));
  card.appendChild(head);
  const body = el("div", "tool-body hidden");
  card.appendChild(body);
  head.onclick = () => body.classList.toggle("hidden");
  card.dataset.id = u.id || u.toolCallId || "";
  setToolBody(body, u.content);
  app.thread.appendChild(card);
  scrollToBottom();
  return card;
}
function setToolBody(body, content) {
  const txt = textOf(content);
  body.innerHTML = txt ? `<pre>${escapeHtml(txt)}</pre>` : "";
}
function upsertTool(u, isUpdate) {
  const id = u.toolCallId || u.id;
  let card = app.refs.tools.get(id);
  if (!card) {
    card = addToolCard({ ...u, id });
    app.refs.tools.set(id, card);
    return;
  }
  if (u.status) {
    card.querySelector(".tool-icon").className = "tool-icon " + u.status;
    card.querySelector(".tool-status").textContent = toolStatusLabel(u.status);
  }
  if (u.title) card.querySelector(".tool-name").textContent = u.title;
  if (u.content) setToolBody(card.querySelector(".tool-body"), u.content);
}

function renderPlan(entries) {
  if (!app.refs.plan) {
    app.refs.plan = el("div", "msg plan-card");
    app.refs.plan.appendChild(el("h4", null, "計畫"));
    app.thread.appendChild(app.refs.plan);
  }
  const card = app.refs.plan;
  card.querySelectorAll(".plan-entry").forEach((n) => n.remove());
  for (const e of entries) {
    const status = e.status || "pending";
    const row = el("div", "plan-entry " + status);
    const mark = status === "completed" ? "✓" : status === "in_progress" ? "▸" : "○";
    row.appendChild(el("span", "box", mark));
    row.appendChild(el("span", null, escapeHtml(e.content || e.title || "")));
    card.appendChild(row);
  }
  scrollToBottom();
}

/* ---------- ask_user_question (option picker) ---------- */
const interactions = new Map(); // requestId -> card element

function resolveInteraction(requestId) {
  const card = interactions.get(requestId);
  if (!card) return;
  interactions.delete(requestId);
  if (!card.dataset.answered) {
    card.querySelectorAll("button, input, textarea").forEach((x) => (x.disabled = true));
    const note = el("div", "q-note", "已在其他裝置回答");
    card.appendChild(note);
  }
  setTimeout(() => card.classList.add("dim"), 50);
}

function renderQuestions(msg) {
  if (interactions.has(msg.requestId)) return;
  const card = el("div", "msg perm-card");
  interactions.set(msg.requestId, card);
  card.appendChild(el("div", "perm-title", "Grok 想問你"));

  const answers = {}; // question text -> label | [labels]
  const blocks = [];

  for (const q of msg.questions || []) {
    const block = el("div", "q-block");
    block.appendChild(el("div", "q-text", escapeHtml(q.question || "")));
    const multi = !!q.multiSelect;
    const opts = el("div", "perm-opts");
    const chosen = new Set();

    for (const o of q.options || []) {
      const b = el("button", null, escapeHtml(o.label));
      if (o.description) b.title = o.description;
      b.onclick = () => {
        if (multi) {
          if (chosen.has(o.label)) { chosen.delete(o.label); b.classList.remove("sel"); }
          else { chosen.add(o.label); b.classList.add("sel"); }
          answers[q.question] = Array.from(chosen);
        } else {
          answers[q.question] = o.label;
          opts.querySelectorAll("button").forEach((x) => x.classList.remove("sel"));
          b.classList.add("sel");
        }
        maybeAutoSubmit();
      };
      opts.appendChild(b);
    }
    block.appendChild(opts);

    const free = el("input", "q-free");
    free.placeholder = "或輸入自訂回答…";
    free.oninput = () => {
      if (free.value.trim()) {
        answers[q.question] = free.value.trim();
        opts.querySelectorAll("button").forEach((x) => x.classList.remove("sel"));
      } else {
        delete answers[q.question];
      }
    };
    free.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); maybeAutoSubmit(true); }
    });
    block.appendChild(free);

    blocks.push({ q, block });
    card.appendChild(block);
  }

  const actions = el("div", "perm-opts q-actions");
  const submit = el("button", "primary", "送出回答");
  const skip = el("button", null, "跳過");
  actions.appendChild(submit);
  actions.appendChild(skip);
  card.appendChild(actions);

  const total = (msg.questions || []).length;
  const answeredCount = () => Object.keys(answers).filter((k) => {
    const v = answers[k];
    return Array.isArray(v) ? v.length : v;
  }).length;

  function finish(payload) {
    card.dataset.answered = "1";
    card.querySelectorAll("button, input").forEach((x) => (x.disabled = true));
    interactions.delete(msg.requestId);
    send(payload);
  }
  function maybeAutoSubmit(force) {
    // Single question, single-select: answer immediately on tap.
    const singleTap = total === 1 && !(msg.questions[0] || {}).multiSelect;
    if ((singleTap || force) && answeredCount() === total) {
      finish({ type: "question_response", requestId: msg.requestId, answers });
    }
  }
  submit.onclick = () => {
    if (answeredCount() === 0) return;
    finish({ type: "question_response", requestId: msg.requestId, answers });
  };
  skip.onclick = () => finish({ type: "question_response", requestId: msg.requestId, skipped: true });

  app.thread.appendChild(card);
  scrollToBottom(true);
}

function renderPermission(msg) {
  if (interactions.has(msg.requestId)) return;
  const card = el("div", "msg perm-card");
  interactions.set(msg.requestId, card);
  const tc = msg.toolCall || {};
  card.appendChild(el("div", "perm-title", "需要你的許可"));
  const desc = tc.title || tc.rawInput ? (tc.title || "") + "\n" + textOf(tc.rawInput || tc.content) : "代理程式要求執行一個動作。";
  card.appendChild(el("div", "perm-desc", escapeHtml(desc)));
  const opts = el("div", "perm-opts");
  for (const o of msg.options || []) {
    const b = el("button", (o.kind || "").includes("allow") ? "primary" : null, escapeHtml(o.name || o.optionId));
    b.onclick = () => {
      card.dataset.answered = "1";
      interactions.delete(msg.requestId);
      opts.querySelectorAll("button").forEach((x) => (x.disabled = true));
      b.textContent = "✓ " + b.textContent;
      send({ type: "permission_response", requestId: msg.requestId, optionId: o.optionId });
    };
    opts.appendChild(b);
  }
  card.appendChild(opts);
  app.thread.appendChild(card);
  scrollToBottom(true);
}

/* ---------- scroll ---------- */
function scrollToBottom(force) {
  const m = $("#messages");
  const near = m.scrollHeight - m.scrollTop - m.clientHeight < 160;
  if (force || near) m.scrollTop = m.scrollHeight;
}

/* ---------- composer ---------- */
function send(obj) {
  if (app.ws && app.ws.readyState === WebSocket.OPEN) app.ws.send(JSON.stringify(obj));
}
function setActive(on) {
  app.active = on;
  $("#send").classList.toggle("hidden", on);
  $("#stop").classList.toggle("hidden", !on);
}
function sendPrompt() {
  const input = $("#input");
  const text = input.value.trim();
  if (attachmentsBusy()) return; // wait for uploads to finish
  const { metas, previews } = takeAttachments();
  if (!text && !metas.length) return;
  startNewTurn();
  const bubble = addUserBubble(text || "（附件）");
  if (previews.length) {
    const wrap = el("div", "msg-att");
    for (const p of previews) {
      if (p.isImage && p.previewUrl) {
        const img = document.createElement("img");
        img.src = p.previewUrl;
        wrap.appendChild(img);
      } else {
        wrap.appendChild(el("span", "file-tag", "📎 " + escapeHtml(p.name)));
      }
    }
    bubble.querySelector(".bubble").appendChild(wrap);
  }
  // the agent echoes our text plus generated attachment notes; match on the prefix
  app.pendingEcho = text || "[附加";
  send({ type: "prompt", text, attachments: metas });
  input.value = "";
  autoGrow();
  const sm = document.querySelector("#slash-menu");
  if (sm) sm.classList.add("hidden");
  setActive(true);
  scrollToBottom(true);
}
function autoGrow() {
  const t = $("#input");
  t.style.height = "auto";
  t.style.height = Math.min(t.scrollHeight, window.innerHeight * 0.4) + "px";
}

/* ---------- attachments ---------- */
const attach = { items: [] }; // {file?, meta?, chip, previewUrl, uploading}

function renderAttachBar() {
  const bar = $("#attach-bar");
  bar.classList.toggle("hidden", attach.items.length === 0);
}

async function addFiles(fileList) {
  if (!app.sessionId) return;
  for (const file of fileList) {
    const chip = el("div", "attach-chip uploading");
    const item = { file, meta: null, chip, uploading: true };
    if (file.type.startsWith("image/")) {
      const img = document.createElement("img");
      item.previewUrl = URL.createObjectURL(file);
      img.src = item.previewUrl;
      chip.appendChild(img);
    }
    chip.appendChild(el("span", "a-name", escapeHtml(file.name)));
    const x = el("button", "a-x", "✕");
    x.onclick = () => {
      attach.items = attach.items.filter((i) => i !== item);
      chip.remove();
      renderAttachBar();
    };
    chip.appendChild(x);
    $("#attach-bar").appendChild(chip);
    attach.items.push(item);
    renderAttachBar();

    const form = new FormData();
    form.append("file", file, file.name);
    try {
      const res = await fetch(`/api/sessions/${app.sessionId}/upload`, { method: "POST", body: form });
      const j = await res.json();
      if (!res.ok || j.error) throw new Error(j.error || res.status);
      item.meta = j;
      item.uploading = false;
      chip.classList.remove("uploading");
    } catch (e) {
      chip.querySelector(".a-name").textContent = file.name + "（上傳失敗）";
      item.failed = true;
      chip.classList.remove("uploading");
    }
  }
}

function takeAttachments() {
  const ready = attach.items.filter((i) => i.meta && !i.failed);
  const metas = ready.map((i) => i.meta);
  const previews = ready.map((i) => ({ name: i.meta.name, isImage: i.meta.isImage, previewUrl: i.previewUrl }));
  for (const i of attach.items) i.chip.remove();
  attach.items = [];
  renderAttachBar();
  return { metas, previews };
}

function attachmentsBusy() {
  return attach.items.some((i) => i.uploading);
}

$("#attach").onclick = () => $("#file-input").click();
$("#file-input").addEventListener("change", (e) => {
  addFiles(Array.from(e.target.files || []));
  e.target.value = "";
});
$("#input").addEventListener("paste", (e) => {
  const files = Array.from(e.clipboardData?.files || []);
  if (files.length) { e.preventDefault(); addFiles(files); }
});
{
  const m = $("#messages");
  m.addEventListener("dragover", (e) => { e.preventDefault(); m.classList.add("dragover"); });
  m.addEventListener("dragleave", () => m.classList.remove("dragover"));
  m.addEventListener("drop", (e) => {
    e.preventDefault();
    m.classList.remove("dragover");
    addFiles(Array.from(e.dataTransfer?.files || []));
  });
}

/* ---------- wiring ---------- */
$("#refresh").onclick = loadSessions;
$("#new-chat").onclick = openNewChat;
$("#nc-cancel").onclick = closeNewChat;
$("#nc-create").onclick = createSession;
$("#newchat-backdrop").onclick = closeNewChat;
$("#nc-cwd").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); createSession(); }
});
$("#back").onclick = backToPicker;
$("#send").onclick = sendPrompt;
$("#stop").onclick = () => send({ type: "cancel" });
$("#input").addEventListener("input", autoGrow);
$("#input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    sendPrompt();
  }
});

loadSessions();
