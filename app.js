"use strict";

const byId = (id) => document.getElementById(id);
const statusNames = { idle: "待运行", running: "采集中", stopping: "停止中", stopped: "已停止", completed: "已完成", error: "运行失败" };
const integerFields = ["max_images", "max_pages", "download_workers", "image_long_edge", "crop_bottom", "retries"];
const decimalFields = ["request_interval", "timeout"];
const listFields = ["user_ids", "album_urls", "album_names"];
let selectedTool = "weibo";
let currentView = "crawler";
let cropExporting = false;
let toolVersion = 0;
let xhsMode = "note";
let pinterestMode = "pin";
const pinterestInputs = { pin: "pinterest_urls", board: "pinterest_board_urls", section: "pinterest_section_urls", profile: "pinterest_profile_urls", short: "pinterest_short_urls", search: "pinterest_keywords" };
const pinterestLabels = { pin: "Pin", board: "画板", section: "分区", profile: "主页", short: "短链接", search: "关键词" };
const toolDrafts = new Map();
const toolNames = { weibo: "微博相册", xhs: "小红书图集", google: "谷歌图片", pinterest: "Pinterest 图集" };
let connected = false;
let state = null;
let busy = false;
let targetMode = "uid";
let dirty = false;
let loaded = false;
let toastTimer;
let clearBefore = 0;
let logSignature = "";
let historySignature = "";
let cropImage = null;
let cropHeight = 0;
let cropWidth = 0;
let cropCandidate = 0;
let cropDragging = false;
let cropLoadVersion = 0;
let cropLoading = false;
let directorySelecting = false;
let galleryImages = [];
let galleryTotal = 0;
let galleryDirectory = "";
let galleryCatalog = null;
let galleryLoading = false;
let galleryVersion = 0;
let imageIndex = 0;
let imageVersion = 0;

function number(value) {
  return Number(value || 0).toLocaleString("en-US");
}

function elapsed(value) {
  const seconds = Math.max(0, Math.floor(value || 0));
  return [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, seconds % 60].map((part) => String(part).padStart(2, "0")).join(":");
}

function bytes(value) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
  return `${(value / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function toast(message, error = false) {
  clearTimeout(toastTimer);
  byId("toast-text").textContent = message;
  byId("toast").classList.toggle("error", error);
  byId("toast-icon").setAttribute("href", error ? "#icon-error" : "#icon-check");
  byId("toast").hidden = false;
  toastTimer = setTimeout(() => { byId("toast").hidden = true; }, error ? 6500 : 3000);
}

async function api(path, payload, timeout = 10000, toolId = selectedTool) {
  if (location.protocol === "file:") throw new Error("请通过 start.bat 打开控制台");
  const response = await fetch(`${path}${path.includes("?") ? "&" : "?"}tool=${encodeURIComponent(toolId)}`, {
    method: payload === undefined ? "GET" : "POST",
    headers: payload === undefined ? {} : { "Content-Type": "application/json" },
    body: payload === undefined ? undefined : JSON.stringify(payload),
    signal: timeout ? AbortSignal.timeout(timeout) : undefined,
    cache: "no-store",
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "请求失败");
  return data;
}

function setDirty(value) {
  dirty = value;
  byId("save-state").classList.toggle("unsaved", value);
  const dot = document.createElement("span");
  dot.className = "status-dot";
  byId("save-state").replaceChildren(dot, document.createTextNode(value ? "有未保存修改" : "设置已保存"));
}

function setTarget(mode, changed = true) {
  targetMode = mode;
  byId("uid-mode").classList.toggle("active", mode === "uid");
  byId("album-mode").classList.toggle("active", mode === "album");
  byId("uid-mode").setAttribute("aria-pressed", String(mode === "uid"));
  byId("album-mode").setAttribute("aria-pressed", String(mode === "album"));
  byId("uid-field").hidden = mode !== "uid";
  byId("album-names-field").hidden = mode !== "uid";
  byId("album-field").hidden = mode !== "album";
  if (changed) setDirty(true);
}

function populate(config) {
  for (const [key, value] of Object.entries(config)) {
    if (selectedTool === "pinterest" && key === "pinterest_urls") {
      populatePinterestTargets(value);
      continue;
    }
    const pinterestFields = ["min_width", "min_height", "max_file_mb", "proxy", "browser_channel", "headless", "deduplicate"];
    const input = byId(selectedTool === "pinterest" && pinterestFields.includes(key) ? `pinterest_${key}` : (selectedTool === "google" && key === "keywords" ? "google_keywords" : key));
    if (!input) continue;
    if (input.type === "checkbox") input.checked = value;
    else input.value = Array.isArray(value) ? value.join(key === "album_names" ? ", " : "\n") : value;
  }
  setTarget(config.album_urls?.length ? "album" : "uid", false);
  setXhsTarget(config.target_mode || "note", false);
  syncImageSettings();
  byId("filename-preview").textContent = `${config.filename_prefix}_0001.jpg`;
  setDirty(false);
  loaded = true;
}

function collectConfig() {
  const invalid = [...document.querySelectorAll("#config-fields input, #config-fields textarea, #image-settings input")].find((input) => !input.checkValidity());
  if (invalid) {
    showView(invalid.closest("#image-settings") ? "images" : "crawler");
    activateTab(invalid.closest("#advanced-settings") ? "advanced-tab" : "basic-tab", ["basic-tab", "advanced-tab"], ["basic-settings", "advanced-settings"]);
    invalid.reportValidity();
    throw new Error("请检查设置中的数值范围");
  }
  const result = {};
  for (const key of integerFields) result[key] = Number(byId(key).value);
  for (const key of decimalFields) result[key] = Number(byId(key).value);
  for (const key of listFields) result[key] = byId(key).value.split(key === "album_urls" ? /[\n\r]+/ : /[,，\n\r]+/).map((value) => value.trim()).filter(Boolean);
  result.user_ids = targetMode === "uid" ? result.user_ids : [];
  result.album_urls = targetMode === "album" ? result.album_urls : [];
  for (const key of ["output_dir", "filename_prefix"]) result[key] = byId(key).value.trim();
  for (const key of ["original_size", "include_avatar", "resize_enabled"]) result[key] = byId(key).checked;
  if (selectedTool === "xhs") {
    for (const key of ["note_urls", "profile_urls", "keywords"]) result[key] = byId(key).value.split(/[\n\r]+/).map(value => value.trim()).filter(Boolean);
    result.target_mode = xhsMode;
    result.search_limit = Number(byId("search_limit").value);
    result.sort_type = Number(byId("sort_type").value);
  }
  if (selectedTool === "google") {
    result.keywords = byId("google_keywords").value.split(/[\n\r]+/).map(value => value.trim()).filter(Boolean);
    for (const key of ["min_width", "min_height", "max_file_mb"]) result[key] = Number(byId(key).value);
    for (const key of ["google_size", "google_type", "google_color", "search_language", "proxy", "browser_channel"]) result[key] = byId(key).value.trim();
    for (const key of ["safe_search", "headless", "deduplicate"]) result[key] = byId(key).checked;
  }
  if (selectedTool === "pinterest") {
    result.pinterest_urls = [...new Set(Object.entries(pinterestInputs).flatMap(([mode,id]) => inputLines(id).map(value => mode === "search" && !/^https?:\/\//i.test(value)
      ? `https://www.pinterest.com/search/pins/?${new URLSearchParams({q:value})}` : value)))];
    for (const key of ["min_width", "min_height", "max_file_mb"]) result[key] = Number(byId(`pinterest_${key}`).value);
    for (const key of ["proxy", "browser_channel"]) result[key] = byId(`pinterest_${key}`).value.trim();
    for (const key of ["headless", "deduplicate"]) result[key] = byId(`pinterest_${key}`).checked;
    result.include_sections = byId("include_sections").checked;
  }
  return result;
}

function inputLines(id) {
  return byId(id).value.split(/[\n\r]+/).map(value => value.trim()).filter(Boolean);
}

function pinterestTargetType(value) {
  if (/^https?:\/\/[^/]+\/search\/pins\//i.test(value)) return "search";
  if (/^https?:\/\/pin\.it\//i.test(value)) return "short";
  const path = value.replace(/^https?:\/\/[^/]+\//i, "").replace(/^\/+|\/+$/g, "").split(/[?#]/)[0];
  const segments = path.split("/").filter(Boolean);
  if (/^\d+$/.test(path) || segments[0] === "pin") return "pin";
  if (segments.length === 1 || (segments.length === 2 && ["_saved", "_created", "pins", "boards"].includes(segments[1]))) return "profile";
  return segments.length === 3 ? "section" : "board";
}

function populatePinterestTargets(values) {
  const groups = Object.fromEntries(Object.keys(pinterestInputs).map(mode => [mode, []]));
  for (const value of values || []) {
    const mode = pinterestTargetType(value);
    groups[mode].push(mode === "search" ? new URL(value).searchParams.get("q") || value : value);
  }
  for (const [mode, id] of Object.entries(pinterestInputs)) byId(id).value = groups[mode].join("\n");
  setPinterestTarget(Object.keys(groups).find(mode => groups[mode].length) || "pin");
}

function setPinterestTarget(mode) {
  pinterestMode = mode;
  for (const name of Object.keys(pinterestInputs)) {
    byId(`pinterest-${name}-mode`).classList.toggle("active", name === mode);
    byId(`pinterest-${name}-mode`).setAttribute("aria-pressed", String(name === mode));
    byId(`pinterest-${name}-field`).hidden = name !== mode;
  }
  updatePinterestSummary();
}

function updatePinterestSummary() {
  const counts = Object.entries(pinterestInputs).map(([mode,id]) => [mode,inputLines(id).length]);
  const total = counts.reduce((sum,[,count]) => sum+count,0);
  byId("pinterest-input-summary").textContent = total
    ? `已填写 ${total} 项：${counts.filter(([,count]) => count).map(([mode,count]) => `${pinterestLabels[mode]} ${count}`).join(" · ")}。开始采集时一起执行。`
    : "各分类可同时填写，开始采集时一起执行。";
}

function setXhsTarget(mode, changed = true) {
  xhsMode = mode;
  for (const name of ["note", "profile", "search"]) {
    byId(`xhs-${name}-mode`).classList.toggle("active", name === mode);
    byId(`xhs-${name}-mode`).setAttribute("aria-pressed", String(name === mode));
    byId(`xhs-${name}-field`).hidden = name !== mode;
  }
  byId("search_limit").disabled = selectedTool !== "xhs" || mode !== "search";
  if (changed) setDirty(true);
}

function configureTool() {
  const isXhs = selectedTool === "xhs";
  const isGoogle = selectedTool === "google";
  const isPinterest = selectedTool === "pinterest";
  byId("page-title").textContent = toolNames[selectedTool];
  byId("crop-apply").textContent = `保存到${toolNames[selectedTool]}`;
  byId("image-settings-target").textContent = `${toolNames[selectedTool]} · 后续采集使用此设置`;
  updateViewNavigation();
  byId("weibo-targets").hidden = selectedTool !== "weibo";
  byId("xhs-targets").hidden = !isXhs;
  byId("pinterest-targets").hidden = !isPinterest;
  byId("pinterest-advanced").hidden = !isPinterest;
  byId("pinterest-advanced").querySelectorAll("input,select").forEach(input => { input.disabled = !isPinterest; });
  byId("cookie-optional").hidden = !isPinterest;
  byId("google-targets").hidden = !isGoogle;
  byId("google-advanced").hidden = !isGoogle;
  byId("credentials-settings").hidden = isGoogle;
  byId("cookie").disabled = isGoogle;
  byId("original-setting").hidden = isGoogle || isPinterest;
  byId("max-pages-label").textContent = isGoogle ? "每个关键词滚动上限" : (isPinterest ? "每个列表的翻页 / 滚动上限" : "每个目标页面上限");
  byId("avatar-setting").hidden = selectedTool !== "weibo";
  byId("google-advanced").querySelectorAll("input,select").forEach(input => { input.disabled = !isGoogle; });
  byId("album-label").textContent = isGoogle ? "当前搜索" : (isXhs ? "当前笔记" : "当前相册");
  byId("include_avatar").disabled = selectedTool !== "weibo";
  for (const id of ["weibo", "xhs", "google", "pinterest"]) {
    byId(`${id}-targets`).querySelectorAll("input,textarea,select,button").forEach(input => { input.disabled = id !== selectedTool; });
  }
  const site = isPinterest ? "www.pinterest.com" : (isXhs ? "www.xiaohongshu.com" : "weibo.cn");
  byId("cookie-site").href = `https://${site}`;
  byId("cookie-site").firstChild.textContent = site + " ";
  byId("cookie").placeholder = isPinterest ? "公开内容可留空，需要登录时粘贴完整 Cookie" : isXhs ? "粘贴请求头完整 Cookie（含 a1、web_session）" : "粘贴登录后的 Cookie";
}

function updateViewNavigation() {
  const images = currentView === "images";
  byId("breadcrumb-group").textContent = images ? "图片工具" : "爬虫工具";
  byId("breadcrumb-tool").textContent = images ? "图片裁剪" : toolNames[selectedTool];
  document.title = `${images ? "图片裁剪" : toolNames[selectedTool]} · SpiderRoom`;
  for (const id of ["weibo", "xhs", "google", "pinterest", "crop"]) {
    const active = images ? id === "crop" : id === selectedTool;
    byId(`${id}-nav`).classList.toggle("selected", active);
    if (active) byId(`${id}-nav`).setAttribute("aria-current", "page");
    else byId(`${id}-nav`).removeAttribute("aria-current");
  }
}

function showView(view) {
  currentView = view;
  byId("crawler-page").hidden = view !== "crawler";
  byId("image-tools-page").hidden = view !== "images";
  updateViewNavigation();
  byId("main-content").scrollTo({ top: 0, behavior: "auto" });
  if (view === "images") requestAnimationFrame(refreshCropPreview);
}

function setSidebarCollapsed(collapsed, persist = true) {
  document.querySelector(".app-shell").classList.toggle("sidebar-collapsed", collapsed);
  const button = byId("sidebar-toggle");
  button.setAttribute("aria-expanded", String(!collapsed));
  button.setAttribute("aria-label", collapsed ? "展开侧边栏" : "收起侧边栏");
  button.title = collapsed ? "展开侧边栏" : "收起侧边栏";
  if (persist) {
    try { localStorage.setItem("spiderroom.sidebarCollapsed", String(collapsed)); } catch {}
  }
  setTimeout(() => { if (currentView === "images") drawCrop(); }, 200);
}

async function switchTool(id) {
  if (id === selectedTool) { showView("crawler"); return; }
  if (busy || directorySelecting || !loaded) return;
  await action(async () => {
    const fields = {};
    document.querySelectorAll("#config-form input, #config-form textarea, #config-form select, #image-settings input").forEach(input => {
      if (input.id) fields[input.id] = input.type === "checkbox" ? input.checked : input.value;
    });
    toolDrafts.set(selectedTool, { fields, dirty, targetMode, xhsMode, pinterestMode, clearBefore });
    const [config, snapshot] = await Promise.all([api("/api/config", undefined, 10000, id), api("/api/state", undefined, 10000, id)]);
    selectedTool = id;
    toolVersion++;
    configureTool();
    populate(config);
    byId("cookie").type = "password";
    byId("show-cookie").setAttribute("aria-pressed", "false");
    byId("show-cookie").setAttribute("aria-label", "显示 Cookie");
    clearBefore = 0;
    const draft = toolDrafts.get(id);
    if (draft) {
      for (const [key, value] of Object.entries(draft.fields)) {
        if (byId(key).type === "checkbox") byId(key).checked = value;
        else byId(key).value = value;
      }
      setTarget(draft.targetMode, false);
      setXhsTarget(draft.xhsMode, false);
      setPinterestTarget(draft.pinterestMode || "pin");
      syncImageSettings();
      byId("filename-preview").textContent = `${byId("filename_prefix").value}_0001.jpg`;
      setDirty(draft.dirty);
      clearBefore = draft.clearBefore;
    }
    state = null;
    logSignature = historySignature = "";
    render(snapshot);
    showView("crawler");
    byId("main-content").scrollTo({ top: 0, behavior: "auto" });
  });
}

function activateTab(selected, tabs, panels) {
  tabs.forEach((id, index) => {
    const active = id === selected;
    byId(id).classList.toggle("active", active);
    byId(id).setAttribute("aria-selected", String(active));
    byId(id).tabIndex = active ? 0 : -1;
    byId(panels[index]).hidden = !active;
  });
}

function wireTabs(tabs, panels) {
  tabs.forEach((id, index) => {
    byId(id).addEventListener("click", () => activateTab(id, tabs, panels));
    byId(id).addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
      activateTab(tabs[next], tabs, panels);
      byId(tabs[next]).focus();
    });
  });
}

function updateConnection(value) {
  connected = value;
  for (const id of ["connection-dot", "service-dot", "footer-dot"]) byId(id).className = `status-dot${value ? " online" : ""}`;
  byId("connection-text").textContent = value ? "本地服务在线" : "服务未连接";
  byId("service-text").textContent = value ? "本地服务在线" : "服务未连接";
  byId("footer-state").textContent = value ? "系统就绪" : "服务未连接";
  updateButtons();
}

function updateButtons() {
  const running = state?.status === "running";
  const stopping = state?.status === "stopping";
  const active = running || stopping;
  byId("run-button").disabled = busy || directorySelecting || stopping || !connected || !loaded;
  byId("run-button").classList.toggle("stop-button", active);
  byId("run-icon").setAttribute("href", active ? "#icon-stop" : "#icon-play");
  byId("run-label").textContent = busy ? "处理中…" : stopping ? "停止中…" : running ? "停止采集" : "开始采集";
  byId("save-button").disabled = busy || directorySelecting || active || !connected || !loaded;
  byId("config-fields").disabled = busy || active || !loaded;
  byId("open-output").disabled = busy || !connected;
  byId("open-gallery").disabled = !connected || !loaded;
  byId("select-output").disabled = directorySelecting || active || busy || !connected || !loaded;
  const validImageSettings = [...byId("image-settings").querySelectorAll("input")].every(input => input.checkValidity());
  byId("crop-apply").disabled = active || busy || directorySelecting || !connected || !loaded || !validImageSettings;
  byId("crop-export").disabled = cropExporting || cropLoading || !cropImage || !validImageSettings || !byId("crop-editor-error").hidden;
  if (connected) byId("footer-state").textContent = stopping ? "正在停止任务" : running ? "正在采集" : "系统就绪";
}

function drawChart(samples) {
  const values = Array(60).fill(0);
  samples.slice(-60).forEach((value, index, recent) => { values[60 - recent.length + index] = Math.max(0, Number(value) || 0); });
  const maximum = Math.max(1, ...values) * 1.15;
  const points = values.map((value, index) => `${(index * 600 / 59).toFixed(1)} ${(56 - value / maximum * 50).toFixed(1)}`);
  byId("chart-line").setAttribute("d", `M${points.join("L")}`);
  byId("chart-area").setAttribute("d", `M0 58L${points.join("L")}L600 58Z`);
  byId("rate-chart").setAttribute("aria-label", `最近一分钟下载速率，当前 ${state.rate.toFixed(1)} 张每秒`);
}

function renderLogs() {
  if (!state) return;
  const filter = byId("log-filter").value;
  const entries = state.logs.filter((entry) => entry.id > clearBefore && (filter === "all" || entry.level === filter));
  const signature = `${clearBefore}:${filter}:${entries[0]?.id}:${entries.at(-1)?.id}:${entries.length}`;
  byId("log-count").textContent = entries.length;
  if (signature === logSignature) return;
  logSignature = signature;
  const view = byId("log-view");
  const scrollTop = view.scrollTop;
  const fragment = document.createDocumentFragment();
  for (const entry of entries) {
    const line = document.createElement("div");
    line.className = `log-line ${entry.level}`;
    const time = document.createElement("span");
    time.className = "log-time";
    time.textContent = entry.time;
    const level = document.createElement("span");
    level.className = "log-level";
    level.textContent = { info: "INFO", success: "OK", warning: "WARN", error: "ERR" }[entry.level] || "INFO";
    const message = document.createElement("span");
    message.className = "log-message";
    message.textContent = entry.message;
    line.append(time, level, message);
    fragment.append(line);
  }
  byId("log-lines").replaceChildren(fragment);
  byId("log-empty").hidden = entries.length > 0;
  byId("log-empty").lastElementChild.textContent = state.logs.length ? "无匹配日志" : "等待任务启动";
  view.scrollTop = byId("auto-scroll").checked ? view.scrollHeight : scrollTop;
}

function renderHistory() {
  const signature = JSON.stringify(state.history);
  if (signature === historySignature) return;
  historySignature = signature;
  if (!state.history.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "暂无运行记录";
    byId("history-view").replaceChildren(empty);
    return;
  }
  const table = document.createElement("table");
  table.className = "history-table";
  const head = document.createElement("thead");
  const header = document.createElement("tr");
  for (const title of ["开始 / 结束", "状态", "新增", "失败", "时长"]) {
    const cell = document.createElement("th");
    cell.textContent = title === "开始 / 结束" ? "结束时间" : title;
    header.append(cell);
  }
  head.append(header);
  const body = document.createElement("tbody");
  state.history.forEach((entry) => {
    const row = document.createElement("tr");
    [entry.time, statusNames[entry.status], number(entry.downloaded), number(entry.failed), elapsed(entry.elapsed)].forEach((value, index) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      if (index === 1 && entry.status === "error") cell.className = "history-error";
      row.append(cell);
    });
    body.append(row);
  });
  table.append(head, body);
  byId("history-view").replaceChildren(table);
}

function render(snapshot) {
  const previous = state;
  state = snapshot;
  if (previous && snapshot.logs[0]?.id > (previous.logs.at(-1)?.id || 0) && snapshot.status === "running" && previous.status !== "running") clearBefore = 0;
  for (const key of ["downloaded", "existing", "failed"]) byId(`metric-${key}`).textContent = number(snapshot[key]);
  byId("metric-elapsed").textContent = elapsed(snapshot.elapsed);
  byId("metric-failed").style.color = snapshot.failed ? "var(--error)" : "";
  byId("run-status").className = `status-badge ${snapshot.status}`;
  byId("status-text").textContent = statusNames[snapshot.status] || snapshot.status;
  byId("run-status").firstElementChild.className = `status-dot ${snapshot.status === "error" ? "error" : ["running", "completed"].includes(snapshot.status) ? "online" : ""}`;
  snapshot.tools.forEach(tool => {
    const dot = byId(tool.id === "weibo" ? "nav-dot" : `${tool.id}-nav-dot`);
    if (dot) dot.className = `status-dot ${["running", "stopping"].includes(tool.status) ? "running" : tool.status === "error" ? "error" : ""}`;
  });
  const unbounded = snapshot.progress === null;
  byId("progress-number").textContent = unbounded ? number(snapshot.downloaded + snapshot.existing) : snapshot.progress.toFixed(1);
  byId("progress-unit").textContent = unbounded ? "张" : "%";
  byId("progress-caption").textContent = unbounded ? "有效图片 / 不限量" : "有效图片 / 采集上限";
  const tally = document.createTextNode(`${number(snapshot.downloaded + snapshot.existing)} `);
  const limit = document.createElement("span");
  limit.textContent = `/ ${unbounded ? "不限" : number(snapshot.limit)}`;
  byId("progress-tally").replaceChildren(tally, limit);
  byId("progress-fill").style.width = `${snapshot.progress || 0}%`;
  byId("progress-track").classList.toggle("indeterminate", unbounded && snapshot.status === "running");
  if (unbounded) byId("progress-track").removeAttribute("aria-valuenow");
  else byId("progress-track").setAttribute("aria-valuenow", snapshot.progress);
  byId("progress-stage").textContent = { idle: "等待开始", running: "正在采集", stopping: "正在停止", stopped: "图片已保留", completed: "采集完成", error: "检查运行日志" }[snapshot.status];
  byId("rate-value").textContent = snapshot.rate.toFixed(1);
  byId("current-target").textContent = snapshot.target;
  byId("current-target").title = snapshot.target;
  byId("current-album").textContent = snapshot.album;
  byId("current-album").title = snapshot.album;
  byId("targets-done").textContent = `${snapshot.targets_done} / ${snapshot.targets_total}`;
  byId("pages-count").textContent = number(snapshot.pages);
  byId("bytes-count").textContent = bytes(snapshot.bytes);
  byId("run-error").hidden = !snapshot.error;
  byId("run-error").textContent = snapshot.error;
  byId("log-dot").className = `status-dot ${snapshot.status === "running" ? "online" : ""}`;
  byId("log-footer-text").textContent = snapshot.status === "running" ? "实时连接" : statusNames[snapshot.status];
  drawChart(snapshot.samples);
  renderLogs();
  renderHistory();
  updateButtons();
}

async function poll() {
  const version = toolVersion;
  const id = selectedTool;
  try {
    const snapshot = await api("/api/state", undefined, 10000, id);
    if (version !== toolVersion) return;
    if (!loaded) {
      const config = await api("/api/config", undefined, 10000, id);
      if (version !== toolVersion) return;
      configureTool();
      populate(config);
    }
    updateConnection(true);
    render(snapshot);
  } catch (error) {
    if (version !== toolVersion) return;
    updateConnection(false);
    byId("log-footer-text").textContent = "连接断开";
    byId("log-dot").className = "status-dot";
    byId("rate-value").textContent = "—";
  } finally {
    setTimeout(poll, 1000);
  }
}

async function action(operation) {
  if (busy) return;
  busy = true;
  updateButtons();
  try {
    await operation();
  } catch (error) {
    toast(error.message, true);
  } finally {
    busy = false;
    updateButtons();
  }
}

function roundPixel(value) {
  const lower = Math.floor(value);
  if (value - lower === 0.5) return lower % 2 === 0 ? lower : lower + 1;
  return Math.round(value);
}

function cropError(message) {
  byId("crop-editor-error").hidden = !message;
  byId("crop-editor-error").textContent = message;
}

function prepareCropImage() {
  if (!cropImage) return;
  const edge = Number(byId("image_long_edge").value);
  const resizeEnabled = byId("resize_enabled").checked;
  if (resizeEnabled && (!Number.isInteger(edge) || edge < 1 || edge > 20000)) throw new Error("请先填写有效的图片长边");
  const scale = resizeEnabled ? edge / Math.max(cropImage.naturalWidth, cropImage.naturalHeight) : 1;
  cropWidth = Math.max(1, roundPixel(cropImage.naturalWidth * scale));
  cropHeight = Math.max(1, roundPixel(cropImage.naturalHeight * scale));
  const displayScale = Math.min(1, 1200 / cropWidth, 1200 / cropHeight);
  byId("crop-canvas").width = Math.max(1, Math.round(cropWidth * displayScale));
  byId("crop-canvas").height = Math.max(1, Math.round(cropHeight * displayScale));
  byId("crop-canvas").hidden = false;
  byId("crop-preview").hidden = false;
  byId("crop-empty").hidden = true;
  byId("crop-preview-empty").hidden = true;
  const maximumCrop = Math.min(cropHeight - 1, 19999);
  byId("crop-pixels").max = 19999;
  byId("crop-range").max = maximumCrop;
  byId("crop-canvas").setAttribute("aria-valuemax", maximumCrop);
  for (const id of ["crop-pixels", "crop-range", "crop-reset"]) byId(id).disabled = false;
  byId("crop-source-size").textContent = `${resizeEnabled ? "处理尺寸" : "原图尺寸"} ${cropWidth} × ${cropHeight} px`;
  byId("crop-scale-note").textContent = resizeEnabled ? "像素按「图片长边」处理后的尺寸计算" : "像素按原图尺寸计算";
  setCropCandidate(cropCandidate);
}

function setCropCandidate(value) {
  cropCandidate = Math.min(19999, Math.max(0, Math.round(Number(value) || 0)));
  byId("crop_bottom").value = cropCandidate;
  byId("crop-pixels").value = cropCandidate;
  byId("crop-range").value = cropCandidate;
  byId("crop-canvas").setAttribute("aria-valuenow", cropCandidate);
  byId("crop-canvas").setAttribute("aria-valuetext", `底部裁剪 ${cropCandidate} 像素，保留 ${cropHeight - cropCandidate} 像素高`);
  const exceedsImage = cropImage && cropCandidate >= cropHeight;
  byId("crop-kept-size").textContent = cropImage && !exceedsImage ? `${cropWidth} × ${cropHeight - cropCandidate} px` : "—";
  cropError(exceedsImage ? `裁剪值超出当前图片高度（${cropHeight} px），请减小裁剪值后保存图片` : "");
  drawCrop();
  updateButtons();
}

function drawCrop() {
  if (!cropImage || !cropHeight) return;
  const canvas = byId("crop-canvas");
  const context = canvas.getContext("2d");
  const boundary = Math.max(0, canvas.height * (cropHeight - cropCandidate) / cropHeight);
  const displayRatio = canvas.width / (canvas.getBoundingClientRect().width || canvas.width);
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.fillStyle = "white";
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.imageSmoothingQuality = "high";
  context.drawImage(cropImage, 0, 0, canvas.width, canvas.height);
  if (cropCandidate > 0) {
    context.fillStyle = "rgba(10, 12, 10, .76)";
    context.fillRect(0, boundary, canvas.width, canvas.height - boundary);
    context.save();
    context.beginPath();
    context.rect(0, boundary, canvas.width, canvas.height - boundary);
    context.clip();
    context.strokeStyle = "rgba(155, 184, 176, .18)";
    context.lineWidth = displayRatio;
    const gap = 18 * displayRatio;
    for (let offset = -canvas.height; offset < canvas.width; offset += gap) {
      context.beginPath();
      context.moveTo(offset, canvas.height);
      context.lineTo(offset + canvas.height, 0);
      context.stroke();
    }
    context.restore();
  }
  const lineY = Math.min(canvas.height - displayRatio, Math.max(displayRatio, boundary));
  context.strokeStyle = "#9bb8b0";
  context.lineWidth = 2 * displayRatio;
  context.beginPath();
  context.moveTo(0, lineY);
  context.lineTo(canvas.width, lineY);
  context.stroke();
  context.fillStyle = "#9bb8b0";
  context.fillRect(canvas.width / 2 - 20 * displayRatio, Math.max(0, lineY - 3 * displayRatio), 40 * displayRatio, 6 * displayRatio);
  const preview = byId("crop-preview");
  preview.hidden = cropCandidate >= cropHeight;
  byId("crop-preview-empty").hidden = !preview.hidden;
  if (preview.hidden) return;
  const keptHeight = cropHeight - cropCandidate;
  const scale = Math.min(1, 520 / cropWidth, 520 / keptHeight);
  preview.width = Math.max(1, Math.round(cropWidth * scale));
  preview.height = Math.max(1, Math.round(keptHeight * scale));
  const previewContext = preview.getContext("2d");
  previewContext.fillStyle = "white";
  previewContext.fillRect(0, 0, preview.width, preview.height);
  previewContext.imageSmoothingQuality = "high";
  previewContext.drawImage(cropImage, 0, 0, cropImage.naturalWidth, cropImage.naturalHeight * keptHeight / cropHeight, 0, 0, preview.width, preview.height);
}

function refreshCropPreview() {
  try {
    cropError("");
    prepareCropImage();
  } catch (error) {
    cropError(error.message);
  }
  updateButtons();
}

function syncImageSettings() {
  cropCandidate = Number(byId("crop_bottom").value) || 0;
  byId("crop-pixels").value = cropCandidate;
  updateResizeSetting();
  refreshCropPreview();
}

async function exportCropImage() {
  if (byId("crop-export").disabled) return;
  cropExporting = true;
  updateButtons();
  try {
    prepareCropImage();
    const canvas = document.createElement("canvas");
    const keptHeight = cropHeight - cropCandidate;
    canvas.width = cropWidth;
    canvas.height = keptHeight;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("图片尺寸过大，请减小图片长边后重试");
    context.fillStyle = "white";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.imageSmoothingQuality = "high";
    context.drawImage(cropImage, 0, 0, cropImage.naturalWidth, cropImage.naturalHeight * keptHeight / cropHeight, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise(resolve => canvas.toBlob(resolve, "image/jpeg", .95));
    if (!blob) throw new Error("保存失败，请减小图片长边后重试");
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${byId("crop-source-name").textContent.replace(/\.[^.]+$/, "")}_processed.jpg`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast("处理后的图片已保存");
  } catch (error) {
    toast(error.message, true);
  } finally {
    cropExporting = false;
    updateButtons();
  }
}

async function loadCropFile(file) {
  if (!file) return;
  const version = ++cropLoadVersion;
  cropLoading = true;
  byId("crop-open-file").disabled = true;
  updateButtons();
  try {
    const dataUrl = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error("图片读取失败"));
      reader.readAsDataURL(file);
    });
    const image = new Image();
    image.src = dataUrl;
    await image.decode();
    if (version !== cropLoadVersion) return;
    cropImage = image;
    byId("crop-source-name").textContent = file.name;
    byId("crop-source-name").title = file.name;
    const previousCrop = cropCandidate;
    refreshCropPreview();
    if (previousCrop !== cropCandidate) setDirty(true);
  } catch (error) {
    cropError(error.message === "图片读取失败" ? error.message : "无法打开图片，请选择 JPG、PNG 或 WebP 图片");
  } finally {
    if (version === cropLoadVersion) {
      cropLoading = false;
      byId("crop-open-file").disabled = false;
      updateButtons();
    }
  }
}

async function selectOutputDirectory() {
  if (directorySelecting) return;
  directorySelecting = true;
  byId("select-output").lastElementChild.textContent = "选择中…";
  updateButtons();
  try {
    const result = await api("/api/select-output", { initial: byId("output_dir").value.trim() || undefined }, 0);
    if (result.path) {
      byId("output_dir").value = result.path;
      byId("output_dir").dispatchEvent(new Event("input", { bubbles: true }));
      toast("已选择目录，保存设置后生效");
    }
  } catch (error) {
    toast(error.message, true);
  } finally {
    directorySelecting = false;
    byId("select-output").lastElementChild.textContent = "选择目录";
    updateButtons();
  }
}

function updateGalleryFooter() {
  byId("gallery-count").textContent = `已显示 ${number(galleryImages.length)} / ${number(galleryTotal)} 张`;
  byId("gallery-more").hidden = galleryImages.length >= galleryTotal;
  byId("gallery-more").disabled = galleryLoading;
  byId("gallery-more").textContent = galleryLoading ? "加载中…" : "加载更多";
  byId("gallery-refresh").disabled = galleryLoading;
  byId("gallery-grid").setAttribute("aria-busy", String(galleryLoading));
}

async function loadGallery(reset = false) {
  if (galleryLoading || !byId("gallery-dialog").open) return;
  const version = galleryVersion;
  galleryLoading = true;
  if (reset) {
    galleryImages = [];
    galleryTotal = 0;
    galleryCatalog = null;
    byId("gallery-grid").replaceChildren();
    byId("gallery-message").textContent = "正在加载图片…";
    byId("gallery-message").hidden = false;
  }
  updateGalleryFooter();
  try {
    const result = await api("/api/images", { output_dir: galleryDirectory, offset: galleryImages.length, limit: 60, catalog: galleryCatalog }, 30000);
    if (version !== galleryVersion) return;
    galleryTotal = result.total;
    galleryCatalog = result.catalog || null;
    byId("gallery-directory").textContent = result.directory;
    byId("gallery-directory").title = result.directory;
    const fragment = document.createDocumentFragment();
    result.images.forEach((entry) => {
      const index = galleryImages.length;
      galleryImages.push(entry);
      const button = document.createElement("button");
      button.type = "button";
      button.className = "gallery-card";
      button.title = entry.path;
      button.setAttribute("aria-label", `放大查看 ${entry.name}`);
      const image = document.createElement("img");
      image.src = entry.thumbnail;
      image.alt = entry.name;
      image.loading = "lazy";
      image.decoding = "async";
      image.addEventListener("error", () => {
        image.hidden = true;
        button.classList.add("image-unavailable");
        button.title = `${entry.path}（图片无法读取，请刷新列表）`;
      });
      const name = document.createElement("span");
      name.className = "gallery-card-name";
      name.textContent = entry.name;
      const caption = document.createElement("small");
      caption.textContent = `${bytes(entry.size)} · ${entry.path.includes("/") ? entry.path.slice(0, entry.path.lastIndexOf("/")) : "保存目录"}`;
      button.append(image, name, caption);
      button.addEventListener("click", () => showGalleryImage(index));
      fragment.append(button);
    });
    byId("gallery-grid").append(fragment);
    byId("gallery-message").hidden = galleryImages.length > 0;
    byId("gallery-message").textContent = "这个目录还没有图片，采集后点击「刷新」查看";
  } catch (error) {
    if (version !== galleryVersion) return;
    byId("gallery-message").textContent = `加载失败：${error.message}，请点击「刷新」重试`;
    byId("gallery-message").hidden = false;
  } finally {
    if (version === galleryVersion) {
      galleryLoading = false;
      updateGalleryFooter();
    }
  }
}

function openGallery() {
  galleryDirectory = ["running", "stopping"].includes(state?.status) ? state.output_dir : byId("output_dir").value.trim();
  byId("gallery-directory").textContent = galleryDirectory;
  byId("gallery-dialog").showModal();
  loadGallery(true);
}

async function showGalleryImage(index) {
  if (index < 0 || index >= galleryImages.length) return;
  imageIndex = index;
  const version = ++imageVersion;
  const entry = galleryImages[index];
  const image = byId("image-full");
  byId("image-title").textContent = entry.name;
  byId("image-title").title = entry.path;
  byId("image-position").textContent = `${index + 1} / ${galleryImages.length}`;
  byId("image-details").textContent = bytes(entry.size);
  byId("image-previous").disabled = index === 0;
  byId("image-next").disabled = index === galleryImages.length - 1;
  byId("image-stage").classList.remove("actual-size");
  byId("image-zoom").setAttribute("aria-pressed", "false");
  byId("image-zoom").textContent = "原始尺寸";
  byId("image-zoom").disabled = true;
  image.hidden = true;
  image.alt = entry.name;
  image.src = entry.url;
  byId("image-message").hidden = false;
  byId("image-message").textContent = "正在加载原图…";
  if (!byId("image-dialog").open) byId("image-dialog").showModal();
  try {
    await image.decode();
    if (version !== imageVersion) return;
    image.hidden = false;
    byId("image-message").hidden = true;
    byId("image-zoom").disabled = false;
    byId("image-details").textContent = `${image.naturalWidth} × ${image.naturalHeight} px · ${bytes(entry.size)}`;
    byId("image-stage").scrollTo(0, 0);
  } catch {
    if (version !== imageVersion) return;
    byId("image-message").textContent = "图片无法读取或已移动，请关闭后刷新图片列表";
  }
}

byId("select-output").addEventListener("click", selectOutputDirectory);
byId("open-gallery").addEventListener("click", openGallery);
byId("gallery-refresh").addEventListener("click", () => loadGallery(true));
byId("gallery-more").addEventListener("click", () => loadGallery());
byId("gallery-close").addEventListener("click", () => byId("gallery-dialog").close());
byId("gallery-dialog").addEventListener("close", () => {
  galleryVersion++;
  galleryLoading = false;
});
byId("image-close").addEventListener("click", () => byId("image-dialog").close());
byId("image-dialog").addEventListener("close", () => {
  imageVersion++;
  byId("image-full").removeAttribute("src");
  byId("image-full").hidden = true;
});
byId("image-previous").addEventListener("click", () => showGalleryImage(imageIndex - 1));
byId("image-next").addEventListener("click", () => showGalleryImage(imageIndex + 1));
byId("image-zoom").addEventListener("click", () => {
  const actualSize = byId("image-stage").classList.toggle("actual-size");
  byId("image-zoom").setAttribute("aria-pressed", String(actualSize));
  byId("image-zoom").textContent = actualSize ? "适应窗口" : "原始尺寸";
});
byId("image-dialog").addEventListener("keydown", (event) => {
  if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
  event.preventDefault();
  showGalleryImage(imageIndex + (event.key === "ArrowRight" ? 1 : -1));
});

byId("crop-nav").addEventListener("click", () => showView("images"));
byId("crop-export").addEventListener("click", exportCropImage);
byId("crop-open-file").addEventListener("click", () => byId("crop-file").click());
byId("crop-file").addEventListener("change", (event) => {
  loadCropFile(event.target.files[0]);
  event.target.value = "";
});
byId("crop-reset").addEventListener("click", () => { setCropCandidate(0); setDirty(true); });
byId("crop-range").addEventListener("input", (event) => setCropCandidate(event.target.value));
byId("crop-pixels").addEventListener("input", (event) => {
  if (Number.isNaN(event.target.valueAsNumber)) {
    cropError("请输入裁剪像素数");
    updateButtons();
    return;
  }
  setCropCandidate(event.target.valueAsNumber);
});
function moveCropPointer(event) {
  const bounds = byId("crop-canvas").getBoundingClientRect();
  setCropCandidate(cropHeight - Math.round((event.clientY - bounds.top) / bounds.height * cropHeight));
  setDirty(true);
}
byId("crop-canvas").addEventListener("pointerdown", (event) => {
  if (!cropImage || (event.pointerType === "mouse" && event.button !== 0)) return;
  event.preventDefault();
  cropDragging = true;
  event.currentTarget.focus();
  event.currentTarget.setPointerCapture(event.pointerId);
  moveCropPointer(event);
});
byId("crop-canvas").addEventListener("pointermove", (event) => {
  if (cropDragging) moveCropPointer(event);
});
for (const name of ["pointerup", "pointercancel", "lostpointercapture"]) byId("crop-canvas").addEventListener(name, () => { cropDragging = false; });
byId("crop-canvas").addEventListener("keydown", (event) => {
  if (!cropImage || !["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  setDirty(true);
  const step = event.shiftKey ? 10 : 1;
  setCropCandidate(event.key === "Home" ? 0 : event.key === "End" ? cropHeight - 1 : cropCandidate + (event.key === "ArrowUp" ? step : -step));
});
byId("crop-apply").addEventListener("click", () => byId("save-button").click());
window.addEventListener("resize", () => {
  if (currentView === "images") requestAnimationFrame(drawCrop);
});
byId("image-settings").addEventListener("submit", event => event.preventDefault());
byId("image-settings").addEventListener("input", event => {
  setDirty(true);
  if (["resize_enabled", "image_long_edge"].includes(event.target.id)) {
    updateResizeSetting();
    refreshCropPreview();
  }
  updateButtons();
});

wireTabs(["basic-tab", "advanced-tab"], ["basic-settings", "advanced-settings"]);
function updateResizeSetting() {
  const enabled = byId("resize_enabled").checked;
  byId("image-long-edge-field").hidden = !enabled;
  byId("image_long_edge").disabled = !enabled;
}
byId("resize_enabled").addEventListener("change", () => { updateResizeSetting(); refreshCropPreview(); });
wireTabs(["live-tab", "history-tab"], ["log-view", "history-view"]);
byId("uid-mode").addEventListener("click", () => setTarget("uid"));
byId("album-mode").addEventListener("click", () => setTarget("album"));
byId("config-form").addEventListener("input", (event) => {
  setDirty(true);
  if (selectedTool === "pinterest") updatePinterestSummary();
  if (event.target.id === "filename_prefix") byId("filename-preview").textContent = `${event.target.value || "weibo"}_0001.jpg`;
});
byId("config-form").addEventListener("submit", (event) => event.preventDefault());
byId("show-cookie").addEventListener("click", () => {
  const visible = byId("cookie").type === "password";
  byId("cookie").type = visible ? "text" : "password";
  byId("show-cookie").setAttribute("aria-label", visible ? "隐藏 Cookie" : "显示 Cookie");
  byId("show-cookie").setAttribute("aria-pressed", String(visible));
});
byId("save-button").addEventListener("click", () => action(async () => {
  const response = await api("/api/config", { config: collectConfig(), cookie: selectedTool === "google" ? "" : byId("cookie").value.trim() });
  populate(response.config);
  render(await api("/api/state"));
  toast("设置已保存");
}));
byId("run-button").addEventListener("click", () => action(async () => {
  if (state?.status === "running") {
    await api("/api/stop", {});
    toast("正在停止采集");
  } else {
    const cookie = byId("cookie").value.trim();
    if (["weibo", "xhs"].includes(selectedTool) && !cookie) {
      activateTab("basic-tab", ["basic-tab", "advanced-tab"], ["basic-settings", "advanced-settings"]);
      byId("cookie").focus();
      throw new Error(`请填写 ${selectedTool === "xhs" ? "xiaohongshu.com" : "weibo.cn"} 的 Cookie`);
    }
    await api("/api/start", { config: collectConfig(), cookie });
    byId("cookie").type = "password";
    byId("show-cookie").setAttribute("aria-pressed", "false");
    byId("show-cookie").setAttribute("aria-label", "显示 Cookie");
    const saved = await api("/api/config");
    setDirty(selectedTool !== "google" && cookie !== saved.cookie);
    clearBefore = 0;
    toast("采集已启动");
  }
  render(await api("/api/state"));
}));
byId("open-output").addEventListener("click", () => action(async () => {
  await api("/api/open-output", {});
}));
byId("log-filter").addEventListener("change", renderLogs);
byId("clear-log").addEventListener("click", () => {
  clearBefore = state?.logs.at(-1)?.id || 0;
  renderLogs();
});
byId("export-log").addEventListener("click", () => {
  const logs = state?.logs || [];
  if (!logs.length) return toast("暂无日志");
  const text = logs.map((entry) => `${entry.time} [${entry.level.toUpperCase()}] ${entry.message}`).join("\r\n");
  const url = URL.createObjectURL(new Blob(["\ufeff", text], { type: "text/plain;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `${selectedTool}-${new Date().toISOString().slice(0, 10)}.log`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
byId("tool-search").addEventListener("input", (event) => {
  const query = event.target.value.trim().toLowerCase();
  let matches = 0;
  for (const [id, keywords] of Object.entries({weibo: "微博相册 weibospider weibo", xhs: "小红书图集 spider_xhs xiaohongshu xhs", google: "谷歌图片 google images", pinterest: "Pinterest 图集 pin 画板 分区", crop: "图片裁剪 缩放 crop resize"})) {
    const match = keywords.includes(query);
    byId(`${id}-nav`).hidden = !match;
    if (match) matches++;
  }
  byId("crawler-group").hidden = byId("weibo-nav").hidden && byId("xhs-nav").hidden && byId("google-nav").hidden && byId("pinterest-nav").hidden;
  byId("image-group").hidden = byId("crop-nav").hidden;
  byId("search-empty").hidden = matches > 0;
});
for (const id of ["weibo", "xhs", "google", "pinterest"]) byId(`${id}-nav`).addEventListener("click", () => switchTool(id));
for (const mode of ["note", "profile", "search"]) byId(`xhs-${mode}-mode`).addEventListener("click", () => setXhsTarget(mode));
for (const mode of Object.keys(pinterestInputs)) byId(`pinterest-${mode}-mode`).addEventListener("click", () => setPinterestTarget(mode));

document.addEventListener("keydown", (event) => {
  if (event.key === "/" && !document.querySelector("dialog[open]") && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) {
    event.preventDefault();
    setSidebarCollapsed(false);
    byId("tool-search").focus();
  }
});
window.addEventListener("beforeunload", (event) => {
  if (dirty || [...toolDrafts.entries()].some(([id, draft]) => id !== selectedTool && draft.dirty)) {
    event.preventDefault();
    event.returnValue = "";
  }
});
byId("sidebar-toggle").addEventListener("click", () => setSidebarCollapsed(!document.querySelector(".app-shell").classList.contains("sidebar-collapsed")));
try {
  const preference = localStorage.getItem("spiderroom.sidebarCollapsed");
  setSidebarCollapsed(preference === null ? matchMedia("(max-width: 999px)").matches : preference === "true", false);
} catch { setSidebarCollapsed(matchMedia("(max-width: 999px)").matches, false); }
updateButtons();
if (location.protocol === "file:") {
  updateConnection(false);
  toast("双击 start.bat 启动控制台", true);
} else poll();
