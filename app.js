"use strict";

const $ = (id) => document.getElementById(id);
const store = {
  get(key, fallback) {
    try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* privat / blockiert */ }
  },
};

const state = {
  data: null,
  tab: store.get("tab", "all"),
  read: new Set(store.get("read", [])),
  loadedAt: 0,
};
const controls = ["search", "range", "source", "sort", "onlyTop", "hideRead"];

function restoreControls() {
  const saved = store.get("controls", {});
  for (const id of controls) {
    if (!(id in saved) || id === "search") continue;
    const el = $(id);
    if (el.type === "checkbox") el.checked = saved[id]; else el.value = saved[id];
  }
}
function saveControls() {
  const out = {};
  for (const id of controls) { const el = $(id); out[id] = el.type === "checkbox" ? el.checked : el.value; }
  store.set("controls", out);
}

function timeAgo(date) {
  const mins = Math.round((Date.now() - date.getTime()) / 60000);
  if (mins < 1) return "gerade eben";
  if (mins < 60) return `vor ${mins} Min.`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `vor ${hrs} Std.`;
  const days = Math.round(hrs / 24);
  if (days < 7) return days === 1 ? "gestern" : `vor ${days} Tagen`;
  return date.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" });
}

function tabDefs() {
  const d = state.data;
  return [
    { id: "all", label: "Alle", test: () => true },
    { id: "top", label: "★ Top", test: (i) => i.top },
    ...d.combos.map((c) => ({ id: c.id, label: c.label, test: (i) => i.combos.includes(c.id) })),
    ...d.topics.map((t) => ({ id: t.id, label: t.label, test: (i) => i.topics.includes(t.id) })),
  ];
}

function baseFilter() {
  const hours = Number($("range").value);
  const cutoff = Date.now() - hours * 3600e3;
  const q = $("search").value.trim().toLowerCase();
  const src = $("source").value;
  const onlyTop = $("onlyTop").checked;
  const hideRead = $("hideRead").checked;
  return state.data.items.filter((i) =>
    i._date.getTime() >= cutoff &&
    (!src || i.source === src || i.feed === src) &&
    (!onlyTop || i.top) &&
    (!hideRead || !state.read.has(i.id)) &&
    (!q || i._haystack.includes(q))
  );
}

function renderTabs(items) {
  const nav = $("tabs");
  nav.replaceChildren();
  for (const tab of tabDefs()) {
    const n = items.filter(tab.test).length;
    const btn = document.createElement("button");
    btn.className = "tab";
    btn.type = "button";
    btn.setAttribute("aria-pressed", String(tab.id === state.tab));
    btn.append(tab.label);
    const count = document.createElement("span");
    count.className = "n";
    count.textContent = n;
    btn.append(count);
    btn.addEventListener("click", () => { state.tab = tab.id; store.set("tab", tab.id); render(); });
    nav.append(btn);
  }
}

function render() {
  if (!state.data) return;
  const defs = tabDefs();
  if (!defs.some((t) => t.id === state.tab)) state.tab = "all";
  const base = baseFilter();
  renderTabs(base);
  const tab = defs.find((t) => t.id === state.tab);
  const items = base.filter(tab.test);
  if ($("sort").value === "score") items.sort((a, b) => b.score - a.score || b._date - a._date);

  const topicById = Object.fromEntries(state.data.topics.map((t) => [t.id, t]));
  const comboById = Object.fromEntries(state.data.combos.map((c) => [c.id, c]));
  const tpl = $("card").content;
  const frag = document.createDocumentFragment();
  for (const item of items.slice(0, 300)) {
    const li = tpl.firstElementChild.cloneNode(true);
    li.classList.toggle("is-top", item.top);
    li.classList.toggle("read", state.read.has(item.id));
    li.querySelector(".src").textContent = item.source + (item.also?.length ? ` +${item.also.length}` : "");
    if (item.also?.length) li.querySelector(".src").title = "Auch bei: " + item.also.join(", ");
    const time = li.querySelector("time");
    time.dateTime = item.published;
    time.textContent = timeAgo(item._date);
    time.title = item._date.toLocaleString("de-DE");
    const a = li.querySelector(".title");
    a.href = item.url;
    a.textContent = item.title;
    a.addEventListener("click", () => markRead(item.id, li));
    li.querySelector(".summary").textContent = item.summary || "";
    const tags = li.querySelector(".tags");
    for (const c of item.combos) {
      const s = document.createElement("span");
      s.className = "tag combo";
      s.textContent = comboById[c]?.label ?? c;
      tags.append(s);
    }
    for (const t of item.topics) {
      const s = document.createElement("span");
      s.className = "tag";
      s.style.setProperty("--c", topicById[t]?.color ?? "#888");
      s.textContent = topicById[t]?.label ?? t;
      tags.append(s);
    }
    frag.append(li);
  }
  $("list").replaceChildren(frag);
  $("empty").hidden = items.length > 0;
  $("count").textContent = `${items.length} Meldungen`;
}

function markRead(id, li) {
  state.read.add(id);
  li.classList.add("read");
  const known = new Set(state.data.items.map((i) => i.id));
  store.set("read", [...state.read].filter((x) => known.has(x)));
}

function renderMeta() {
  const d = state.data;
  const gen = new Date(d.generated_at);
  $("updated").textContent = `Stand: ${gen.toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" })} (${timeAgo(gen)})`;

  const select = $("source");
  const current = select.value;
  const names = [...new Set(d.items.map((i) => i.source))].sort((a, b) => a.localeCompare(b, "de"));
  select.replaceChildren(new Option("Alle Quellen", ""), ...names.map((n) => new Option(n, n)));
  select.value = names.includes(current) ? current : "";

  const ok = d.sources.filter((s) => s.ok).length;
  $("health").textContent = `Quellen: ${ok} von ${d.sources.length} erreichbar`;
  $("sources").replaceChildren(...d.sources.map((s) => {
    const li = document.createElement("li");
    li.textContent = s.ok ? `${s.name} – ${s.count} relevante Meldungen` : `${s.name} – nicht erreichbar (${s.error})`;
    if (!s.ok) li.className = "bad";
    return li;
  }));
}

async function load() {
  const btn = $("refresh");
  btn.classList.add("spin");
  try {
    const res = await fetch(`data/news.json?t=${Date.now()}`, { cache: "no-store" });
    if (!res.ok) throw new Error(res.status);
    const data = await res.json();
    for (const i of data.items) {
      i._date = new Date(i.published);
      i._haystack = `${i.title} ${i.summary} ${i.source} ${(i.also || []).join(" ")}`.toLowerCase();
    }
    state.data = data;
    state.loadedAt = Date.now();
    renderMeta();
    render();
  } catch (err) {
    $("updated").textContent = state.data
      ? "Offline – zeige zuletzt geladenen Stand"
      : "Noch keine Daten. Läuft der GitHub-Workflow schon?";
  } finally {
    btn.classList.remove("spin");
  }
}

restoreControls();
for (const id of controls) {
  $(id).addEventListener(id === "search" ? "input" : "change", () => { saveControls(); render(); });
}
$("refresh").addEventListener("click", load);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && Date.now() - state.loadedAt > 10 * 60e3) load();
});
load();

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").catch(() => {});
}
