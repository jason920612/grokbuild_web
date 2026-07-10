"use strict";
/* Settings & status bottom sheet + slash-command autocomplete.
   Depends on globals from app.js: $, el, escapeHtml, app, send. */

const settings = { commands: [], lastState: null };

/* ---------- sheet plumbing ---------- */
function openSheet() {
  $("#sheet").classList.remove("hidden");
  $("#sheet-backdrop").classList.remove("hidden");
}
function closeSheet() {
  $("#sheet").classList.add("hidden");
  $("#sheet-backdrop").classList.add("hidden");
}
$("#sheet-backdrop").onclick = closeSheet;

function section(title) {
  const s = el("div", "set-section");
  s.appendChild(el("h3", null, title));
  return s;
}
function row(label, hint) {
  const r = el("div", "set-row");
  const l = el("div", "set-label", escapeHtml(label));
  if (hint) l.appendChild(el("span", "hint", escapeHtml(hint)));
  r.appendChild(l);
  return r;
}
function toggle(checked, onchange) {
  const t = el("label", "toggle");
  const i = document.createElement("input");
  i.type = "checkbox"; i.checked = !!checked;
  i.onchange = () => onchange(i.checked, i);
  t.appendChild(i);
  t.appendChild(el("span", "knob"));
  return t;
}
function bar(pct, markPct) {
  const b = el("div", "bar");
  const f = el("div", "fill" + (pct >= 90 ? " err" : pct >= 70 ? " warn" : ""));
  f.style.width = Math.min(100, Math.max(0, pct)) + "%";
  b.appendChild(f);
  if (markPct != null) {
    const m = el("div", "mark");
    m.style.left = markPct + "%";
    b.appendChild(m);
  }
  return b;
}
function barRow(label, valText, pct, markPct) {
  const r = el("div", "bar-row");
  const top = el("div", "bar-top");
  top.appendChild(el("span", null, escapeHtml(label)));
  top.appendChild(el("span", "val", escapeHtml(valText)));
  r.appendChild(top);
  r.appendChild(bar(pct, markPct));
  return r;
}
const fmtNum = (n) => (n == null ? "?" : Number(n).toLocaleString());

/* ---------- data ---------- */
async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(url + " -> " + res.status);
  return res.json();
}

/* ---------- render ---------- */
async function openSettings() {
  const body = $("#sheet-body");
  body.innerHTML = '<div class="set-loading">載入中…</div>';
  openSheet();

  const inChat = !!app.sessionId;
  const jobs = [getJSON("/api/status"), getJSON("/api/config")];
  if (inChat) jobs.push(getJSON(`/api/sessions/${app.sessionId}/settings`));
  let status = {}, cfg = { entries: [] }, sess = null;
  try {
    const r = await Promise.allSettled(jobs);
    if (r[0].status === "fulfilled") status = r[0].value;
    if (r[1].status === "fulfilled") cfg = r[1].value;
    if (inChat && r[2] && r[2].status === "fulfilled") sess = r[2].value;
  } catch (_) {}

  body.innerHTML = "";

  /* --- appearance --- */
  const ap = section("外觀");
  const themeRow = row("主題", "與 grok.com 類似的深色 / 淺色");
  const themeSel = document.createElement("select");
  for (const [val, label] of [["dark", "Dark"], ["light", "Light"]]) {
    const o = document.createElement("option");
    o.value = val; o.textContent = label;
    if (val === (typeof currentTheme === "function" ? currentTheme() : "dark")) o.selected = true;
    themeSel.appendChild(o);
  }
  themeSel.onchange = () => {
    if (typeof applyTheme === "function") applyTheme(themeSel.value);
  };
  themeRow.appendChild(themeSel);
  ap.appendChild(themeRow);
  body.appendChild(ap);

  /* --- usage / limits --- */
  const su = section("用量與限制");
  const meta = (status.subscription || {}).meta || {};
  const bc = (status.billing || {}).config || {};
  const tierRow = row("方案", meta.email || "");
  tierRow.appendChild(el("span", "set-value", escapeHtml(meta.subscription_tier || status.billing?.subscription_tier || "未知")));
  su.appendChild(tierRow);

  if (bc.creditUsagePercent != null) {
    su.appendChild(barRow("方案額度（本期）", bc.creditUsagePercent.toFixed(1) + "% 已用", bc.creditUsagePercent));
  }
  const cap = bc.onDemandCap?.val, used = bc.onDemandUsed?.val;
  if (cap != null) {
    su.appendChild(barRow("隨用付費 On-demand", `${fmtNum(used)} / ${fmtNum(cap)}`, cap ? (used / cap) * 100 : 0));
  }
  if (bc.billingPeriodEnd) {
    const end = new Date(bc.billingPeriodEnd);
    const hrs = Math.max(0, (end - Date.now()) / 36e5);
    const rr = row("額度重置", end.toLocaleString());
    rr.appendChild(el("span", "set-value", hrs > 48 ? Math.round(hrs / 24) + " 天後" : Math.round(hrs) + " 小時後"));
    su.appendChild(rr);
  }
  if (status.error) su.appendChild(el("div", "set-note", "狀態讀取失敗：" + escapeHtml(status.error)));
  body.appendChild(su);

  /* --- this session --- */
  if (inChat && sess) {
    const ss = section("此對話");
    const ctx = sess.info?.context;
    if (ctx) {
      ss.appendChild(barRow(
        "上下文使用",
        `${fmtNum(ctx.used)} / ${fmtNum(ctx.total)}（${ctx.usagePct}%）`,
        ctx.usagePct,
        ctx.autoCompactThresholdPercent
      ));
      ss.appendChild(el("div", "set-note",
        `黃線 ${ctx.autoCompactThresholdPercent}% = 自動壓縮門檻 · 回合 ${sess.info.turns ?? "?"} · 訊息 ${ctx.messageCount ?? "?"}`));
    }

    const models = (sess.state?.models?.availableModels) || [];
    const mr = row("模型");
    const msel = document.createElement("select");
    for (const m of models) {
      const o = document.createElement("option");
      o.value = m.modelId; o.textContent = m.name || m.modelId;
      if (m.modelId === sess.state.modelId) o.selected = true;
      msel.appendChild(o);
    }
    msel.onchange = () => postSetting({ modelId: msel.value });
    mr.appendChild(msel);
    ss.appendChild(mr);

    const cur = models.find((m) => m.modelId === (settings.lastState?.modelId || sess.state.modelId));
    const efforts = cur?._meta?.reasoningEfforts || [];
    if (efforts.length) {
      const er = row("推理力度", "影響品質與速度");
      const esel = document.createElement("select");
      for (const e of efforts) {
        const o = document.createElement("option");
        o.value = e.id; o.textContent = e.label || e.id;
        if (e.id === sess.state.modeId) o.selected = true;
        esel.appendChild(o);
      }
      esel.onchange = () => postSetting({ modeId: esel.value });
      er.appendChild(esel);
      ss.appendChild(er);
    }

    if (sess.live) {
      const st = row("目前狀態");
      st.appendChild(el("span", "set-value",
        `${sess.live.activity || "?"} · always-approve ${sess.live.yolo ? "開" : "關"}`));
      ss.appendChild(st);
    }
    body.appendChild(ss);
    settings.commands = sess.commands || [];
  }

  /* --- global config --- */
  const gs = section("全域設定（config.toml）");
  for (const e of cfg.entries || []) {
    const r = row(e.label || e.key, e.hint);
    if (e.type === "bool") {
      r.appendChild(toggle(e.value, async (v, input) => {
        input.disabled = true;
        try { await putConfig(e.section, e.key, v); } catch (_) { input.checked = !v; }
        input.disabled = false;
      }));
    } else if (e.type === "choice") {
      const sel = document.createElement("select");
      for (const opt of e.options) {
        const o = document.createElement("option");
        o.value = opt; o.textContent = opt;
        if (opt === e.value) o.selected = true;
        sel.appendChild(o);
      }
      if (e.value != null && !e.options.includes(e.value)) {
        const o = document.createElement("option");
        o.value = e.value; o.textContent = e.value; o.selected = true;
        sel.appendChild(o);
      }
      sel.onchange = () => putConfig(e.section, e.key, sel.value);
      r.appendChild(sel);
    } else {
      const inp = document.createElement("input");
      inp.type = e.type === "int" ? "number" : "text";
      inp.value = e.value ?? "";
      inp.onchange = () => putConfig(e.section, e.key, e.type === "int" ? Number(inp.value) : inp.value);
      r.appendChild(inp);
    }
    gs.appendChild(r);
  }
  gs.appendChild(el("div", "set-note", "改動立即寫入 config.toml；已開啟的終端機 TUI 需重啟才會套用。"));
  body.appendChild(gs);

  /* --- slash commands --- */
  if (inChat && settings.commands.length) {
    const cs = section(`斜線指令（${settings.commands.length}）`);
    const wrap = el("div", "chip-wrap");
    for (const c of settings.commands) {
      const chip = el("button", "chip", "/" + escapeHtml(c.name));
      chip.title = c.description || "";
      chip.onclick = () => {
        const input = $("#input");
        input.value = "/" + c.name + " ";
        closeSheet();
        input.focus();
        input.dispatchEvent(new Event("input"));
      };
      wrap.appendChild(chip);
    }
    cs.appendChild(wrap);
    body.appendChild(cs);
  }
}

async function putConfig(sectionName, key, value) {
  const res = await fetch("/api/config", {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ section: sectionName, key, value }),
  });
  if (!res.ok) throw new Error("config write failed");
}

async function postSetting(payload) {
  try {
    const res = await fetch(`/api/sessions/${app.sessionId}/settings`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const j = await res.json();
    if (j.state) { settings.lastState = j.state; return; }
    if (j.error) {
      // bridge errors carry a JSON-RPC error as a JSON string — dig out the message
      let msg = j.error;
      try { msg = JSON.parse(j.error).message || msg; } catch (_) {}
      alert(msg);
      openSettings(); // re-render with real state
    }
  } catch (_) {}
}

/* ---------- slash autocomplete ---------- */
const slashMenu = $("#slash-menu");

async function ensureCommands() {
  if (settings.commands.length || !app.sessionId) return;
  try {
    const s = await getJSON(`/api/sessions/${app.sessionId}/settings`);
    settings.commands = s.commands || [];
  } catch (_) {}
}

function updateSlashMenu() {
  const input = $("#input");
  const v = input.value;
  if (!v.startsWith("/") || v.includes("\n") || v.length > 60) {
    slashMenu.classList.add("hidden");
    return;
  }
  ensureCommands().then(() => {
    const q = v.slice(1).toLowerCase();
    const hits = settings.commands.filter((c) => c.name.toLowerCase().startsWith(q)).slice(0, 8);
    slashMenu.innerHTML = "";
    if (!hits.length) { slashMenu.classList.add("hidden"); return; }
    for (const c of hits) {
      const item = el("div", "slash-item");
      item.appendChild(el("div", "s-name", "/" + escapeHtml(c.name)));
      if (c.description) item.appendChild(el("div", "s-desc", escapeHtml(c.description)));
      item.onclick = () => {
        input.value = "/" + c.name + " ";
        slashMenu.classList.add("hidden");
        input.focus();
      };
      slashMenu.appendChild(item);
    }
    slashMenu.classList.remove("hidden");
  });
}

$("#input").addEventListener("input", updateSlashMenu);
$("#settings-btn").onclick = openSettings;
