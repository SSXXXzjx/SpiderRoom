"use strict";

const byId = (id) => document.getElementById(id);
const statusNames = { idle: "待运行", running: "采集中", stopping: "停止中", stopped: "已停止", completed: "已完成", error: "运行失败" };
const integerFields = ["max_images", "max_pages", "download_workers", "image_long_edge", "crop_bottom", "retries"];
const decimalFields = ["request_interval", "timeout"];
const listFields = ["user_ids", "album_urls", "album_names"];
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

async function api(path, payload) {
  if (location.protocol === "file:") throw new Error("请通过 start.bat 打开控制台");
  const response = await fetch(path, {
    method: payload === undefined ? "GET" : "POST",
    headers: payload === undefined ? {} : { "Content-Type": "application/json" },
    body: payload === undefined ? undefined : JSON.stringify(payload),
    signal: AbortSignal.timeout(10000),
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
    const input = byId(key);
    if (!input) continue;
    if (input.type === "checkbox") input.checked = value;
    else input.value = Array.isArray(value) ? value.join(key === "album_names" ? ", " : "\n") : value;
  }
  setTarget(config.album_urls.length ? "album" : "uid", false);
  updateResizeSetting();
  byId("filename-preview").textContent = `${config.filename_prefix}_0001.jpg`;
  setDirty(false);
  loaded = true;
}

function collectConfig() {
  const invalid = [...byId("config-fields").querySelectorAll("input,textarea")].find((input) => !input.checkValidity());
  if (invalid) {
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
  return result;
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
  byId("run-button").disabled = busy || stopping || !connected || !loaded;
  byId("run-button").classList.toggle("stop-button", active);
  byId("run-icon").setAttribute("href", active ? "#icon-stop" : "#icon-play");
  byId("run-label").textContent = busy ? "处理中…" : stopping ? "停止中…" : running ? "停止采集" : "开始采集";
  byId("save-button").disabled = busy || active || !connected || !loaded;
  byId("config-fields").disabled = active || !loaded;
  byId("open-output").disabled = busy || !connected;
  byId("open-crop-top").disabled = active || busy || !loaded;
  if (byId("crop-dialog").open) byId("crop-apply").disabled = active || busy || cropLoading || !cropImage || !byId("crop-editor-error").hidden;
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
  if (!state.history.length) return;
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
  byId("nav-dot").className = `status-dot ${snapshot.status === "running" ? "running" : snapshot.status === "error" ? "error" : ""}`;
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
  byId("project-path").textContent = snapshot.tools[0].path;
  drawChart(snapshot.samples);
  renderLogs();
  renderHistory();
  updateButtons();
}

async function poll() {
  try {
    const snapshot = await api("/api/state");
    if (!loaded) populate(await api("/api/config"));
    updateConnection(true);
    render(snapshot);
  } catch (error) {
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
  byId("crop-pixels").max = maximumCrop;
  byId("crop-range").max = maximumCrop;
  byId("crop-canvas").setAttribute("aria-valuemax", maximumCrop);
  for (const id of ["crop-pixels", "crop-range", "crop-reset"]) byId(id).disabled = false;
  byId("crop-source-size").textContent = `${resizeEnabled ? "处理尺寸" : "原图尺寸"} ${cropWidth} × ${cropHeight} px`;
  byId("crop-scale-note").textContent = resizeEnabled ? "像素按「图片长边」处理后的尺寸计算" : "像素按原图尺寸计算";
  setCropCandidate(cropCandidate);
}

function setCropCandidate(value) {
  if (!cropImage) return;
  cropCandidate = Math.min(cropHeight - 1, 19999, Math.max(0, Math.round(Number(value) || 0)));
  byId("crop-pixels").value = cropCandidate;
  byId("crop-range").value = cropCandidate;
  byId("crop-canvas").setAttribute("aria-valuenow", cropCandidate);
  byId("crop-canvas").setAttribute("aria-valuetext", `底部裁剪 ${cropCandidate} 像素，保留 ${cropHeight - cropCandidate} 像素高`);
  byId("crop-kept-size").textContent = `${cropWidth} × ${cropHeight - cropCandidate} px`;
  cropError("");
  drawCrop();
  updateButtons();
}

function drawCrop() {
  if (!cropImage || !cropHeight) return;
  const canvas = byId("crop-canvas");
  const context = canvas.getContext("2d");
  const boundary = canvas.height * (cropHeight - cropCandidate) / cropHeight;
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
    context.strokeStyle = "rgba(194, 255, 99, .18)";
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
  context.strokeStyle = "#c2ff63";
  context.lineWidth = 2 * displayRatio;
  context.beginPath();
  context.moveTo(0, lineY);
  context.lineTo(canvas.width, lineY);
  context.stroke();
  context.fillStyle = "#c2ff63";
  context.fillRect(canvas.width / 2 - 20 * displayRatio, Math.max(0, lineY - 3 * displayRatio), 40 * displayRatio, 6 * displayRatio);
  const preview = byId("crop-preview");
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

function openCropEditor() {
  cropCandidate = Number(byId("crop_bottom").value) || 0;
  cropError("");
  byId("crop-dialog").showModal();
  try {
    prepareCropImage();
  } catch (error) {
    cropError(error.message);
    byId("crop-apply").disabled = true;
  }
  if (!cropImage) byId("crop-file").click();
}

async function loadCropFile(file) {
  if (!file) return;
  const version = ++cropLoadVersion;
  cropLoading = true;
  byId("crop-open-file").disabled = true;
  byId("crop-apply").disabled = true;
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
    prepareCropImage();
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

byId("open-crop").addEventListener("click", openCropEditor);
byId("open-crop-top").addEventListener("click", openCropEditor);
byId("crop-open-file").addEventListener("click", () => byId("crop-file").click());
byId("crop-file").addEventListener("change", (event) => {
  loadCropFile(event.target.files[0]);
  event.target.value = "";
});
for (const id of ["crop-close", "crop-cancel"]) byId(id).addEventListener("click", () => byId("crop-dialog").close());
byId("crop-reset").addEventListener("click", () => setCropCandidate(0));
byId("crop-range").addEventListener("input", (event) => setCropCandidate(event.target.value));
byId("crop-pixels").addEventListener("input", (event) => {
  if (Number.isNaN(event.target.valueAsNumber)) {
    cropError("请输入裁剪像素数");
    byId("crop-apply").disabled = true;
    return;
  }
  setCropCandidate(event.target.valueAsNumber);
});
function moveCropPointer(event) {
  const bounds = byId("crop-canvas").getBoundingClientRect();
  setCropCandidate(cropHeight - Math.round((event.clientY - bounds.top) / bounds.height * cropHeight));
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
  const step = event.shiftKey ? 10 : 1;
  setCropCandidate(event.key === "Home" ? 0 : event.key === "End" ? cropHeight - 1 : cropCandidate + (event.key === "ArrowUp" ? step : -step));
});
byId("crop-apply").addEventListener("click", () => {
  if (!cropImage || busy || ["running", "stopping"].includes(state?.status)) return;
  byId("crop_bottom").value = cropCandidate;
  byId("crop_bottom").dispatchEvent(new Event("input", { bubbles: true }));
  byId("crop-dialog").close();
  toast(`底部裁剪 ${cropCandidate} px`);
});
window.addEventListener("resize", () => {
  if (byId("crop-dialog").open) requestAnimationFrame(drawCrop);
});

wireTabs(["basic-tab", "advanced-tab"], ["basic-settings", "advanced-settings"]);
function updateResizeSetting() {
  const enabled = byId("resize_enabled").checked;
  byId("image-long-edge-field").hidden = !enabled;
  byId("image_long_edge").disabled = !enabled;
}
byId("resize_enabled").addEventListener("change", updateResizeSetting);
wireTabs(["live-tab", "history-tab"], ["log-view", "history-view"]);
byId("uid-mode").addEventListener("click", () => setTarget("uid"));
byId("album-mode").addEventListener("click", () => setTarget("album"));
byId("config-form").addEventListener("input", (event) => {
  if (event.target.id !== "cookie") setDirty(true);
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
  const response = await api("/api/config", { config: collectConfig() });
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
    if (!cookie) {
      activateTab("basic-tab", ["basic-tab", "advanced-tab"], ["basic-settings", "advanced-settings"]);
      byId("cookie").focus();
      throw new Error("请填写 weibo.cn 的 Cookie");
    }
    await api("/api/start", { config: collectConfig(), cookie });
    byId("cookie").value = "";
    byId("cookie").type = "password";
    byId("show-cookie").setAttribute("aria-pressed", "false");
    byId("show-cookie").setAttribute("aria-label", "显示 Cookie");
    setDirty(false);
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
  link.download = `weibo-${new Date().toISOString().slice(0, 10)}.log`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
byId("tool-search").addEventListener("input", (event) => {
  const match = "微博相册 weibospider weibo".includes(event.target.value.trim().toLowerCase());
  byId("weibo-nav").hidden = !match;
  byId("search-empty").hidden = match;
});
byId("weibo-nav").addEventListener("click", () => {
  byId("main-content").scrollTo({ top: 0, behavior: "auto" });
  byId("run-button").focus();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) {
    event.preventDefault();
    byId("tool-search").focus();
  }
});
window.addEventListener("beforeunload", (event) => {
  if (dirty) {
    event.preventDefault();
    event.returnValue = "";
  }
});
updateButtons();
if (location.protocol === "file:") {
  updateConnection(false);
  toast("双击 start.bat 启动控制台", true);
} else poll();
