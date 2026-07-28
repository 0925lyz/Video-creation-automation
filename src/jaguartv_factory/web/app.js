const state = {
  overview: null,
  candidates: [],
  publications: [],
  workers: [],
  feedback: [],
  keywordGroups: [],
  settings: null,
  sessions: [],
  uploads: [],
  health: null,
  tasks: [],
  selectedCandidates: new Set(),
  status: "",
  search: "",
  pendingProductionIds: [],
};

const views = {
  overview: ["OPERATIONS", "内容生产总览"],
  inventory: ["INVENTORY", "内容库存"],
  publishing: ["DISTRIBUTION", "发布队列"],
  analytics: ["GROWTH", "增长分析"],
  nodes: ["INFRASTRUCTURE", "运行节点"],
  settings: ["SYSTEM", "系统管理"],
};

const statusLabels = {
  DISCOVERED: "待筛选",
  SKIPPED: "已忽略",
  DOWNLOADED: "待制作",
  READY_FOR_REVIEW: "待审核",
  APPROVED: "审核通过",
  REVISION_REQUIRED: "需返工",
  LANGUAGE_REJECTED: "语言排除",
  TOO_LONG: "超30分钟",
  DOWNLOAD_FAILED: "下载失败",
  PRODUCTION_FAILED: "制作失败",
  BLOCKED_RIGHTS: "权利待人工确认",
  QUEUED: "排队中",
  SCHEDULED: "已计划",
  PUBLISHED: "已发布",
  FAILED: "失败",
};

const scoreDimensionLabels = {
  velocity: "热度速度", engagement: "互动质量", relevance: "相关度",
  editability: "可剪辑性", brazil_fit: "巴西适配", freshness: "新鲜度",
};

function scoreTooltip(breakdown) {
  if (!breakdown || !Object.keys(breakdown).length) return "";
  return Object.entries(scoreDimensionLabels)
    .filter(([key]) => breakdown[key] !== undefined)
    .map(([key, label]) => `${label} ${(breakdown[key] * 100).toFixed(0)}`)
    .join(" · ");
}

const number = (value) => new Intl.NumberFormat("zh-CN", { notation: Number(value) > 999999 ? "compact" : "standard", maximumFractionDigits: 1 }).format(Number(value || 0));
const percent = (value) => `${(Number(value || 0) * 100).toFixed(2)}%`;
const dateText = (value) => value ? new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value)) : "未设置";
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[character]));
const storedUploadToken = () => sessionStorage.getItem("jaguartvUploadToken") || "";

function rememberUploadToken(token) {
  const value = String(token || "").trim();
  if (value) sessionStorage.setItem("jaguartvUploadToken", value);
  return value;
}

async function api(path, options = {}) {
  const { headers = {}, ...requestOptions } = options;
  const response = await fetch(path, {
    ...requestOptions,
    headers: { "Content-Type": "application/json", ...headers },
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

function toast(message, type = "ok") {
  const element = document.querySelector("#toast");
  element.textContent = message;
  element.className = `toast show ${type === "error" ? "error" : ""}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => element.className = "toast", 3200);
}

async function refreshAll(showToast = false) {
  const button = document.querySelector("#refreshButton");
  button.disabled = true;
  try {
    const token = storedUploadToken();
    const [overview, candidates, publications, workers, feedback, keywordGroups, tasks, settings, sessions, health, uploads] = await Promise.all([
      api("/api/overview"), api("/api/candidates?limit=200"), api("/api/publications"), api("/api/workers"), api("/api/feedback"), api("/api/keywords"), api("/api/tasks"), api("/api/settings"), api("/api/sessions"), api("/api/health"),
      token ? api("/api/uploads", { headers: { "X-Upload-Token": token } }).catch(() => []) : Promise.resolve([]),
    ]);
    Object.assign(state, { overview, candidates, publications, workers, feedback, keywordGroups, tasks, settings, sessions, health, uploads });
    renderAll();
    document.querySelector("#serviceTime").textContent = `更新于 ${dateText(overview.generated_at)}`;
    if (showToast) toast("数据已刷新");
  } catch (error) {
    toast(`刷新失败：${error.message}`, "error");
  } finally {
    button.disabled = false;
  }
}

function renderAll() {
  renderKpis();
  renderFunnel();
  renderPlatforms();
  renderKeywords();
  renderRecent();
  renderInventory();
  renderPublications();
  renderAnalytics();
  renderKeywordGroups();
  renderWorkers();
  renderSettings();
  renderSessions();
  renderUploads();
  renderServerHealth();
  renderTasks();
  fillCandidateSelects();
  document.querySelector("#navInventory").textContent = state.overview.kpis.inventory;
  document.querySelector("#navQueue").textContent = state.overview.kpis.scheduled;
  document.querySelector("#navNodes").textContent = state.workers.length;
}

function renderKpis() {
  const k = state.overview.kpis;
  const cards = [
    ["内容库存", k.inventory, "全部候选"], ["待审核成片", k.ready, `审核通过 ${number(k.approved || 0)}`],
    ["计划发布", k.scheduled, "等待发布节点"], ["落地页点击", k.clicks, "JaguarTV CTA"],
    ["注册用户", k.registrations, `点击转注册 ${percent(k.conversion_rate)}`],
    ["首次观看", k.first_watch || 0, `注册转观看 ${percent(k.activation_rate)} · 北极星`],
  ];
  document.querySelector("#kpiGrid").innerHTML = cards.map((item, index) => `
    <article class="kpi-card ${index === 5 ? "highlight" : ""}"><span>${item[0]}</span><strong>${number(item[1])}</strong><small>${item[2]}</small></article>
  `).join("");
}

function renderFunnel() {
  const funnel = state.overview.funnel;
  document.querySelector("#funnel").innerHTML = funnel.map((step, index) => {
    const previous = index ? funnel[index - 1].value : step.value;
    const rate = previous ? step.value / previous : 0;
    return `<div class="funnel-step ${step.value ? "" : "zero"}"><span>${step.label}</span><strong>${number(step.value)}</strong><small>${index ? `阶段转化 ${percent(rate)}` : "内容入口"}</small></div>`;
  }).join("");
}

function renderPlatforms() {
  const initials = { youtube: "YT", facebook: "FB", tiktok: "TK", kwai: "KW" };
  document.querySelector("#platformTable").innerHTML = state.overview.platforms.map((row) => `
    <tr><td class="platform-name"><span class="platform-badge">${initials[row.platform] || row.platform.slice(0, 2)}</span>${row.platform}</td><td>${number(row.posts)}</td><td>${number(row.views)}</td><td>${number(row.clicks)}</td><td>${number(row.registrations)}</td></tr>
  `).join("");
}

function renderKeywords() {
  const keywords = state.overview.keywords.slice(0, 6);
  const max = Math.max(1, ...keywords.map((row) => row.score || row.candidates));
  document.querySelector("#keywordBars").innerHTML = keywords.length ? keywords.map((row) => `
    <div class="keyword-row"><span title="${escapeHtml(row.keyword)}">${escapeHtml(row.keyword)}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.max(5, (row.score || row.candidates) / max * 100)}%"></div></div><strong>${number(row.score || row.candidates)}</strong></div>
  `).join("") : `<div class="empty-state">发现素材后显示关键词信号</div>`;
}

function renderRecent() {
  document.querySelector("#recentContent").innerHTML = state.candidates.slice(0, 4).map((item) => `
    <article class="content-card">
      ${item.cover_url ? `<img class="content-cover" src="${item.cover_url}" alt="${escapeHtml(item.title)}">` : `<div class="cover-placeholder">${escapeHtml(item.platform.toUpperCase())}</div>`}
      <div class="content-body"><h3>${escapeHtml(item.title || "未命名内容")}</h3><div class="content-meta"><span>${escapeHtml(item.platform)}</span><span class="status-pill ${statusClass(item.status)}">${statusLabels[item.status] || item.status}</span></div></div>
    </article>
  `).join("");
}

function filteredCandidates() {
  const query = state.search.toLowerCase();
  return state.candidates.filter((item) => (!state.status || item.status === state.status) && (!query || item.title.toLowerCase().includes(query) || item.id.toLowerCase().includes(query)));
}

function renderInventory() {
  const rows = filteredCandidates();
  document.querySelector("#inventoryCount").textContent = `${rows.length} 条内容`;
  document.querySelector("#inventoryTable").innerHTML = rows.length ? rows.map((item) => {
    const thumb = item.cover_url || item.thumbnail_url;
    const task = activeTaskFor(item.id);
    const status = task ? `${task.action === "download" ? "下载" : task.action === "produce" ? "制作" : "处理"}中` : (statusLabels[item.status] || item.status);
    const failure = item.failure_detail ? `<small class="failure-reason" title="${escapeHtml(item.failure_detail)}">${escapeHtml(failureReason(item.failure_detail))}</small>` : "";
    return `
    <tr>
      <td class="check-column"><input class="candidate-checkbox" type="checkbox" data-candidate-select="${item.id}" ${state.selectedCandidates.has(item.id) ? "checked" : ""} aria-label="选择 ${escapeHtml(item.title || item.id)}"></td>
      <td><div class="content-cell">${thumb ? `<img class="mini-cover" src="${thumb}" alt="" loading="lazy" referrerpolicy="no-referrer">` : `<div class="mini-cover"></div>`}<div><strong title="${escapeHtml(item.title)}">${escapeHtml(item.title || "未命名内容")}</strong><small>${item.id}${item.keyword ? ` · ${escapeHtml(item.keyword)}` : ""}</small></div></div></td>
      <td>${escapeHtml(item.platform)}</td>
      <td><strong>${escapeHtml(item.content_type || "unknown")}</strong><small>${escapeHtml(item.segment_strategy || "未分析")} · ${escapeHtml(item.audio_policy || "自动")}</small></td>
      <td>${Number(item.highlight_score || 0).toFixed(1)}</td>
      <td><span title="${escapeHtml(scoreTooltip(item.score_breakdown))}">${Number(item.score || 0).toFixed(1)}</span></td>
      <td><span class="status-pill ${task ? "running" : statusClass(item.status)}">${status}</span>${failure}</td><td>${dateText(item.updated_at)}</td>
      <td>${task ? `<span class="row-progress">${task.progress || 0}%</span>` : candidateAction(item)}</td>
    </tr>`;
  }).join("") : `<tr><td colspan="9"><div class="empty-state">没有符合条件的内容</div></td></tr>`;
  document.querySelectorAll("[data-candidate-select]").forEach((checkbox) => checkbox.addEventListener("change", () => {
    if (checkbox.checked) state.selectedCandidates.add(checkbox.dataset.candidateSelect);
    else state.selectedCandidates.delete(checkbox.dataset.candidateSelect);
    updateBatchToolbar();
  }));
  document.querySelectorAll("[data-candidate-action]").forEach((button) => button.addEventListener("click", () => runCandidateAction(button.dataset.candidateAction, button.dataset.candidateId)));
  document.querySelectorAll("[data-review-decision]").forEach((button) => button.addEventListener("click", () => submitReview(button.dataset.reviewDecision, button.dataset.candidateId)));
  document.querySelectorAll("[data-skip-id]").forEach((button) => button.addEventListener("click", () => skipCandidate(button.dataset.skipId)));
  updateBatchToolbar();
}

function failureReason(detail) {
  const text = String(detail || "");
  if (text.includes("Sign in to confirm you’re not a bot") || text.includes("Sign in to confirm you're not a bot")) return "YouTube 要求登录：在系统管理保存 YouTube 登录态后重试";
  if (text.includes("No supported JavaScript runtime")) return "YouTube 解析运行时未就绪：服务器需要 Deno 或 Node 22+";
  if (text.includes("service unreachable")) return "平台采集服务未启动或不可访问，请检查对应采集节点";
  if (text.includes("HTTP Error 412")) return "Bilibili 拒绝当前请求：保存 B站登录态后重试";
  if (text.includes("Video unavailable")) return "源视频已删除、设为私密或当前地区不可用";
  if (text.includes("HTTP Error 403")) return "源站拒绝下载（403）：建议配置 cookies 或更换素材";
  if (text.includes("Language gate")) return "葡语脚本被错误识别为英文：检测逻辑已修复，可重新制作";
  if (text.includes("exit status 69")) return "并发渲染导致成片文件不完整：已改为排队制作，可重新制作";
  if (text.includes("BLOCKED_RIGHTS")) return "旧版本曾因权利状态阻断；现在可重新制作并进入人工审核";
  return text.length > 150 ? `${text.slice(0, 147)}...` : text;
}

function activeTaskFor(candidateId) {
  return state.tasks.find((task) => task.status === "RUNNING" && (task.candidate_ids || []).includes(candidateId));
}

function selectedRows() {
  return state.candidates.filter((item) => state.selectedCandidates.has(item.id));
}

function updateBatchToolbar() {
  const rows = selectedRows();
  document.querySelector("#selectionCount").textContent = `已选 ${rows.length} 条`;
  document.querySelector("#batchDownload").disabled = !rows.some((item) => ["DISCOVERED", "DOWNLOAD_FAILED"].includes(item.status));
  document.querySelector("#batchProduce").disabled = !rows.some((item) => ["DOWNLOADED", "PRODUCTION_FAILED", "REVISION_REQUIRED", "BLOCKED_RIGHTS"].includes(item.status));
  document.querySelector("#batchSkip").disabled = !rows.some((item) => item.status === "DISCOVERED");
  const visible = filteredCandidates();
  const selectVisible = document.querySelector("#selectVisible");
  selectVisible.checked = visible.length > 0 && visible.every((item) => state.selectedCandidates.has(item.id));
  selectVisible.indeterminate = visible.some((item) => state.selectedCandidates.has(item.id)) && !selectVisible.checked;
}

function renderTasks() {
  const panel = document.querySelector("#taskProgressPanel");
  const tasks = state.tasks.filter((task) => task.status === "RUNNING" || Date.now() - new Date(task.finished_at || 0).getTime() < 120000);
  panel.hidden = tasks.length === 0;
  panel.innerHTML = tasks.map((task) => `<div class="task-progress-item">
    <div><strong>${escapeHtml({discover:"发现素材",ingest:"导入素材",download:"下载素材",produce:"制作成片",skip:"忽略候选"}[task.action] || task.action)}</strong><span>${escapeHtml(task.message || "处理中")}${task.total > 1 ? ` · ${task.completed || 0}/${task.total}` : ""}</span></div>
    <div class="progress-track"><span style="width:${Math.max(2, Number(task.progress || 0))}%"></span></div><b>${Number(task.progress || 0)}%</b>
  </div>`).join("");
}

function statusClass(status) {
  if (status === "READY_FOR_REVIEW" || status === "PUBLISHED" || status === "APPROVED") return "ready";
  if (status.includes("FAILED") || status === "REVISION_REQUIRED" || status === "BLOCKED_RIGHTS") return "failed";
  return "";
}

function candidateAction(item) {
  const sourceLink = item.url ? `<button class="table-action" onclick="window.open('${escapeHtml(item.url)}','_blank')">源页</button>` : "";
  if (item.status === "DISCOVERED") return `${sourceLink}<button class="table-action" data-candidate-action="download" data-candidate-id="${item.id}">下载</button><button class="table-action" data-skip-id="${item.id}">忽略</button>`;
  if (item.status === "DOWNLOAD_FAILED") return `${sourceLink}<button class="table-action" data-candidate-action="download" data-candidate-id="${item.id}">重试下载</button>`;
  if (["DOWNLOADED", "PRODUCTION_FAILED", "REVISION_REQUIRED", "BLOCKED_RIGHTS"].includes(item.status)) return `<button class="table-action" data-candidate-action="produce" data-candidate-id="${item.id}">制作</button>`;
  if (item.status === "READY_FOR_REVIEW") {
    const preview = item.video_url ? `<button class="table-action" onclick="window.open('${item.video_url}','_blank')">预览</button>` : "";
    return `${preview}<button class="table-action" data-review-decision="APPROVED" data-candidate-id="${item.id}">通过</button><button class="table-action" data-review-decision="REVISION_REQUIRED" data-candidate-id="${item.id}">返工</button>`;
  }
  if (item.status === "APPROVED" && item.video_url) {
    const server = item.server_url ? `<button class="table-action" onclick="window.open('${escapeHtml(item.server_url)}','_blank')">服务器成片</button>` : "";
    return `<button class="table-action" onclick="window.open('${item.video_url}','_blank')">预览</button>${server}`;
  }
  return "";
}

async function submitReview(decision, candidateId) {
  try {
    await api("/api/review", { method: "POST", body: JSON.stringify({ candidate_id: candidateId, decision, reviewer: "dashboard" }) });
    toast(decision === "APPROVED" ? "已通过，可加入发布队列" : "已标记为需返工");
    await refreshAll();
  } catch (error) { toast(error.message, "error"); }
}

async function skipCandidate(candidateId) {
  try {
    await api("/api/skip", { method: "POST", body: JSON.stringify({ candidate_id: candidateId }) });
    toast("已忽略该候选");
    await refreshAll();
  } catch (error) { toast(error.message, "error"); }
}

async function runCandidateAction(action, candidateId) {
  if (action === "produce") {
    openProductionDialog([candidateId]);
    return;
  }
  try {
    const result = await api("/api/actions", { method: "POST", body: JSON.stringify({ action, candidate_id: candidateId }) });
    toast(`任务 ${result.task_id} 已启动`);
    pollTask(result.task_id);
  } catch (error) { toast(error.message, "error"); }
}

async function runBatchAction(action, allowedStatuses, options = {}, explicitIds = null) {
  const candidateIds = explicitIds || selectedRows().filter((item) => allowedStatuses.includes(item.status)).map((item) => item.id);
  if (!candidateIds.length) return toast("所选内容中没有可执行项目", "error");
  try {
    const result = await api("/api/actions", { method: "POST", body: JSON.stringify({ action, candidate_ids: candidateIds, options }) });
    toast(`批量任务 ${result.task_id} 已启动，共 ${candidateIds.length} 条`);
    pollTask(result.task_id);
    await refreshAll();
  } catch (error) { toast(error.message, "error"); }
}

async function pollTask(taskId) {
  for (let attempt = 0; attempt < 90; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 2000));
    const tasks = await api("/api/tasks");
    state.tasks = tasks;
    renderTasks();
    renderInventory();
    const task = tasks.find((item) => item.id === taskId);
    if (!task || task.status === "RUNNING") continue;
    if (task.status === "COMPLETED") {
      const failed = Number(task.result?.failed || 0);
      toast(failed ? `任务完成，失败 ${failed} 条；请查看对应行原因` : "任务完成", failed ? "error" : "ok");
      state.selectedCandidates.clear();
      await refreshAll();
    }
    else toast(`任务失败：${task.error}`, "error");
    return;
  }
  toast("任务仍在后台运行，请稍后刷新");
}

function renderPublications() {
  document.querySelector("#publicationTable").innerHTML = state.publications.length ? state.publications.map((item) => `
    <tr><td title="${escapeHtml(item.title || item.candidate_id)}">${escapeHtml((item.title || item.candidate_id).slice(0, 42))}</td><td class="platform-name">${escapeHtml(item.platform)}</td><td>${escapeHtml(item.account || "未指定")}</td><td>${dateText(item.scheduled_at)}</td><td><span class="status-pill ${statusClass(item.status)}">${statusLabels[item.status] || item.status}</span></td></tr>
  `).join("") : `<tr><td colspan="5"><div class="empty-state">尚无发布任务<br>先选择审核通过的成片加入队列</div></td></tr>`;
}

function renderAnalytics() {
  const k = state.overview.kpis;
  const cards = [["播放", k.views], ["点击", k.clicks], ["注册", k.registrations], ["首次观看", k.first_watch || 0]];
  document.querySelector("#analyticsSummary").innerHTML = cards.map((item, index) => `<article class="kpi-card ${index === 3 ? "highlight" : ""}"><span>${item[0]}</span><strong>${number(item[1])}</strong><small>${index ? `上一阶段转化见总览` : "平台最新快照"}</small></article>`).join("");
  document.querySelector("#keywordTable").innerHTML = state.overview.keywords.length ? state.overview.keywords.map((row) => `<tr><td>${escapeHtml(row.keyword)}</td><td>${row.candidates}</td><td>${number(row.views)}</td><td>${number(row.clicks)}</td><td>${number(row.registrations)}</td><td>${number(row.first_watch || 0)}</td><td>${row.score}</td></tr>`).join("") : `<tr><td colspan="7"><div class="empty-state">暂无关键词数据</div></td></tr>`;
  document.querySelector("#feedbackList").innerHTML = state.feedback.length ? state.feedback.map((item) => `<div class="feedback-item"><strong>${escapeHtml(item.action_type)} · ${escapeHtml(item.keyword || item.candidate_id || "内容")}</strong><span>${escapeHtml(item.reason)} · 信号分 ${Number(item.score).toFixed(2)}</span></div>`).join("") : `<div class="empty-state">当视频达到最低播放量且注册率或分享率突出时，系统会在这里提出关键词增强建议。<br>建议先审核，再应用到发现配置。</div>`;
}

function renderKeywordGroups() {
  const table = document.querySelector("#keywordGroupsTable");
  if (!table) return;
  table.innerHTML = state.keywordGroups.length ? state.keywordGroups.map((group) => {
    const langs = Object.entries(group.terms || {}).map(([lang, terms]) => `<small><strong>${escapeHtml(lang)}</strong>: ${escapeHtml((terms || []).join(", "))}</small>`).join("<br>");
    return `<tr>
      <td><strong>${escapeHtml(group.name)}</strong></td><td>${Number(group.weight).toFixed(1)}</td>
      <td>${group.days && group.days.length ? escapeHtml(group.days.join(",")) : "每天"}</td>
      <td><span class="status-pill ${group.active_today && group.enabled ? "ready" : ""}">${!group.enabled ? "已停用" : group.active_today ? "启用中" : "今日休眠"}</span></td>
      <td>${langs}</td>
      <td><button class="table-action" data-kw-edit="${escapeHtml(group.name)}">编辑</button><button class="table-action" data-kw-delete="${escapeHtml(group.name)}">删除</button></td>
    </tr>`;
  }).join("") : `<tr><td colspan="6"><div class="empty-state">暂无关键词组</div></td></tr>`;
  table.querySelectorAll("[data-kw-edit]").forEach((button) => button.addEventListener("click", () => fillKeywordForm(button.dataset.kwEdit)));
  table.querySelectorAll("[data-kw-delete]").forEach((button) => button.addEventListener("click", () => deleteKeywordGroup(button.dataset.kwDelete)));
}

function fillKeywordForm(name) {
  const group = state.keywordGroups.find((item) => item.name === name);
  if (!group) return;
  document.querySelector("#kwName").value = group.name;
  document.querySelector("#kwWeight").value = group.weight;
  document.querySelector("#kwDays").value = (group.days || []).join(",");
  document.querySelector("#kwEn").value = ((group.terms || {}).en || []).join(", ");
  document.querySelector("#kwZh").value = ((group.terms || {})["zh-CN"] || []).join(", ");
  toast(`已载入 ${name}，修改后点保存`);
}

async function deleteKeywordGroup(name) {
  if (!confirm(`删除关键词组 ${name}？`)) return;
  try {
    await api("/api/keywords", { method: "POST", body: JSON.stringify({ name, delete: true }) });
    toast("已删除");
    await refreshAll();
  } catch (error) { toast(error.message, "error"); }
}

function renderWorkers() {
  document.querySelector("#workerGrid").innerHTML = state.workers.length ? state.workers.map((worker) => `
    <article class="worker-card"><header><div><strong>${escapeHtml(worker.name)}</strong><small>${escapeHtml(worker.role)} · ${escapeHtml(worker.host)}</small></div><span class="status-pill ready">${escapeHtml(worker.status)}</span></header><p>最后心跳 ${dateText(worker.last_seen)}${worker.current_job ? ` · ${escapeHtml(worker.current_job)}` : ""}</p></article>
  `).join("") : `<div class="empty-state">暂无运行节点</div>`;
}

function renderSettings() {
  if (!state.settings) return;
  const edit = state.settings.edit || {};
  const remotion = state.settings.remotion || {};
  const brand = state.settings.brand || {};
  const watermark = brand.watermark || {};
  const cover = brand.cover || {};
  const endcard = brand.endcard || {};
  const duration = edit.output_duration_sec || [12, 30];
  document.querySelector("#settingRenderEngine").value = edit.render_engine || "ffmpeg";
  document.querySelector("#settingMinDuration").value = duration[0] ?? 12;
  document.querySelector("#settingMaxDuration").value = duration[1] ?? 30;
  document.querySelector("#settingMaxSegments").value = edit.max_segments_per_source ?? 3;
  document.querySelector("#settingLayout").value = edit.layout_mode || "original";
  document.querySelector("#settingWatermarkImage").value = watermark.image || "";
  document.querySelector("#settingWatermarkPosition").value = watermark.position || "top_left";
  document.querySelector("#settingWatermarkWidth").value = watermark.width || 170;
  document.querySelector("#settingWatermarkOpacity").value = watermark.opacity ?? 0.92;
  document.querySelector("#settingCoverMode").value = cover.mode || "frame";
  document.querySelector("#settingCoverImage").value = cover.image || "";
  document.querySelector("#settingEndcardMode").value = endcard.mode || "generated";
  document.querySelector("#settingEndcardImage").value = endcard.image || "";
  document.querySelector("#settingEndcardSite").value = endcard.site || "";
  document.querySelector("#settingCta").value = brand.cta || "";
  document.querySelector("#settingRemotionTopBadge").value = remotion.top_badge || "";
  document.querySelector("#settingRemotionHeadline").value = remotion.bottom_headline || "";
  document.querySelector("#settingRemotionSubline").value = remotion.bottom_subline || "";
  document.querySelector("#settingRemotionEndcardCta").value = remotion.endcard_cta || "";
  document.querySelector("#settingRemotionContentBgm").value = remotion.content_bgm_volume ?? 0.16;
  document.querySelector("#settingRemotionEndcardBgm").value = remotion.endcard_bgm_volume ?? 0.24;
  document.querySelector("#settingRemotionAddBgm").checked = remotion.add_bgm_under_source !== false;
  document.querySelector("#settingsConfigPath").textContent = `配置文件：${state.settings.config_path || "未加载"}`;
}

function sessionStatusClass(status) {
  if (status === "READY") return "ready";
  if (["EXPIRED", "INVALID"].includes(status)) return "failed";
  return "";
}

function sessionStatusLabel(status) {
  return ({
    READY: "可用",
    NEEDS_LOGIN: "需登录",
    EXPIRED: "已过期",
    INVALID: "无效",
  })[status] || status || "未检测";
}

function renderSessions() {
  const table = document.querySelector("#sessionTable");
  if (!table) return;
  table.innerHTML = state.sessions.length ? state.sessions.map((item) => `
    <tr>
      <td><strong>${escapeHtml(item.label || item.account)}</strong><small>${escapeHtml(item.platform)} · ${escapeHtml(item.account)}${item.owner ? ` · ${escapeHtml(item.owner)}` : ""}</small></td>
      <td>${escapeHtml(item.purpose || "source_discovery")}<small>${escapeHtml(item.notes || "")}</small></td>
      <td><span class="status-pill ${sessionStatusClass(item.status)}">${sessionStatusLabel(item.status)}</span><small title="${escapeHtml(item.status_reason || "")}">${escapeHtml(item.expires_at ? `到期 ${dateText(item.expires_at)}` : item.status_reason || "")}</small></td>
      <td>${Number(item.cookie_count || 0)}<small>${escapeHtml(item.cookie_file_path || "")}</small></td>
      <td><code class="command-snippet" title="${escapeHtml(item.login_command || "")}">${escapeHtml(item.login_command || "保存后生成")}</code></td>
      <td><button class="table-action" data-session-copy="${escapeHtml(item.login_command || "")}">复制命令</button><button class="table-action" data-session-check="${escapeHtml(item.platform)}:${escapeHtml(item.account)}">检测</button><button class="table-action" data-session-delete="${escapeHtml(item.platform)}:${escapeHtml(item.account)}">删除</button></td>
    </tr>
  `).join("") : `<tr><td colspan="6"><div class="empty-state">还没有登录态。先登记抖音/小红书账号，再在服务器终端执行生成的登录命令完成扫码。</div></td></tr>`;
  table.querySelectorAll("[data-session-copy]").forEach((button) => button.addEventListener("click", () => copyText(button.dataset.sessionCopy)));
  table.querySelectorAll("[data-session-check]").forEach((button) => button.addEventListener("click", () => checkSession(button.dataset.sessionCheck)));
  table.querySelectorAll("[data-session-delete]").forEach((button) => button.addEventListener("click", () => deleteSession(button.dataset.sessionDelete)));
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}

function renderServerHealth() {
  const element = document.querySelector("#serverHealth");
  if (!element || !state.health) return;
  const free = formatBytes(state.health.storage?.free_bytes || 0);
  const runtime = state.health.youtube_runtime ? `YouTube 运行时 ${state.health.youtube_runtime}` : "YouTube 运行时未就绪";
  const sessions = (state.health.ready_sessions || []).length;
  element.textContent = `${free} 可用 · ${runtime} · ${sessions} 个可用登录态`;
}

function renderUploads() {
  const table = document.querySelector("#assetTable");
  if (!table) return;
  if (!storedUploadToken()) {
    table.innerHTML = `<tr><td colspan="5"><div class="empty-state">输入管理令牌后加载服务器素材</div></td></tr>`;
    return;
  }
  table.innerHTML = state.uploads.length ? state.uploads.map((item) => `
    <tr>
      <td><strong>${escapeHtml(item.original_filename || item.id)}</strong><small>${escapeHtml(item.id)}</small></td>
      <td><span class="status-pill ${item.kind === "source" ? "ready" : ""}">${item.kind === "source" ? "源视频" : "Reaction"}</span></td>
      <td>${formatBytes(item.size)}</td>
      <td>${dateText(item.uploaded_at)}</td>
      <td><button class="table-action asset-download" data-upload-download="${escapeHtml(item.id)}" type="button">下载</button></td>
    </tr>
  `).join("") : `<tr><td colspan="5"><div class="empty-state">服务器还没有上传素材</div></td></tr>`;
  table.querySelectorAll("[data-upload-download]").forEach((button) => {
    button.addEventListener("click", () => downloadUploadAsset(button.dataset.uploadDownload));
  });
}

async function parseUploadResponse(response) {
  const text = await response.text();
  let payload = {};
  try { payload = text ? JSON.parse(text) : {}; }
  catch { payload = { error: `服务器返回了非 JSON 响应（HTTP ${response.status}）` }; }
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

async function uploadServerFile(file, kind, token, onProgress = () => {}) {
  const authToken = rememberUploadToken(token);
  if (!authToken) throw new Error("请输入服务器管理令牌");
  const init = await api("/api/uploads/init", {
    method: "POST",
    headers: { "X-Upload-Token": authToken },
    body: JSON.stringify({ filename: file.name, kind, size: file.size }),
  });
  const chunkSize = Number(init.chunk_bytes);
  const total = Number(init.chunk_count);
  for (let index = 0; index < total; index += 1) {
    const start = index * chunkSize;
    const end = Math.min(file.size, start + chunkSize);
    const response = await fetch(`/api/uploads/chunk?upload_id=${encodeURIComponent(init.id)}&index=${index}`, {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream", "X-Upload-Token": authToken },
      body: file.slice(start, end),
    });
    await parseUploadResponse(response);
    onProgress(Math.round(((index + 1) / total) * 94), `正在上传 ${index + 1}/${total}`);
  }
  onProgress(97, "服务器正在合并文件");
  const completed = await api("/api/uploads/complete", {
    method: "POST",
    headers: { "X-Upload-Token": authToken },
    body: JSON.stringify({ upload_id: init.id }),
  });
  onProgress(100, kind === "source" ? "上传完成，已进入待制作库存" : "Reaction 上传完成");
  return completed;
}

async function downloadUploadAsset(uploadId) {
  const token = storedUploadToken();
  if (!token) return toast("请输入服务器管理令牌", "error");
  try {
    const payload = await api(`/api/uploads/${encodeURIComponent(uploadId)}/link`, {
      headers: { "X-Upload-Token": token },
    });
    if (!payload.download_url) throw new Error("服务器没有返回下载链接");
    window.location.assign(payload.download_url);
  } catch (error) {
    toast(`下载失败：${error.message}`, "error");
  }
}

async function copyText(value) {
  if (!value) return toast("还没有可复制的命令", "error");
  try {
    await navigator.clipboard.writeText(value);
    toast("登录命令已复制");
  } catch {
    toast(value);
  }
}

async function checkSession(value) {
  const [platform, account] = String(value || "").split(":");
  try {
    const updated = await api("/api/sessions", { method: "POST", body: JSON.stringify({ platform, account, check: true }) });
    state.sessions = state.sessions.map((item) => item.platform === platform && item.account === account ? updated : item);
    renderSessions();
    toast(`检测完成：${sessionStatusLabel(updated.status)}`);
  } catch (error) { toast(error.message, "error"); }
}

async function deleteSession(value) {
  const [platform, account] = String(value || "").split(":");
  if (!platform || !account || !confirm(`删除 ${platform}/${account} 的本地登录态？`)) return;
  try {
    await api("/api/sessions", { method: "POST", body: JSON.stringify({ platform, account, delete: true }) });
    toast("登录态已删除");
    await refreshAll();
  } catch (error) { toast(error.message, "error"); }
}

function fillCandidateSelects() {
  const ready = state.candidates.filter((item) => item.status === "APPROVED");
  const allOptions = state.candidates.map((item) => `<option value="${item.id}">${escapeHtml(item.title.slice(0, 42))}</option>`).join("");
  const readyOptions = ready.map((item) => `<option value="${item.id}">${escapeHtml(item.title.slice(0, 42))}</option>`).join("");
  document.querySelector("#scheduleCandidate").innerHTML = `<option value="">选择成片</option>${readyOptions}`;
  document.querySelector("#metricsCandidate").innerHTML = `<option value="">选择内容</option>${allOptions}`;
}

function openView(name) {
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === name));
  document.querySelectorAll(".view").forEach((item) => item.classList.toggle("active", item.id === `view-${name}`));
  document.querySelector("#viewEyebrow").textContent = views[name][0];
  document.querySelector("#viewTitle").textContent = views[name][1];
}

function openProductionDialog(candidateIds) {
  state.pendingProductionIds = [...new Set(candidateIds)];
  document.querySelector("#productionDialog").showModal();
}

async function uploadReactionFile(file, token) {
  return uploadServerFile(file, "reaction", token, (percent, message) => toast(`${message} · ${percent}%`));
}

function setUploadProgress(percent, message) {
  const panel = document.querySelector("#assetUploadProgress");
  panel.hidden = false;
  document.querySelector("#assetUploadMessage").textContent = message;
  document.querySelector("#assetUploadPercent").textContent = `${percent}%`;
  document.querySelector("#assetUploadBar").style.width = `${Math.max(0, Math.min(100, percent))}%`;
}

document.querySelector("#assetUploadForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = document.querySelector("#assetUploadButton");
  const file = document.querySelector("#assetUploadFile").files[0];
  const kind = document.querySelector("#assetUploadKind").value;
  const token = document.querySelector("#assetUploadToken").value;
  if (!file) return toast("请选择要上传的视频", "error");
  button.disabled = true;
  try {
    const uploaded = await uploadServerFile(file, kind, token, setUploadProgress);
    document.querySelector("#productionUploadToken").value = rememberUploadToken(token);
    document.querySelector("#assetUploadFile").value = "";
    toast(kind === "source" ? `源视频已入库：${uploaded.candidate_id}` : "Reaction 已保存到服务器");
    await refreshAll();
  } catch (error) {
    setUploadProgress(0, `上传失败：${error.message}`);
    toast(`上传失败：${error.message}`, "error");
  } finally {
    button.disabled = false;
  }
});

document.querySelector("#assetUploadToken").addEventListener("change", async (event) => {
  const token = rememberUploadToken(event.target.value);
  document.querySelector("#productionUploadToken").value = token;
  if (!token) return;
  try {
    state.uploads = await api("/api/uploads", { headers: { "X-Upload-Token": token } });
    renderUploads();
    toast("服务器资产已连接");
  } catch (error) {
    state.uploads = [];
    renderUploads();
    toast(`连接失败：${error.message}`, "error");
  }
});

document.querySelectorAll(".nav-item").forEach((button) => button.addEventListener("click", () => openView(button.dataset.view)));
document.querySelectorAll("[data-open-view]").forEach((button) => button.addEventListener("click", () => openView(button.dataset.openView)));
document.querySelector("#refreshButton").addEventListener("click", () => refreshAll(true));
document.querySelector("#discoverButton").addEventListener("click", () => document.querySelector("#discoverDialog").showModal());
document.querySelector("#closeDiscoverDialog").addEventListener("click", () => document.querySelector("#discoverDialog").close());
document.querySelector("#cancelDiscoverDialog").addEventListener("click", () => document.querySelector("#discoverDialog").close());
document.querySelector("#globalSearch").addEventListener("input", (event) => { state.search = event.target.value; renderInventory(); });
document.querySelector("#selectVisible").addEventListener("change", (event) => {
  filteredCandidates().forEach((item) => event.target.checked ? state.selectedCandidates.add(item.id) : state.selectedCandidates.delete(item.id));
  renderInventory();
});
document.querySelector("#batchDownload").addEventListener("click", () => runBatchAction("download", ["DISCOVERED", "DOWNLOAD_FAILED"]));
document.querySelector("#batchProduce").addEventListener("click", () => {
  const ids = selectedRows().filter((item) => ["DOWNLOADED", "PRODUCTION_FAILED", "REVISION_REQUIRED", "BLOCKED_RIGHTS"].includes(item.status)).map((item) => item.id);
  if (!ids.length) return toast("所选内容中没有可制作项目", "error");
  openProductionDialog(ids);
});
document.querySelector("#batchSkip").addEventListener("click", () => runBatchAction("skip", ["DISCOVERED"]));
document.querySelectorAll("#statusFilters button").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll("#statusFilters button").forEach((item) => item.classList.toggle("active", item === button));
  state.status = button.dataset.status;
  renderInventory();
}));

function updateDiscoverMode() {
  const platform = document.querySelector("#discoverPlatform").value;
  document.querySelector("#discoverLimitField").hidden = platform === "xiaohongshu";
  document.querySelector("#discoverUrl").required = platform === "xiaohongshu";
  const notes = {
    youtube: "可按关键词发现或粘贴视频 URL；巴甲词已内置，遇到登录验证时保存 YouTube 登录态。",
    bilibili: "可按关键词发现或粘贴视频 URL；登录态可提高稳定性和画质。",
    douyin: "可按关键词发现或粘贴作品 URL；服务器采集服务和登录态必须可用。",
    xiaohongshu: "小红书当前通过作品 URL 导入，需要先启动本机 5556 端口的 XHS 服务。",
    tiktok: "请优先粘贴具体视频 URL；巴甲相关内容需要服务器 TikTok 登录态。",
    facebook: "请粘贴具体视频或 Reels URL；服务器登录态必须有权访问该视频。",
  };
  document.querySelector("#discoverPlatformNote").textContent = notes[platform];
}

document.querySelector("#discoverPlatform").addEventListener("change", updateDiscoverMode);
updateDiscoverMode();

document.querySelector("#closeProductionDialog").addEventListener("click", () => document.querySelector("#productionDialog").close());
document.querySelector("#cancelProductionDialog").addEventListener("click", () => document.querySelector("#productionDialog").close());
document.querySelector("#productionForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const mode = document.querySelector("#productionReactionMode").value;
  const file = document.querySelector("#productionReactionFile").files[0];
  let reactionSource = document.querySelector("#productionReactionSource").value.trim();
  try {
    if (mode !== "none" && file) {
      const uploaded = await uploadReactionFile(file, document.querySelector("#productionUploadToken").value);
      reactionSource = uploaded.path;
    }
    if (mode !== "none" && !reactionSource) throw new Error("启用 Reaction 时必须填写服务器路径或上传视频");
    const options = {
      content_type: document.querySelector("#productionContentType").value,
      segment_strategy: document.querySelector("#productionSegmentStrategy").value,
      audio_policy: document.querySelector("#productionAudioPolicy").value,
      max_segments: Number(document.querySelector("#productionMaxSegments").value || 3),
      max_duration: Number(document.querySelector("#productionMaxDuration").value || 30),
      reaction_mode: mode,
      reaction_source: reactionSource,
      source_volume: Number(document.querySelector("#productionSourceVolume").value || 0.72),
      reaction_volume: Number(document.querySelector("#productionReactionVolume").value || 1),
      rights_status: document.querySelector("#productionRightsStatus").value,
    };
    document.querySelector("#productionDialog").close();
    await runBatchAction("produce", [], options, state.pendingProductionIds);
  } catch (error) { toast(error.message, "error"); }
});

document.querySelector("#discoverForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  document.querySelector("#discoverDialog").close();
  try {
    const platform = document.querySelector("#discoverPlatform").value;
    const url = document.querySelector("#discoverUrl").value.trim();
    const payload = url
      ? { action: "ingest", url }
      : { action: "discover", platform, limit: Number(document.querySelector("#discoverLimit").value) };
    const result = await api("/api/actions", { method: "POST", body: JSON.stringify(payload) });
    toast(`发现任务 ${result.task_id} 已启动`);
    pollTask(result.task_id);
  } catch (error) { toast(error.message, "error"); }
});

document.querySelector("#scheduleForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/api/publications", { method: "POST", body: JSON.stringify({ candidate_id: document.querySelector("#scheduleCandidate").value, platform: document.querySelector("#schedulePlatform").value, account: document.querySelector("#scheduleAccount").value, scheduled_at: document.querySelector("#scheduleTime").value || null }) });
    toast("已加入发布队列");
    await refreshAll();
  } catch (error) { toast(error.message, "error"); }
});

document.querySelector("#keywordForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const parseTerms = (value) => value.split(",").map((item) => item.trim()).filter(Boolean);
  const terms = {};
  const en = parseTerms(document.querySelector("#kwEn").value);
  const zh = parseTerms(document.querySelector("#kwZh").value);
  if (en.length) terms.en = en;
  if (zh.length) terms["zh-CN"] = zh;
  const days = parseTerms(document.querySelector("#kwDays").value);
  try {
    await api("/api/keywords", { method: "POST", body: JSON.stringify({
      name: document.querySelector("#kwName").value.trim(),
      weight: Number(document.querySelector("#kwWeight").value || 1),
      days, terms,
    }) });
    toast("关键词组已保存，下次发现即生效");
    await refreshAll();
  } catch (error) { toast(error.message, "error"); }
});

document.querySelector("#metricsForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/api/metrics", { method: "POST", body: JSON.stringify({ candidate_id: document.querySelector("#metricsCandidate").value, platform: document.querySelector("#metricsPlatform").value, views: document.querySelector("#metricsViews").value, clicks: document.querySelector("#metricsClicks").value, installs: document.querySelector("#metricsInstalls").value, registrations: document.querySelector("#metricsRegistrations").value }) });
    toast("平台快照已保存");
    await refreshAll();
  } catch (error) { toast(error.message, "error"); }
});

document.querySelector("#sessionForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const payload = {
      platform: document.querySelector("#sessionPlatform").value,
      account: document.querySelector("#sessionAccount").value.trim(),
      label: document.querySelector("#sessionLabel").value.trim(),
      owner: document.querySelector("#sessionOwner").value.trim(),
      purpose: document.querySelector("#sessionPurpose").value.trim(),
      login_url: document.querySelector("#sessionLoginUrl").value.trim(),
      cookies_json: document.querySelector("#sessionCookies").value.trim(),
      notes: document.querySelector("#sessionNotes").value.trim(),
    };
    await api("/api/sessions", { method: "POST", body: JSON.stringify(payload) });
    document.querySelector("#sessionCookies").value = "";
    toast("登录态配置已保存");
    await refreshAll();
  } catch (error) { toast(error.message, "error"); }
});

document.querySelector("#settingsForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const payload = {
      edit: {
        render_engine: document.querySelector("#settingRenderEngine").value,
        output_duration_sec: [Number(document.querySelector("#settingMinDuration").value || 12), Number(document.querySelector("#settingMaxDuration").value || 30)],
        max_segments_per_source: Number(document.querySelector("#settingMaxSegments").value || 3),
        layout_mode: document.querySelector("#settingLayout").value,
      },
      remotion: {
        top_badge: document.querySelector("#settingRemotionTopBadge").value.trim(),
        bottom_headline: document.querySelector("#settingRemotionHeadline").value.trim(),
        bottom_subline: document.querySelector("#settingRemotionSubline").value.trim(),
        endcard_cta: document.querySelector("#settingRemotionEndcardCta").value.trim(),
        content_bgm_volume: Number(document.querySelector("#settingRemotionContentBgm").value || 0),
        endcard_bgm_volume: Number(document.querySelector("#settingRemotionEndcardBgm").value || 0),
        add_bgm_under_source: document.querySelector("#settingRemotionAddBgm").checked,
      },
      brand: {
        cta: document.querySelector("#settingCta").value.trim(),
        watermark: {
          image: document.querySelector("#settingWatermarkImage").value.trim(),
          position: document.querySelector("#settingWatermarkPosition").value,
          width: Number(document.querySelector("#settingWatermarkWidth").value || 170),
          opacity: Number(document.querySelector("#settingWatermarkOpacity").value || 0.92),
        },
        cover: {
          mode: document.querySelector("#settingCoverMode").value,
          image: document.querySelector("#settingCoverImage").value.trim(),
        },
        endcard: {
          mode: document.querySelector("#settingEndcardMode").value,
          image: document.querySelector("#settingEndcardImage").value.trim(),
          site: document.querySelector("#settingEndcardSite").value.trim(),
        },
      },
    };
    state.settings = await api("/api/settings", { method: "POST", body: JSON.stringify(payload) });
    renderSettings();
    toast("系统设置已保存，下一次制作生效");
  } catch (error) { toast(error.message, "error"); }
});

const initialUploadToken = storedUploadToken();
document.querySelector("#assetUploadToken").value = initialUploadToken;
document.querySelector("#productionUploadToken").value = initialUploadToken;
refreshAll();
