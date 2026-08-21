const state = {
  overview: null,
  candidates: [],
  publications: [],
  xAuths: [],
  workers: [],
  feedback: [],
  downloadClaims: [],
  keywordGroups: [],
  hotKeywords: [],
  categoryKeywords: { rows: [] },
  settings: null,
  sessions: [],
  uploads: [],
  health: null,
  publishCapabilities: {},
  publishAccounts: [],
  tasks: [],
  selectedCandidates: new Set(),
  status: "",
  categoryFilter: "",
  search: "",
  pendingProductionIds: [],
  pendingPublishAsset: null,
  pendingPublishItem: null,
  posters: [],
  posterCounts: { ALL: 0, PENDING_SCREENING: 0, PENDING_REVIEW: 0, APPROVED: 0 },
  posterStatus: "",
  posterCategory: "",
  posterPagination: { page: 1, page_size: 24, total: 0, pages: 0 },
  posterLoading: false,
  posterError: "",
  posterRequestSerial: 0,
  posterAbortController: null,
  posterPendingActions: new Set(),
  posterPreviewIndex: -1,
  posterZoom: 1,
  pendingPosterDelete: null,
  designCandidate: null,
  designLayers: [],
  selectedDesignLayerId: "design-text",
  youtubeGrowth: {
    page: 1,
    pageSize: 20,
    pages: 0,
    total: 0,
    requestSerial: 0,
    abortController: null,
    loaded: false,
  },
};

const views = {
  overview: ["OPERATIONS", "内容生产总览"],
  inventory: ["INVENTORY", "内容库存"],
  posters: ["POSTER INVENTORY", "海报库存"],
  publishing: ["DISTRIBUTION", "发布队列"],
  analytics: ["GROWTH", "增长分析"],
  nodes: ["INFRASTRUCTURE", "运行节点"],
  settings: ["SYSTEM", "系统管理"],
};

const statusLabels = {
  DISCOVERED: "待筛选",
  DOWNLOADED: "待制作",
  PRODUCTION_RUNNING: "制作中",
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

const initialCategoryLabels = [
  "ai短剧", "明星名人歌手", "足球球星", "足球类", "新闻类", "音乐类",
  "肥皂剧（电视剧、电影）", "少儿剧", "成人频道", "纪录片（美食、动物、地区发展）",
  "综艺", "社交挑战", "舞蹈", "教程及优点展示类", "官方性质类",
  "合作类", "运营教学类", "教程及答疑类", "未分类",
];

const posterCategoryLabels = [
  ["time_location", "时间地点"],
  ["factor_analysis", "因素分析"],
  ["match_prediction", "预测比赛"],
  ["multi_schedule", "多赛程"],
  ["star_fans", "球星球迷"],
];

const matrixAccounts = [
  ["consumer_main", "JaguarTV Hoje"],
  ["consumer_football", "JaguarTV Futebol"],
  ["consumer_guide", "JaguarTV Guia"],
  ["consumer_entertainment", "JaguarTV Entretenimento"],
  ["partner_main", "JaguarTV Parceiros"],
  ["partner_embaixador", "JaguarTV Embaixador"],
  ["partner_revendedor", "JaguarTV Revendedor"],
  ["partner_academia", "Academia JaguarTV"],
];

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
    const [overview, candidates, publications, xAuths, workers, feedback, downloadClaims, keywordGroups, hotKeywords, categoryKeywords, tasks, settings, sessions, health, capabilities, uploads, posterCounts] = await Promise.all([
      api("/api/overview"), api("/api/candidates?limit=200"), api("/api/publications"), api("/api/x-auths"), api("/api/workers"), api("/api/feedback"), api("/api/download-claims"), api("/api/keywords"), api("/api/hot-keywords?date=today"), api("/api/category-keywords?date=today"), api("/api/tasks"), api("/api/settings"), api("/api/sessions"), api("/api/health"), api("/api/publish/capabilities"),
      api("/api/uploads").catch(() => []),
      api("/api/posters/counts"),
    ]);
    Object.assign(state, { overview, candidates, publications, xAuths, workers, feedback, downloadClaims, keywordGroups, hotKeywords, categoryKeywords, tasks, settings, sessions, health, publishCapabilities: capabilities, uploads, posterCounts });
    renderAll();
    if (document.querySelector("#view-posters").classList.contains("active")) await loadPosters();
    if (document.querySelector("#view-analytics").classList.contains("active")) await loadYouTubeAnalytics();
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
  renderXAuths();
  renderInventory();
  renderPosterCounts();
  renderPublications();
  renderAnalytics();
  renderDownloadClaims();
  renderHotKeywords();
  renderCategoryKeywords();
  renderKeywordGroups();
  renderWorkers();
  renderSettings();
  renderSessions();
  renderUploads();
  renderServerHealth();
  renderTasks();
  fillCandidateSelects();
  document.querySelector("#navInventory").textContent = state.overview.kpis.inventory;
  document.querySelector("#navPosters").textContent = state.posterCounts.ALL || 0;
  document.querySelector("#navQueue").textContent = state.overview.kpis.scheduled;
  document.querySelector("#navNodes").textContent = state.workers.length;
}

function renderKpis() {
  const k = state.overview.kpis;
  const cards = [
    ["内容库存", k.inventory, "真实视频"], ["待审核成片", k.ready, `审核通过 ${number(k.approved || 0)}`],
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
  return inventoryRows()
    .filter((item) => {
      const haystack = `${item.display_title || ""} ${item.title || ""} ${item.id || ""} ${item.initial_category || ""} ${item.initial_keyword || ""} ${item.keyword || ""} ${(item.output_assets || []).map((asset) => asset.filename || "").join(" ")}`.toLowerCase();
      const statusMatch = !state.status || item.status === state.status || (state.status === "DOWNLOADED" && item.status === "PRODUCTION_RUNNING");
      const categoryMatch = !state.categoryFilter || (item.initial_category || "未分类") === state.categoryFilter;
      return statusMatch && categoryMatch && (!query || haystack.includes(query));
    })
    .sort((a, b) => {
      const aPart = Number(a.part_number || (a.output_assets || [])[0]?.part_number || 0);
      const bPart = Number(b.part_number || (b.output_assets || [])[0]?.part_number || 0);
      const aParent = aPart ? String(a.id || "").replace(/_part\d+$/, "") : "";
      const bParent = bPart ? String(b.id || "").replace(/_part\d+$/, "") : "";
      if (aParent || bParent) {
        const slicedDelta = (aParent ? 0 : 1) - (bParent ? 0 : 1);
        if (slicedDelta) return slicedDelta;
        const parentDelta = aParent.localeCompare(bParent, "zh-CN");
        if (parentDelta) return parentDelta;
        const partDelta = aPart - bPart;
        if (partDelta) return partDelta;
      }
      const aVariant = a.variant_group || (a.output_assets || [])[0]?.variant || "";
      const bVariant = b.variant_group || (b.output_assets || [])[0]?.variant || "";
      const variantOrder = (value) => value === "FB版" ? 0 : value === "通用版" ? 1 : 2;
      const variantDelta = variantOrder(aVariant) - variantOrder(bVariant);
      if (variantDelta) return variantDelta;
      const sourceDelta = String(a.platform || "").localeCompare(String(b.platform || ""), "zh-CN");
      if (sourceDelta) return sourceDelta;
      return new Date(b.updated_at || 0).getTime() - new Date(a.updated_at || 0).getTime();
    });
}

function inventoryRows() {
  const parents = [];
  const childrenMap = {};
  const candidateById = new Map();
  const candidateIds = new Set();

  state.candidates.forEach(item => {
    candidateIds.add(item.id);
    candidateById.set(item.id, item);
    if (item.parent_id) {
      if (!childrenMap[item.parent_id]) childrenMap[item.parent_id] = [];
      childrenMap[item.parent_id].push(item);
    } else {
      parents.push(item);
    }
  });

  const rows = parents.flatMap((item) => {
    const children = childrenMap[item.id] || [];

    item.is_parent = true;
    item.children = children.sort((a, b) => new Date(a.created_at) - new Date(b.created_at));
    return [item];
  });
  Object.entries(childrenMap).forEach(([parentId, children]) => {
    const parent = candidateById.get(parentId);
    const parentMatchesStatus = parent && (
      !state.status ||
      parent.status === state.status ||
      (state.status === "DOWNLOADED" && parent.status === "PRODUCTION_RUNNING")
    );
    if (candidateIds.has(parentId) && parentMatchesStatus) return;
    children.forEach((child) => {
      child.is_parent = false;
      child.is_child = false;
      child.is_orphan_part = true;
      rows.push(child);
    });
  });
  return rows;
}

function renderInventory() {
  const rows = filteredCandidates();
  renderCategoryFilters();
  document.querySelector("#inventoryCount").textContent = `${rows.length} 条内容`;
  document.querySelector("#inventoryTable").innerHTML = rows.length ? rows.map((item) => {
    const thumb = item.cover_url || item.thumbnail_url;
    const task = activeTaskFor(item.id);
    const status = task ? `${task.action === "download" ? "下载" : task.action === "produce" ? "制作" : "处理"}中` : (statusLabels[item.status] || item.status);
    const failure = item.failure_detail ? `<small class="failure-reason" title="${escapeHtml(item.failure_detail)}">${escapeHtml(failureReason(item.failure_detail))}</small>` : "";
    const outro = sourceOutroText(item.source_outro_trim);
    const publicationNote = publicationStateText(item.publication_state);
    const isChild = !!item.is_child;
    const isParent = !!item.is_parent;
    return `
    <tr class="${isChild ? 'child-slice-row' : ''}" style="${isChild ? 'background-color: var(--surface-hover);' : ''}">
      <td class="check-column"><input class="candidate-checkbox" type="checkbox" data-candidate-select="${item.id}" ${state.selectedCandidates.has(item.id) ? "checked" : ""} aria-label="选择 ${escapeHtml(item.display_title || item.title || item.id)}"></td>
      <td style="${isChild ? 'padding-left: 2rem;' : ''}"><div class="content-cell">${thumb ? `<img class="mini-cover" src="${thumb}" alt="" loading="lazy" referrerpolicy="no-referrer">` : `<div class="mini-cover"></div>`}<div><strong title="${escapeHtml(item.display_title || item.title)}">${escapeHtml(item.display_title || item.title || "未命名内容")}${item.published_flag ? `<span class="badge-published">Published</span>` : ""}<span class="category-pill">${escapeHtml(item.initial_category || "未分类")}</span></strong><small>${escapeHtml(item.platform)} · ${item.id}${item.initial_keyword ? ` · ${escapeHtml(item.initial_keyword)}` : item.keyword ? ` · ${escapeHtml(item.keyword)}` : ""}</small></div></div></td>
      <td>${escapeHtml(item.platform)}</td>
      <td><strong>${escapeHtml(item.content_type || "unknown")}</strong><small>${escapeHtml(item.segment_strategy || "未分析")} · ${escapeHtml(item.audio_policy || "自动")}</small>${outro}</td>
      <td>${Number(item.highlight_score || 0).toFixed(1)}</td>
      <td><span title="${escapeHtml(scoreTooltip(item.score_breakdown))}">${Number(item.score || 0).toFixed(1)}</span></td>
      <td><span class="status-pill ${task ? "running" : statusClass(item.status)}">${status}</span>${publicationNote}${failure}</td><td>${dateText(item.updated_at)}</td>
      <td>${task ? `<span class="row-progress">${task.progress || 0}%</span>` : candidateAction(item) + (isParent && item.status !== 'DOWNLOAD_FAILED' ? ` <button class="secondary-button" style="margin-top: 4px;" onclick="openProductionDialog('${item.id}')">手动切片</button>` : '')}</td>
    </tr>`;
  }).join("") : `<tr><td colspan="9"><div class="empty-state">没有符合条件的内容</div></td></tr>`;
  document.querySelectorAll("[data-candidate-select]").forEach((checkbox) => checkbox.addEventListener("change", () => {
    if (checkbox.checked) state.selectedCandidates.add(checkbox.dataset.candidateSelect);
    else state.selectedCandidates.delete(checkbox.dataset.candidateSelect);
    updateBatchToolbar();
  }));
  document.querySelectorAll("[data-candidate-action]").forEach((button) => button.addEventListener("click", () => runCandidateAction(button.dataset.candidateAction, button.dataset.candidateId)));
  document.querySelectorAll("[data-delete-id]").forEach((button) => button.addEventListener("click", () => deleteCandidates([button.dataset.deleteId])));
  document.querySelectorAll("[data-review-decision]").forEach((button) => button.addEventListener("click", () => submitReview(button.dataset.reviewDecision, button.dataset.candidateId)));
  document.querySelectorAll("[data-publish-asset]").forEach((button) => button.addEventListener("click", () => openPublishDialog(button.dataset.publishAsset, button.dataset.publishCandidate || "")));
  document.querySelectorAll("[data-design-id]").forEach((button) => button.addEventListener("click", () => openDesignDialog(button.dataset.designId, button.dataset.designAssets || "")));
  updateBatchToolbar();
}

function posterRequestId(action, posterId) {
  const suffix = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${action}-${posterId}-${suffix}`.slice(0, 160);
}

function renderPosterCounts() {
  document.querySelectorAll("[data-poster-count]").forEach((element) => {
    element.textContent = number(state.posterCounts[element.dataset.posterCount] || 0);
  });
  const nav = document.querySelector("#navPosters");
  if (nav) nav.textContent = number(state.posterCounts.ALL || 0);
}

function renderPosterCategoryFilters() {
  const container = document.querySelector("#posterCategoryFilters");
  container.innerHTML = [
    `<button class="${state.posterCategory ? "" : "active"}" data-poster-category="" type="button">全部分类</button>`,
    ...posterCategoryLabels.map(([id, label]) => `<button class="${state.posterCategory === id ? "active" : ""}" data-poster-category="${id}" type="button">${label}</button>`),
  ].join("");
  container.querySelectorAll("[data-poster-category]").forEach((button) => button.addEventListener("click", () => {
    if (state.posterLoading || state.posterCategory === button.dataset.posterCategory) return;
    state.posterCategory = button.dataset.posterCategory || "";
    state.posterPagination.page = 1;
    loadPosters();
  }));
}

function posterActionButtons(item) {
  const pending = (action) => state.posterPendingActions.has(`${action}:${item.id}`);
  const deleteButton = `<button class="table-action danger-action" data-poster-delete="${escapeHtml(item.id)}" type="button" ${pending("delete") ? "disabled" : ""}>${pending("delete") ? "删除中" : "删除"}</button>`;
  const previewButton = `<button class="table-action" data-poster-preview="${escapeHtml(item.id)}" type="button">预览</button>`;
  const approveButton = ["PENDING_SCREENING", "PENDING_REVIEW"].includes(item.status_id)
    ? `<button class="table-action" data-poster-approve="${escapeHtml(item.id)}" type="button" ${pending("approve") ? "disabled" : ""}>${pending("approve") ? "处理中" : "通过"}</button>`
    : "";
  const downloadButton = item.status_id === "APPROVED"
    ? `<a class="table-action" data-poster-download="${escapeHtml(item.id)}" href="${escapeHtml(item.download_url)}" download>下载</a>`
    : "";
  return `<div class="row-actions poster-row-actions">${deleteButton}${previewButton}${approveButton}${downloadButton}</div>`;
}

function renderPosterInventory() {
  renderPosterCounts();
  renderPosterCategoryFilters();
  const table = document.querySelector("#posterInventoryTable");
  const pagination = document.querySelector("#posterPagination");
  const posterTableEmpty = state.posterLoading || Boolean(state.posterError) || state.posters.length === 0;
  table.closest(".poster-table-wrap").classList.toggle("poster-table-empty", posterTableEmpty);
  document.querySelector("#posterInventoryCount").textContent = `${number(state.posterPagination.total || 0)} 张海报`;
  if (state.posterLoading) {
    table.innerHTML = `<tr><td colspan="6"><div class="empty-state poster-loading-state"><span class="loading-spinner"></span>正在加载海报</div></td></tr>`;
    pagination.hidden = true;
    return;
  }
  if (state.posterError) {
    table.innerHTML = `<tr><td colspan="6"><div class="empty-state poster-error-state"><span>${escapeHtml(state.posterError)}</span><button class="secondary-button" id="retryPosters" type="button">重试</button></div></td></tr>`;
    document.querySelector("#retryPosters").addEventListener("click", loadPosters);
    pagination.hidden = true;
    return;
  }
  table.innerHTML = state.posters.length ? state.posters.map((item) => {
    const reviewedAt = item.approved_at || item.screened_at;
    const statusClassName = item.status_id === "APPROVED" ? "ready" : item.status_id === "PENDING_REVIEW" ? "running" : "";
    const unknownClass = item.category_known ? "" : " unknown";
    return `<tr>
      <td><div class="content-cell poster-content-cell"><div class="poster-thumb-shell"><img class="poster-thumbnail" src="${escapeHtml(item.thumbnail_url)}" alt="${escapeHtml(item.name)}" loading="lazy"><span class="poster-thumb-error" hidden>图片失效</span></div><div><strong title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</strong><small>${escapeHtml(item.id)}</small></div></div></td>
      <td><span class="category-pill${unknownClass}" title="${escapeHtml(item.category_id)}">${escapeHtml(item.category_label)}</span></td>
      <td><span class="status-pill ${statusClassName}">${escapeHtml(item.status_label)}</span></td>
      <td>${dateText(item.created_at)}</td>
      <td>${dateText(reviewedAt)}</td>
      <td>${posterActionButtons(item)}</td>
    </tr>`;
  }).join("") : `<tr><td colspan="6"><div class="empty-state">当前筛选条件下没有海报</div></td></tr>`;

  table.querySelectorAll(".poster-thumbnail").forEach((image) => image.addEventListener("error", () => {
    image.hidden = true;
    image.nextElementSibling.hidden = false;
  }, { once: true }));
  table.querySelectorAll("[data-poster-preview]").forEach((button) => button.addEventListener("click", () => openPosterPreview(button.dataset.posterPreview)));
  table.querySelectorAll("[data-poster-approve]").forEach((button) => button.addEventListener("click", () => approvePoster(button.dataset.posterApprove)));
  table.querySelectorAll("[data-poster-delete]").forEach((button) => button.addEventListener("click", () => openPosterDelete(button.dataset.posterDelete)));
  table.querySelectorAll("[data-poster-download]").forEach((link) => link.addEventListener("click", () => toast("正在下载审核通过的海报")));

  const { page, pages, total } = state.posterPagination;
  pagination.hidden = total === 0;
  document.querySelector("#posterPageSummary").textContent = pages ? `第 ${page} / ${pages} 页 · 共 ${number(total)} 张` : "";
  document.querySelector("#posterPreviousPage").disabled = page <= 1;
  document.querySelector("#posterNextPage").disabled = !pages || page >= pages;
}

async function loadPosters() {
  state.posterAbortController?.abort();
  const controller = new AbortController();
  state.posterAbortController = controller;
  const serial = ++state.posterRequestSerial;
  state.posterLoading = true;
  state.posterError = "";
  renderPosterInventory();
  const query = new URLSearchParams({
    page: String(state.posterPagination.page || 1),
    page_size: String(state.posterPagination.page_size || 24),
  });
  if (state.posterStatus) query.set("status", state.posterStatus);
  if (state.posterCategory) query.set("category", state.posterCategory);
  try {
    const payload = await api(`/api/posters?${query}`, { signal: controller.signal });
    if (serial !== state.posterRequestSerial) return;
    state.posters = payload.items || [];
    state.posterCounts = payload.counts || state.posterCounts;
    state.posterPagination = payload.pagination || state.posterPagination;
  } catch (error) {
    if (error.name === "AbortError" || serial !== state.posterRequestSerial) return;
    state.posterError = `海报列表加载失败：${error.message}`;
  } finally {
    if (serial === state.posterRequestSerial) {
      state.posterLoading = false;
      renderPosterInventory();
    }
  }
}

async function approvePoster(posterId) {
  const item = state.posters.find((poster) => poster.id === posterId);
  if (!item || !["PENDING_SCREENING", "PENDING_REVIEW"].includes(item.status_id)) return;
  const target = item.status_id === "PENDING_SCREENING" ? "待审核" : "审核通过";
  if (!confirm(`确认通过“${item.name}”并进入${target}？`)) return;
  const pendingKey = `approve:${posterId}`;
  if (state.posterPendingActions.has(pendingKey)) return;
  state.posterPendingActions.add(pendingKey);
  renderPosterInventory();
  try {
    const result = await api(`/api/posters/${encodeURIComponent(posterId)}/approve`, {
      method: "POST",
      headers: { "X-Request-ID": posterRequestId("approve", posterId) },
      body: JSON.stringify({
        actor: localStorage.getItem("jaguartvOperatorName") || "dashboard",
        expected_status: item.status_id,
      }),
    });
    toast(`海报已进入${result.status_label}`);
    await loadPosters();
  } catch (error) {
    toast(`通过失败：${error.message}`, "error");
  } finally {
    state.posterPendingActions.delete(pendingKey);
    if (!state.posterLoading) renderPosterInventory();
  }
}

function openPosterDelete(posterId) {
  const item = state.posters.find((poster) => poster.id === posterId);
  if (!item || state.posterPendingActions.has(`delete:${posterId}`)) return;
  state.pendingPosterDelete = item;
  document.querySelector("#posterDeleteName").textContent = item.name;
  const thumbnail = document.querySelector("#posterDeleteThumbnail");
  thumbnail.hidden = false;
  thumbnail.src = item.thumbnail_url;
  thumbnail.alt = item.name;
  thumbnail.onerror = () => { thumbnail.hidden = true; };
  document.querySelector("#confirmPosterDelete").disabled = false;
  document.querySelector("#posterDeleteDialog").showModal();
}

function applyPosterZoom(value) {
  state.posterZoom = Math.min(4, Math.max(0.25, value));
  document.querySelector("#posterPreviewImage").style.transform = `scale(${state.posterZoom})`;
  document.querySelector("#posterZoomValue").textContent = `${Math.round(state.posterZoom * 100)}%`;
}

function showPosterPreview(index) {
  if (!state.posters.length || index < 0 || index >= state.posters.length) return;
  state.posterPreviewIndex = index;
  const item = state.posters[index];
  document.querySelector("#posterPreviewTitle").textContent = item.name;
  document.querySelector("#posterPreviewMeta").textContent = `${item.category_label} · ${item.status_label} · 创建于 ${dateText(item.created_at)}`;
  document.querySelector("#posterPreviewPrevious").disabled = index <= 0;
  document.querySelector("#posterPreviewNext").disabled = index >= state.posters.length - 1;
  const image = document.querySelector("#posterPreviewImage");
  const message = document.querySelector("#posterPreviewMessage");
  applyPosterZoom(1);
  image.hidden = true;
  message.hidden = false;
  message.textContent = "正在加载图片";
  message.classList.remove("error");
  image.onload = () => {
    image.hidden = false;
    message.hidden = true;
  };
  image.onerror = () => {
    image.hidden = true;
    message.hidden = false;
    message.textContent = "图片不存在、格式错误或加载失败";
    message.classList.add("error");
  };
  image.alt = item.name;
  image.src = item.preview_url;
}

function openPosterPreview(posterId) {
  const index = state.posters.findIndex((item) => item.id === posterId);
  if (index < 0) return;
  showPosterPreview(index);
  const dialog = document.querySelector("#posterPreviewDialog");
  if (!dialog.open) dialog.showModal();
}

function publicationStateText(state) {
  if (!state || !Object.keys(state).length) return "";
  const account = escapeHtml(state.account_label || state.account || "jaguartv vivo");
  if (state.status === "PUBLISHED") {
    const link = state.youtube_url ? ` · <a href="${escapeHtml(state.youtube_url)}" target="_blank" rel="noopener">YouTube 链接</a>` : "";
    const videoId = state.youtube_video_id ? ` · ${escapeHtml(state.youtube_video_id)}` : "";
    return `<small class="publication-note ready">已发布至 YouTube 账号：${account}${link}${videoId}</small>`;
  }
  if (["QUEUED", "SCHEDULED", "PUBLISHING"].includes(state.status)) {
    return `<small class="publication-note ready">已排队发布至 YouTube 账号：${account} · 计划发布时间：${dateText(state.scheduled_at)} ${escapeHtml(state.timezone || "")}</small>`;
  }
  if (state.event_type === "PUBLISH_BLOCKED_SOURCE_PLATFORM") {
    return `<small class="publication-note blocked">审核通过，但 YouTube 发布被来源门禁拦截：源素材来自 ${escapeHtml(state.source_platform || "YouTube")}</small>`;
  }
  if (state.event_type === "PUBLISH_BLOCKED_NO_ROUTE") {
    return `<small class="publication-note blocked">审核通过，但未匹配发布账号</small>`;
  }
  if (state.event_type === "PUBLISH_BLOCKED_NO_ASSET") {
    return `<small class="publication-note blocked">审核通过，但没有可发布的通用版成片</small>`;
  }
  return "";
}

function renderCategoryFilters() {
  const container = document.querySelector("#categoryFilters");
  if (!container) return;
  const counts = {};
  inventoryRows().forEach((item) => {
    if (state.status && item.status !== state.status && !(state.status === "DOWNLOADED" && item.status === "PRODUCTION_RUNNING")) return;
    const label = item.initial_category || "未分类";
    counts[label] = (counts[label] || 0) + 1;
  });
  container.innerHTML = [
    `<button class="${state.categoryFilter ? "" : "active"}" data-category-filter="" type="button">全部分类<span>${Object.values(counts).reduce((sum, value) => sum + value, 0)}</span></button>`,
    ...initialCategoryLabels.map((label) => `<button class="${state.categoryFilter === label ? "active" : ""}" data-category-filter="${escapeHtml(label)}" type="button">${escapeHtml(label)}<span>${counts[label] || 0}</span></button>`),
  ].join("");
  container.querySelectorAll("[data-category-filter]").forEach((button) => button.addEventListener("click", () => {
    state.categoryFilter = button.dataset.categoryFilter || "";
    state.selectedCandidates.clear();
    renderInventory();
  }));
}

function sourceOutroText(payload) {
  if (!payload) return `<small class="source-outro muted">原素材尾卡：未检测</small>`;
  const stateLabel = payload.state || (payload.applied ? "已自动裁剪" : "跳过");
  const trim = Number(payload.trim_end_sec || 0);
  const confidence = Number(payload.confidence || 0);
  const reason = payload.reason || "";
  const frames = [payload.before_frame, payload.after_frame].filter(Boolean).join(" / ");
  const detail = [reason, frames ? `证据：${frames}` : ""].filter(Boolean).join(" · ");
  return `<small class="source-outro" title="${escapeHtml(detail)}">原素材尾卡：${escapeHtml(stateLabel)}${trim ? ` · ${trim.toFixed(1)}s` : ""}${confidence ? ` · ${(confidence * 100).toFixed(0)}%` : ""}</small>`;
}

function failureReason(detail) {
  const text = String(detail || "");
  if (text.includes("Sign in to confirm you’re not a bot") || text.includes("Sign in to confirm you're not a bot")) return "YouTube 要求登录：在系统管理保存 YouTube 登录态后重试";
  if (text.includes("No supported JavaScript runtime")) return "YouTube 解析运行时未就绪：服务器需要 Deno 或 Node 22+";
  if (text.includes("service unreachable")) return "平台采集服务未启动或不可访问，请检查对应采集节点";
  if (text.includes("HTTP Error 412")) return "Bilibili 拒绝当前请求：保存 B站登录态后重试";
  if (text.includes("Video unavailable")) return "源视频已删除、设为私密或当前地区不可用";
  if (text.includes("HTTP Error 403")) return "源站拒绝下载（403）：建议配置 cookies 或更换素材";
  if (text.includes("Language gate")) return "葡语脚本识别异常：请删除该失败项，检查翻译/语音配置后重新拉取素材";
  if (text.includes("exit status 69")) return "历史并发渲染失败：请删除该失败项，使用新任务重新进入流程";
  if (text.includes("BLOCKED_RIGHTS")) return "历史权利状态阻断：请删除该失败项，按人工审核流程重新导入";
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
  const downloadableStatuses = ["DISCOVERED", "DOWNLOAD_FAILED"];
  const producibleStatuses = ["DISCOVERED", "DOWNLOAD_FAILED", "DOWNLOADED", "PRODUCTION_FAILED", "REVISION_REQUIRED", "BLOCKED_RIGHTS"];
  document.querySelector("#selectionCount").textContent = `已选 ${rows.length} 条`;
  document.querySelector("#batchDownload").disabled = !rows.some((item) => downloadableStatuses.includes(item.status));
  document.querySelector("#batchProduce").disabled = !rows.some((item) => producibleStatuses.includes(item.status) || (item.status === "APPROVED" && !outputAssetsFor(item).length));
  document.querySelector("#batchDelete").disabled = rows.length === 0;
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
    <div><strong>${escapeHtml({discover:"发现素材",ingest:"导入素材",download:"下载素材",produce:"制作成片"}[task.action] || task.action)}</strong><span>${escapeHtml(task.message || "处理中")}${task.total > 1 ? ` · ${task.completed || 0}/${task.total}` : ""}</span></div>
    <div class="progress-track"><span style="width:${Math.max(2, Number(task.progress || 0))}%"></span></div><b>${Number(task.progress || 0)}%</b>
  </div>`).join("");
}

function statusClass(status) {
  if (status === "PRODUCTION_RUNNING") return "running";
  if (status === "READY_FOR_REVIEW" || status === "PUBLISHED" || status === "APPROVED") return "ready";
  if (status.includes("FAILED") || status === "REVISION_REQUIRED" || status === "BLOCKED_RIGHTS") return "failed";
  return "";
}

function outputAssetsFor(item) {
  if (Array.isArray(item.output_assets) && item.output_assets.length) return item.output_assets;
  if (!item.video_url) return [];
  const filename = `${String(item.id || "jaguartv-video").replace(/[^0-9A-Za-z_-]+/g, "_")}.mp4`;
  return [{
    id: item.id,
    label: "成片",
    video_url: item.video_url,
    download_url: item.download_url || `${item.video_url}${item.video_url.includes("?") ? "&" : "?"}download=1`,
    server_url: item.server_url || item.video_url,
    filename,
  }];
}

function isDesignOutput(asset) {
  const text = `${asset.label || ""} ${asset.batch_label || ""} ${asset.content_type || ""} ${asset.filename || ""}`;
  return text.includes("文案设计版") || text.includes("design_overlay");
}

function outputDesignButton(item, asset) {
  if (!asset?.id || isDesignOutput(asset)) return "";
  const payload = escapeHtml(JSON.stringify([{
    id: asset.id,
    label: asset.label || "",
    variant: asset.variant || "",
    video_url: asset.video_url || "",
    filename: asset.filename || "",
  }]));
  return `<button class="table-action" data-design-id="${escapeHtml(item.id)}" data-design-assets='${payload}' type="button">文案设计</button>`;
}

function outputActionLinks(asset, item = null) {
  const videoUrl = String(asset.video_url || "");
  if (!videoUrl) return "";
  const downloadUrl = String(asset.download_url || `${videoUrl}${videoUrl.includes("?") ? "&" : "?"}download=1`);
  const serverUrl = String(asset.server_url || videoUrl);
  const filename = String(asset.filename || `${asset.id || "jaguartv-video"}.mp4`).replace(/[^0-9A-Za-z_.-]+/g, "_");
  const payload = escapeHtml(JSON.stringify({ ...asset, download_url: downloadUrl, server_url: serverUrl, filename }));
  const publishButton = item?.status === "APPROVED" ? `<button class="table-action" data-publish-candidate="${escapeHtml(item.id)}" data-publish-asset='${payload}' type="button">发布</button>` : "";
  return `${publishButton}<a class="table-action" href="${escapeHtml(serverUrl)}" target="_blank" rel="noopener">服务器成片</a>${item ? outputDesignButton(item, asset) : ""}`;
}

function approvedOutputActions(item) {
  const assets = outputAssetsFor(item);
  if (!assets.length) {
    return `<button class="table-action" data-candidate-action="produce" data-candidate-id="${item.id}">生成成片</button>`;
  }
  if (assets.length > 1) {
    return `<div class="row-actions"><details class="output-menu"><summary class="table-action">查看全部 ${assets.length} 条</summary><div class="output-menu-panel">${assets.map((asset) => `<div class="output-menu-row"><strong title="${escapeHtml(asset.label || asset.id || "成片")}">${escapeHtml(asset.label || asset.id || "成片")}</strong><div class="row-actions output-menu-actions">${outputActionLinks(asset, item)}</div></div>`).join("")}</div></details></div>`;
  }
  const allOutputs = "";
  return `<div class="row-actions">${outputActionLinks(assets[0], item)}${allOutputs}</div>`;
}

function candidateAction(item) {
  const sourceLink = item.url ? `<button class="table-action" onclick="window.open('${escapeHtml(item.url)}','_blank')">源页</button>` : "";
  const deleteButton = `<button class="table-action danger-action" data-delete-id="${item.id}">删除</button>`;
  if (item.status === "DISCOVERED") return `${sourceLink}<button class="table-action" data-candidate-action="download" data-candidate-id="${item.id}">下载</button><button class="table-action" data-candidate-action="produce" data-candidate-id="${item.id}">制作</button>${deleteButton}`;
  if (item.status === "DOWNLOAD_FAILED") return `${sourceLink}<button class="table-action" data-candidate-action="download" data-candidate-id="${item.id}">重新下载</button><button class="table-action" data-candidate-action="produce" data-candidate-id="${item.id}">下载并制作</button>${deleteButton}`;
  if (item.status === "PRODUCTION_FAILED") return `${sourceLink}<button class="table-action" data-candidate-action="produce" data-candidate-id="${item.id}">重新制作</button>${deleteButton}`;
  if (["DOWNLOADED", "REVISION_REQUIRED", "BLOCKED_RIGHTS"].includes(item.status)) return `<button class="table-action" data-candidate-action="produce" data-candidate-id="${item.id}">制作</button>${deleteButton}`;
  if (item.status === "READY_FOR_REVIEW") {
    return `${approvedOutputActions(item)}<button class="table-action" data-review-decision="APPROVED" data-candidate-id="${item.id}">通过</button><button class="table-action" data-review-decision="REVISION_REQUIRED" data-candidate-id="${item.id}">返工</button>${deleteButton}`;
  }
  if (item.status === "APPROVED") return `${approvedOutputActions(item)}${deleteButton}`;
  return `${deleteButton}`;
}

async function submitReview(decision, candidateId) {
  try {
    await api("/api/review", { method: "POST", body: JSON.stringify({ candidate_id: candidateId, decision, reviewer: "dashboard" }) });
    toast(decision === "APPROVED" ? "已通过，可加入发布队列" : "已标记为需返工");
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

async function deleteCandidates(candidateIds) {
  const ids = Array.from(new Set((candidateIds || []).filter(Boolean)));
  if (!ids.length) return toast("请选择要删除的视频", "error");
  if (!confirm(`确定删除 ${ids.length} 条内容？这会清空数据库记录、源视频、审核包和库存成片，无法撤销。`)) return;
  try {
    const result = await api("/api/candidates/delete", { method: "POST", body: JSON.stringify({ candidate_ids: ids }) });
    ids.forEach((id) => state.selectedCandidates.delete(id));
    const megabytes = (Number(result.bytes_freed || 0) / 1024 / 1024).toFixed(1);
    toast(`已删除 ${result.deleted || ids.length} 条，释放约 ${megabytes} MB`);
    await refreshAll();
  } catch (error) { toast(`删除失败：${error.message}`, "error"); }
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
      const successMessage = task.action === "ingest" && task.result?.status === "DOWNLOADED"
        ? "导入下载完成，已进入待制作"
        : "任务完成";
      toast(failed ? `任务完成，失败 ${failed} 条；请查看对应行原因` : successMessage, failed ? "error" : "ok");
      state.selectedCandidates.clear();
      await refreshAll();
    }
    else {
      if (task.action === "ingest") {
        state.status = "DOWNLOAD_FAILED";
        await refreshAll();
        toast("导入失败，已放入下载失败列表；请查看该行失败原因", "error");
        return;
      }
      toast(`任务失败：${task.error}`, "error");
    }
    return;
  }
  toast("任务仍在后台运行，请稍后刷新");
}

function renderPublications() {
  document.querySelector("#publicationTable").innerHTML = state.publications.length ? state.publications.map((item) => `
    <tr><td title="${escapeHtml(item.title || item.candidate_id)}">${escapeHtml((item.title || item.candidate_id).slice(0, 42))}</td><td class="platform-name">${escapeHtml(item.platform)}</td><td>${escapeHtml(item.account || "未指定")}</td><td>${dateText(item.scheduled_at)}</td><td><span class="status-pill ${statusClass(item.status)}">${statusLabels[item.status] || item.status}</span></td></tr>
  `).join("") : `<tr><td colspan="5"><div class="empty-state">尚无发布任务<br>先选择审核通过的成片加入队列</div></td></tr>`;
}

function authStatusLabel(status) {
  return {
    PENDING_CONFIRMATION: "待确认",
    AUTHORIZED: "可发布",
    REVOKED: "已撤销",
    NEEDS_REAUTH: "需重授",
  }[status] || status || "未授权";
}

function renderXAuths() {
  const accountSelect = document.querySelector("#xAuthAccount");
  if (accountSelect && !accountSelect.options.length) {
    accountSelect.innerHTML = matrixAccounts.map(([id, label]) => `<option value="${id}">${label} · ${id}</option>`).join("");
  }
  const byAccount = new Map(state.xAuths.map((item) => [item.account, item]));
  const rows = matrixAccounts.map(([id, label]) => {
    const item = byAccount.get(id) || { account: id, status: "", username: "", x_user_id: "", scopes: "", updated_at: "" };
    const isPending = item.status === "PENDING_CONFIRMATION";
    const canRevoke = item.status === "AUTHORIZED" || item.status === "PENDING_CONFIRMATION" || item.status === "NEEDS_REAUTH";
    return `
      <tr>
        <td><strong>${escapeHtml(label)}</strong><small>${escapeHtml(id)}</small></td>
        <td>${item.username ? `@${escapeHtml(item.username)}` : "未授权"}<small>${escapeHtml(item.display_name || "")}</small></td>
        <td>${escapeHtml(item.x_user_id || "")}</td>
        <td title="${escapeHtml(item.scopes || "")}">${escapeHtml((item.scopes || "").slice(0, 48))}</td>
        <td><span class="status-pill ${statusClass(item.status || "FAILED")}">${escapeHtml(authStatusLabel(item.status))}</span></td>
        <td>${dateText(item.updated_at || item.authorized_at)}</td>
        <td class="table-actions">
          ${isPending ? `<button class="secondary-button tiny-button" data-x-auth-action="confirm" data-account="${id}" type="button">确认</button>` : ""}
          ${canRevoke ? `<button class="secondary-button danger-button tiny-button" data-x-auth-action="revoke" data-account="${id}" type="button">撤销</button>` : ""}
        </td>
      </tr>
    `;
  });
  document.querySelector("#xAuthTable").innerHTML = rows.join("");
}

function renderAnalytics() {
  const k = state.overview.kpis;
  const cards = [["播放", k.views], ["点击", k.clicks], ["注册", k.registrations], ["首次观看", k.first_watch || 0]];
  document.querySelector("#analyticsSummary").innerHTML = cards.map((item, index) => `<article class="kpi-card ${index === 3 ? "highlight" : ""}"><span>${item[0]}</span><strong>${number(item[1])}</strong><small>${index ? `上一阶段转化见总览` : "平台最新快照"}</small></article>`).join("");
  document.querySelector("#keywordTable").innerHTML = state.overview.keywords.length ? state.overview.keywords.map((row) => `<tr><td>${escapeHtml(row.keyword)}</td><td>${row.candidates}</td><td>${number(row.views)}</td><td>${number(row.clicks)}</td><td>${number(row.registrations)}</td><td>${number(row.first_watch || 0)}</td><td>${row.score}</td></tr>`).join("") : `<tr><td colspan="7"><div class="empty-state">暂无关键词数据</div></td></tr>`;
  document.querySelector("#feedbackList").innerHTML = state.feedback.length ? state.feedback.map((item) => `<div class="feedback-item"><strong>${escapeHtml(item.action_type)} · ${escapeHtml(item.keyword || item.candidate_id || "内容")}</strong><span>${escapeHtml(item.reason)} · 信号分 ${Number(item.score).toFixed(2)}</span></div>`).join("") : `<div class="empty-state">当视频达到最低播放量且注册率或分享率突出时，系统会在这里提出关键词增强建议。<br>建议先审核，再应用到发现配置。</div>`;
}

const ptBRNumber = (value, options = {}) => value === null || value === undefined
  ? "暂无数据"
  : new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 2, ...options }).format(Number(value));

function youtubeDuration(value) {
  if (value === null || value === undefined) return "暂无数据";
  const seconds = Number(value);
  const rounded = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(rounded / 60);
  const remainder = rounded % 60;
  return `${ptBRNumber(seconds)} s · ${minutes}:${String(remainder).padStart(2, "0")}`;
}

function youtubePercentage(value, unavailable = false) {
  if (value === null || value === undefined) return unavailable ? "暂不可用" : "暂无数据";
  return `${ptBRNumber(value)}%`;
}

function saoPauloDateTime(value) {
  if (!value) return "暂无数据";
  return new Intl.DateTimeFormat("pt-BR", {
    timeZone: "America/Sao_Paulo",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function youtubeSyncStatus(value) {
  return {
    PENDING: "等待同步",
    IN_PROGRESS: "同步中",
    SUCCESS: "同步成功",
    PARTIAL: "部分数据",
    RETRY: "等待重试",
    BLOCKED: "暂不可用",
    NEEDS_REAUTH: "需要重新授权",
  }[value] || value || "尚未调度";
}

function youtubeGrowthQuery() {
  const params = new URLSearchParams({
    range: document.querySelector("#youtubeGrowthRange").value,
    account_id: document.querySelector("#youtubeGrowthAccount").value,
  });
  if (params.get("range") === "custom") {
    params.set("start_date", document.querySelector("#youtubeGrowthStart").value);
    params.set("end_date", document.querySelector("#youtubeGrowthEnd").value);
  }
  return params;
}

function renderYouTubeGrowthAccounts(accounts) {
  const select = document.querySelector("#youtubeGrowthAccount");
  const selected = select.value;
  select.innerHTML = `<option value="">全部账号</option>${accounts.map((item) => `
    <option value="${escapeHtml(item.account_id)}">${escapeHtml(item.current_channel_title || item.account_id)} · ${escapeHtml(item.account_id)}${item.status === "ANALYTICS_SCOPE_MISSING" ? " · 需补分析授权" : item.status === "NEEDS_REAUTH" ? " · 需重新授权" : ""}</option>
  `).join("")}`;
  if ([...select.options].some((option) => option.value === selected)) select.value = selected;
}

function renderYouTubeGrowthSummary(summary) {
  const cards = [
    ["视频数量", ptBRNumber(summary.video_count)],
    ["YouTube账号", ptBRNumber(summary.account_count)],
    ["观看次数", ptBRNumber(summary.view_count)],
    ["评论数", ptBRNumber(summary.comment_count)],
    ["点赞量", ptBRNumber(summary.like_count)],
    ["分享数量", ptBRNumber(summary.share_count)],
    ["加权平均观看", youtubeDuration(summary.average_view_duration)],
    ["加权完播率", youtubePercentage(summary.completion_rate, true)],
    ["最后成功更新", summary.last_successful_update ? saoPauloDateTime(summary.last_successful_update) : "暂无数据"],
    ["延迟或缺失视频", ptBRNumber(summary.missing_video_count)],
  ];
  document.querySelector("#youtubeGrowthSummary").innerHTML = cards.map(([label, value]) => `
    <article class="youtube-growth-kpi"><span>${label}</span><strong>${value}</strong></article>
  `).join("");
  document.querySelector("#youtubeGrowthFreshness").textContent = summary.last_successful_update
    ? `最后成功更新 ${saoPauloDateTime(summary.last_successful_update)}`
    : "当前筛选范围尚无成功快照";
}

function renderYouTubeRanking(payload) {
  const body = document.querySelector("#youtubeRankingBody");
  body.innerHTML = payload.items.map((item) => {
    const thumbnail = item.thumbnail_url
      ? `<img class="youtube-video-thumbnail" src="${escapeHtml(item.thumbnail_url)}" alt="" loading="lazy" referrerpolicy="no-referrer">`
      : `<div class="youtube-video-thumbnail placeholder">YT</div>`;
    const title = escapeHtml(item.title || item.youtube_video_id || "未命名视频");
    const link = item.public_url
      ? `<a href="${escapeHtml(item.public_url)}" target="_blank" rel="noopener noreferrer">${title}</a>`
      : title;
    const error = item.last_error_summary
      ? `<small class="youtube-sync-error" title="${escapeHtml(item.last_error_summary)}">${escapeHtml(item.last_error_category || item.last_error_summary)}</small>`
      : "";
    return `<tr>
      <td><div class="youtube-video-cell">${thumbnail}<div><strong>${link}</strong><small>${escapeHtml(item.youtube_video_id || "")}</small></div></div></td>
      <td><strong>${escapeHtml(item.current_channel_title || "未知")}</strong><small>发布时：${escapeHtml(item.published_channel_title || "未知")}</small></td>
      <td>${escapeHtml(item.channel_id || "未知")}</td>
      <td>${saoPauloDateTime(item.published_local_at)}</td>
      <td>${escapeHtml(item.source_platform || "未知")}</td>
      <td>${escapeHtml(!item.source_category || item.source_category === "unknown" ? "未知" : item.source_category)}</td>
      <td>${escapeHtml(!item.source_keyword || item.source_keyword === "unknown" ? "未知" : item.source_keyword)}</td>
      <td>${ptBRNumber(item.view_count)}</td>
      <td>${ptBRNumber(item.comment_count)}</td>
      <td>${ptBRNumber(item.like_count)}</td>
      <td title="原始秒数：${item.average_view_duration ?? "暂无数据"}">${youtubeDuration(item.average_view_duration)}</td>
      <td title="末段桶 ${item.completion_bucket_ratio ?? "暂不可用"} · 原始比例 ${item.completion_raw_ratio ?? "暂不可用"}">${youtubePercentage(item.completion_rate, true)}</td>
      <td>${ptBRNumber(item.share_count)}</td>
      <td><span>最后请求：${saoPauloDateTime(item.last_attempted_at)}</span><small>Analytics 截至：${escapeHtml(item.data_through_date || "暂无数据")} · 下次：${saoPauloDateTime(item.next_sync_at)}</small></td>
      <td><span class="status-pill ${statusClass(item.sync_status)}">${escapeHtml(youtubeSyncStatus(item.sync_status))}</span>${error}</td>
    </tr>`;
  }).join("");
  state.youtubeGrowth.pages = payload.pages;
  state.youtubeGrowth.total = payload.total;
  document.querySelector("#youtubeGrowthPageStatus").textContent = payload.total
    ? `第 ${payload.page} / ${payload.pages} 页 · ${ptBRNumber(payload.total)} 个视频`
    : "";
  document.querySelector("#youtubeGrowthPrevious").disabled = payload.page <= 1;
  document.querySelector("#youtubeGrowthNext").disabled = payload.page >= payload.pages;
  document.querySelector("#youtubeGrowthPagination").hidden = payload.pages <= 1;
}

function setYouTubeGrowthState(name, message = "") {
  document.querySelector("#youtubeGrowthLoading").hidden = name !== "loading";
  document.querySelector("#youtubeGrowthError").hidden = name !== "error";
  document.querySelector("#youtubeGrowthEmpty").hidden = name !== "empty";
  document.querySelector("#youtubeGrowthTable").hidden = name !== "ready";
  if (message) document.querySelector("#youtubeGrowthError span").textContent = message;
}

async function loadYouTubeAnalytics({ resetPage = false } = {}) {
  if (resetPage) state.youtubeGrowth.page = 1;
  if (state.youtubeGrowth.abortController) state.youtubeGrowth.abortController.abort();
  const controller = new AbortController();
  const serial = ++state.youtubeGrowth.requestSerial;
  state.youtubeGrowth.abortController = controller;
  setYouTubeGrowthState("loading");
  try {
    const query = youtubeGrowthQuery();
    const rankingQuery = new URLSearchParams(query);
    rankingQuery.set("metric", document.querySelector("#youtubeGrowthMetric").value);
    rankingQuery.set("page", String(state.youtubeGrowth.page));
    rankingQuery.set("page_size", String(state.youtubeGrowth.pageSize));
    const [accounts, summary, ranking] = await Promise.all([
      api("/api/youtube-analytics/accounts", { signal: controller.signal }),
      api(`/api/youtube-analytics/summary?${query}`, { signal: controller.signal }),
      api(`/api/youtube-analytics/ranking?${rankingQuery}`, { signal: controller.signal }),
    ]);
    if (serial !== state.youtubeGrowth.requestSerial) return;
    renderYouTubeGrowthAccounts(accounts);
    renderYouTubeGrowthSummary(summary);
    renderYouTubeRanking(ranking);
    setYouTubeGrowthState(ranking.items.length ? "ready" : "empty");
    state.youtubeGrowth.loaded = true;
  } catch (error) {
    if (error.name === "AbortError" || serial !== state.youtubeGrowth.requestSerial) return;
    setYouTubeGrowthState("error", `数据加载失败：${error.message}`);
  }
}

function updateYouTubeCustomDates() {
  const custom = document.querySelector("#youtubeGrowthRange").value === "custom";
  document.querySelectorAll(".youtube-custom-date").forEach((label) => { label.hidden = !custom; });
  document.querySelector("#youtubeGrowthStart").required = custom;
  document.querySelector("#youtubeGrowthEnd").required = custom;
  if (custom && !document.querySelector("#youtubeGrowthEnd").value) {
    const formatter = new Intl.DateTimeFormat("sv-SE", {
      timeZone: "America/Sao_Paulo", year: "numeric", month: "2-digit", day: "2-digit",
    });
    const end = formatter.format(new Date());
    const startDate = new Date(`${end}T12:00:00-03:00`);
    startDate.setDate(startDate.getDate() - 29);
    document.querySelector("#youtubeGrowthStart").value = formatter.format(startDate);
    document.querySelector("#youtubeGrowthEnd").value = end;
  }
}

function renderDownloadClaims() {
  const table = document.querySelector("#downloadClaimTable");
  if (!table) return;
  table.innerHTML = state.downloadClaims.length ? state.downloadClaims.map((item) => `
    <tr>
      <td><strong>${escapeHtml(item.publisher)}</strong><small>${escapeHtml(item.publish_platform || "未指定平台")}</small></td>
      <td title="${escapeHtml(item.title || item.candidate_id)}">${escapeHtml((item.title || item.candidate_id).slice(0, 48))}<small>${escapeHtml(item.variant || "")} · ${escapeHtml(item.filename || "")}</small></td>
      <td>${dateText(item.downloaded_at)}</td>
      <td>${number(item.views)}</td>
      <td>${number(item.clicks)}</td>
      <td>${number(item.registrations)}</td>
      <td><button class="table-action" data-claim-metrics="${item.id}" type="button">回传数据</button></td>
    </tr>
  `).join("") : `<tr><td colspan="7"><div class="empty-state">还没有下载记录。审核通过的视频通过“发布”向导选择未配置自动发布的平台后会出现在这里。</div></td></tr>`;
  table.querySelectorAll("[data-claim-metrics]").forEach((button) => button.addEventListener("click", () => openClaimMetricsDialog(button.dataset.claimMetrics)));
}

function saoPauloNowForInput() {
  const parts = new Intl.DateTimeFormat("sv-SE", {
    timeZone: "America/Sao_Paulo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(new Date()).reduce((acc, part) => ({ ...acc, [part.type]: part.value }), {});
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

function tagsFromInput(value) {
  return String(value || "").split(/[,，#\n]+/).map((item) => item.trim()).filter(Boolean);
}

function selectedPublishCapability() {
  return state.publishCapabilities[document.querySelector("#publishPlatform").value] || {};
}

function renderPublishPreview() {
  const capability = selectedPublishCapability();
  const accountSelect = document.querySelector("#publishAccount");
  const accountLabel = accountSelect.selectedOptions[0]?.textContent || "未选择账号";
  const mode = document.querySelector("#publishScheduleMode").value;
  const localTime = mode === "scheduled" ? document.querySelector("#publishScheduledLocal").value : "立即发布";
  const operation = capability.operation_type === "PUBLICATION" ? "公开发布" : "仅下载到本地";
  document.querySelector("#publishPreview").innerHTML = `
    <div><strong>发布平台</strong><span>${escapeHtml(capability.label || document.querySelector("#publishPlatform").value)}</span></div>
    <div><strong>平台授权账号</strong><span>${escapeHtml(accountLabel)}</span></div>
    <div><strong>发布时间</strong><span>${escapeHtml(localTime)} · America/Sao_Paulo</span></div>
    <div><strong>公开范围</strong><span>public</span></div>
    <div><strong>当前操作</strong><span>${escapeHtml(operation)}</span></div>
    <div><strong>标题</strong><span>${escapeHtml(document.querySelector("#publishTitle").value)}</span></div>
    <div><strong>标签</strong><span>${escapeHtml(tagsFromInput(document.querySelector("#publishTags").value).join(", "))}</span></div>
  `;
}

async function loadPublishAccounts(platform) {
  const accounts = await api(`/api/publish/accounts?platform=${encodeURIComponent(platform)}`);
  state.publishAccounts = accounts;
  const capability = state.publishCapabilities[platform] || {};
  const usable = accounts.filter((item) => item.status === "AVAILABLE");
  const select = document.querySelector("#publishAccount");
  if (!accounts.length && capability.operation_type !== "PUBLICATION") {
    select.innerHTML = `<option value="">无需平台账号（仅下载）</option>`;
    select.disabled = true;
  } else {
    select.innerHTML = accounts.length
      ? accounts.map((item) => `<option value="${escapeHtml(item.id)}" ${item.status === "AVAILABLE" ? "" : "disabled"}>${escapeHtml(item.username || item.id)} · ${escapeHtml(item.status === "AVAILABLE" ? "可用" : item.status_reason || "不可用")}</option>`).join("")
      : `<option value="">没有可用账号</option>`;
    select.disabled = !accounts.length;
  }
  if (capability.requires_account && !usable.length) {
    document.querySelector("#publishCapabilityNotice").textContent = "YouTube 必须选择一个可用授权账号；当前没有可用账号，不能创建真实发布任务。";
  } else {
    document.querySelector("#publishCapabilityNotice").textContent = capability.notice || "";
  }
  renderPublishPreview();
}

async function generatePublishCopy() {
  const asset = state.pendingPublishAsset;
  if (!asset?.id) return toast("请先选择成片", "error");
  const button = document.querySelector("#regeneratePublishCopy");
  button.disabled = true;
  try {
    const result = await api("/api/publish/copy", {
      method: "POST",
      body: JSON.stringify({
        candidate_id: String(asset.id).split(":", 1)[0],
        asset_id: asset.id,
        filename: asset.filename,
        variant: asset.variant || "",
        platform: document.querySelector("#publishPlatform").value,
      }),
    });
    document.querySelector("#publishTitle").value = result.title || "";
    document.querySelector("#publishDescription").value = result.description || "";
    document.querySelector("#publishTags").value = (result.tags || []).join(", ");
    renderPublishPreview();
  } catch (error) {
    toast(`AI 文案生成失败：${error.message}`, "error");
  } finally {
    button.disabled = false;
  }
}

async function openPublishDialog(rawAsset, candidateId = "") {
  try {
    state.pendingPublishAsset = JSON.parse(rawAsset || "{}");
  } catch {
    state.pendingPublishAsset = null;
  }
  state.pendingPublishItem = state.candidates.find((item) => item.id === candidateId) || null;
  if (state.pendingPublishItem?.status !== "APPROVED") return toast("只有审核通过的视频才能发布", "error");
  if (!state.pendingPublishAsset?.download_url) return toast("这个成片没有可下载链接", "error");
  const asset = state.pendingPublishAsset;
  document.querySelector("#publishAssetSummary").textContent = `${state.pendingPublishItem.display_title || state.pendingPublishItem.title || candidateId} · ${asset.label || asset.filename || "成片"} · ${asset.variant || "版本"}`;
  document.querySelector("#publishPlatform").value = "youtube";
  document.querySelector("#publishScheduleMode").value = "now";
  document.querySelector("#publishScheduledLocal").value = saoPauloNowForInput();
  document.querySelector("#publishScheduleField").hidden = true;
  document.querySelector("#publishTitle").value = "";
  document.querySelector("#publishDescription").value = "";
  document.querySelector("#publishTags").value = "";
  document.querySelector("#publishDialog").showModal();
  await loadPublishAccounts("youtube");
  await generatePublishCopy();
}

function openClaimMetricsDialog(claimId) {
  const claim = state.downloadClaims.find((item) => String(item.id) === String(claimId));
  if (!claim) return toast("找不到下载记录", "error");
  document.querySelector("#claimMetricsId").value = claim.id;
  document.querySelector("#claimMetricsLabel").textContent = `${claim.publisher} · ${claim.publish_platform || "未指定平台"}`;
  document.querySelector("#claimMetricsViews").value = claim.views || "";
  document.querySelector("#claimMetricsClicks").value = claim.clicks || "";
  document.querySelector("#claimMetricsRegistrations").value = claim.registrations || "";
  document.querySelector("#claimMetricsDialog").showModal();
}

function renderHotKeywords() {
  const element = document.querySelector("#hotKeywordStrip");
  if (!element) return;
  element.innerHTML = state.hotKeywords.length ? state.hotKeywords.map((item) => `
    <button class="hot-keyword" data-hot-keyword="${escapeHtml(item.keyword)}" type="button">
      <strong>${escapeHtml(item.keyword)}</strong><small>${escapeHtml(item.source || "google_trends")}</small>
    </button>
  `).join("") : `<div class="empty-state">今日热词还未同步；调度器会保留最近一次成功结果</div>`;
  element.querySelectorAll("[data-hot-keyword]").forEach((button) => {
    button.addEventListener("click", () => discoverWithHotKeyword(button.dataset.hotKeyword));
  });
}

function renderCategoryKeywords() {
  const table = document.querySelector("#categoryKeywordTable");
  if (!table) return;
  const payload = state.categoryKeywords || {};
  const dateElement = document.querySelector("#categoryKeywordDate");
  if (dateElement) dateElement.textContent = payload.date ? `日期 ${payload.date}` : "";
  const rows = payload.rows || [];
  table.innerHTML = rows.length ? rows.map((row) => {
    const keywords = row.keywords || [];
    const keywordCells = keywords.length ? keywords.map((item) => `
      <button class="category-keyword-chip" data-hot-keyword="${escapeHtml(item.keyword)}" title="${escapeHtml(item.source || "")}" type="button">
        <strong>${escapeHtml(item.keyword)}</strong><small>${escapeHtml(item.source || "")}</small>
      </button>
    `).join("") : `<span class="muted">今日暂无</span>`;
    return `<tr>
      <td><strong>${escapeHtml(row.label)}</strong></td>
      <td><div class="category-keyword-chips">${keywordCells}</div></td>
      <td>${number(row.count || 0)}</td>
    </tr>`;
  }).join("") : `<tr><td colspan="3"><div class="empty-state">今日分类关键词还未同步</div></td></tr>`;
  table.querySelectorAll("[data-hot-keyword]").forEach((button) => {
    button.addEventListener("click", () => discoverWithHotKeyword(button.dataset.hotKeyword));
  });
}

async function discoverWithHotKeyword(keyword) {
  const value = String(keyword || "").trim();
  if (!value) return;
  try {
    const result = await api("/api/actions", {
      method: "POST",
      body: JSON.stringify({ action: "discover", platform: "youtube", limit: 3, keywords: [value] }),
    });
    toast(`热词发现任务 ${result.task_id} 已启动`);
    pollTask(result.task_id);
  } catch (error) {
    toast(`热词发现失败：${error.message}`, "error");
  }
}

async function runTrendsNow() {
  try {
    const result = await api("/api/trends/run", { method: "POST", body: JSON.stringify({}) });
    toast(`Google Trends 已同步 ${result.count || 0} 个热词`);
    await refreshAll();
  } catch (error) {
    toast(`同步失败：${error.message}`, "error");
  }
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
  document.querySelector("#settingRemotionContentBgm").value = remotion.content_bgm_volume ?? 0;
  document.querySelector("#settingRemotionEndcardBgm").value = remotion.endcard_bgm_volume ?? 0;
  document.querySelector("#settingRemotionAddBgm").checked = remotion.add_bgm_under_source === true;
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
  table.innerHTML = state.uploads.length ? state.uploads.map((item) => `
    <tr>
      <td><strong>${escapeHtml(item.original_filename || item.id)}</strong><small>${escapeHtml(item.id)}</small></td>
      <td><span class="status-pill ${item.kind === "source" ? "ready" : ""}">${item.kind === "source" ? "源视频" : item.kind === "design_image" ? "设计图片" : "Reaction"}</span></td>
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
  const init = await api("/api/uploads/init", {
    method: "POST",
    body: JSON.stringify({ filename: file.name, kind, size: file.size }),
  });
  const chunkSize = Number(init.chunk_bytes);
  const total = Number(init.chunk_count);
  for (let index = 0; index < total; index += 1) {
    const start = index * chunkSize;
    const end = Math.min(file.size, start + chunkSize);
    const response = await fetch(`/api/uploads/chunk?upload_id=${encodeURIComponent(init.id)}&index=${index}`, {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: file.slice(start, end),
    });
    await parseUploadResponse(response);
    onProgress(Math.round(((index + 1) / total) * 94), `正在上传 ${index + 1}/${total}`);
  }
  onProgress(97, "服务器正在合并文件");
  const completed = await api("/api/uploads/complete", {
    method: "POST",
    body: JSON.stringify({ upload_id: init.id }),
  });
  onProgress(100, kind === "source" ? "上传完成，已进入待制作库存" : kind === "design_image" ? "图片上传完成" : "Reaction 上传完成");
  return completed;
}

async function downloadUploadAsset(uploadId) {
  try {
    const payload = await api(`/api/uploads/${encodeURIComponent(uploadId)}/link`);
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
  if (!views[name]) return;
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === name));
  document.querySelectorAll(".view").forEach((item) => item.classList.toggle("active", item.id === `view-${name}`));
  document.querySelector("#viewEyebrow").textContent = views[name][0];
  document.querySelector("#viewTitle").textContent = views[name][1];
  document.querySelector(".top-actions .search").hidden = name === "posters";
  document.querySelector("#discoverButton").hidden = name === "posters";
  if (name === "posters") loadPosters();
  if (name === "analytics" && !state.youtubeGrowth.loaded) loadYouTubeAnalytics();
}

function openProductionDialog(candidateIds) {
  const ids = Array.isArray(candidateIds) ? candidateIds : [candidateIds];
  state.pendingProductionIds = [...new Set(ids.filter(Boolean))];
  document.querySelector("#productionDialog").showModal();
}

function parseDesignAssets(raw) {
  try {
    const value = JSON.parse(raw || "[]");
    return Array.isArray(value) ? value.filter((asset) => asset && asset.id) : [];
  } catch {
    return [];
  }
}

function designAssetMap(assets) {
  const generic = assets.find((asset) => asset.variant === "通用版") || assets[0] || null;
  const fb = assets.find((asset) => asset.variant === "FB版") || generic;
  const selected = assets[0] || null;
  const selectedVariant = selected?.variant || generic?.variant || "通用版";
  return {
    generic,
    fb,
    selected,
    selectedVariant,
    ids: { [selectedVariant]: selected?.id || generic?.id || "" },
  };
}

async function openDesignDialog(candidateId, encodedAssets = "") {
  const item = state.candidates.find((candidate) => candidate.id === candidateId);
  if (!item) return toast("找不到这条内容", "error");
  const productionCandidateId = item.source_candidate_id || candidateId;
  const assetMap = designAssetMap(parseDesignAssets(encodedAssets));
  const baseAssetId = assetMap.selected?.id || assetMap.generic?.id || assetMap.fb?.id || "";
  if (!baseAssetId) return toast("请先生成服务器成片，再打开文案设计", "error");
  let designInfo = {};
  try {
    designInfo = await api(`/api/candidates/${encodeURIComponent(`${productionCandidateId}::asset::${baseAssetId}`)}/design`);
  } catch (error) {
    toast(`服务器成片画布信息读取失败：${error.message}`, "error");
  }
  Object.assign(item, designInfo);
  item.design_base_asset_ids = assetMap.ids;
  item.design_variants = [assetMap.selectedVariant];
  item.design_base_label = `${assetMap.selected?.label || item.display_title || item.title || candidateId}`;
  state.pendingProductionIds = [item.source_candidate_id || productionCandidateId];
  state.designCandidate = item;
  clearDesignImages();
  state.designLayers = [{
    id: "design-text",
    type: "text",
    text: "",
    color: "#ffffff",
    fontSizeRatio: 64 / Math.max(1, Number(item.design_canvas_height || 1920)),
    maxWidth: 0.84,
    fontWeight: 800,
    x: 0.08,
    y: 0.10,
  }];
  state.selectedDesignLayerId = "design-text";
  document.querySelector("#designCandidateLabel").textContent = `${item.display_title || item.title || candidateId} · ${assetMap.selectedVariant}`;
  document.querySelector("#designText").value = "";
  document.querySelector("#designTextColor").value = "#ffffff";
  document.querySelector("#designTextSize").value = 64;
  document.querySelector("#designTextWidth").value = 84;
  document.querySelector("#designUploadProgress").hidden = true;
  setDesignSource(item);
  renderDesignEditor();
  const dialog = document.querySelector("#designDialog");
  dialog.showModal();
  requestAnimationFrame(() => {
    sizeDesignCanvas();
    renderDesignLayers();
    document.querySelector("#designText").focus();
  });
}

const clamp = (value, minimum, maximum) => Math.max(minimum, Math.min(maximum, Number(value) || 0));

function designTextLayer() {
  return state.designLayers.find((layer) => layer.id === "design-text");
}

function selectedDesignImage() {
  return state.designLayers.find((layer) => layer.id === state.selectedDesignLayerId && layer.type === "image");
}

function clearDesignImages() {
  state.designLayers.filter((layer) => layer.type === "image" && layer.previewUrl).forEach((layer) => URL.revokeObjectURL(layer.previewUrl));
}

function setDesignSource(item) {
  const video = document.querySelector("#designSourceVideo");
  const image = document.querySelector("#designSourceImage");
  const empty = document.querySelector("#designCanvasEmpty");
  video.pause();
  video.removeAttribute("src");
  video.load();
  video.style.display = "none";
  image.style.display = "none";
  empty.hidden = false;
  const previewUrl = String(item.source_preview_url || "");
  if (previewUrl) {
    video.src = previewUrl;
    video.style.objectFit = item.source_fit || "contain";
    video.style.display = "block";
    empty.hidden = true;
    video.play().catch(() => {});
    return;
  }
  const fallback = String(item.cover_url || item.thumbnail_url || "");
  if (fallback) {
    image.src = fallback;
    image.style.objectFit = item.source_fit || "contain";
    image.style.display = "block";
    empty.hidden = true;
  }
}

function sizeDesignCanvas() {
  const item = state.designCandidate || {};
  const width = Math.max(1, Number(item.design_canvas_width || 1080));
  const height = Math.max(1, Number(item.design_canvas_height || 1920));
  const shell = document.querySelector(".design-canvas-shell");
  const canvas = document.querySelector("#designCanvas");
  if (!shell || !canvas) return;
  const scale = Math.min(shell.clientWidth / width, shell.clientHeight / height);
  canvas.style.width = `${Math.max(160, Math.floor(width * scale))}px`;
  canvas.style.height = `${Math.max(220, Math.floor(height * scale))}px`;
  document.querySelector("#designCanvasSize").textContent = `${width} × ${height}`;
}

function renderDesignEditor() {
  renderDesignLayers();
  renderDesignImageList();
  syncDesignControls();
}

function renderDesignLayers() {
  const stage = document.querySelector("#designLayerStage");
  const canvas = document.querySelector("#designCanvas");
  if (!stage || !canvas) return;
  const canvasHeight = Math.max(1, canvas.clientHeight);
  stage.innerHTML = state.designLayers.map((layer) => {
    const selected = layer.id === state.selectedDesignLayerId ? " selected" : "";
    if (layer.type === "text") {
      if (!layer.text) return "";
      return `<div class="design-layer design-text-layer${selected}" data-design-layer-id="${escapeHtml(layer.id)}" style="left:${layer.x * 100}%;top:${layer.y * 100}%;max-width:${layer.maxWidth * 100}%;font-size:${Math.max(8, layer.fontSizeRatio * canvasHeight)}px;color:${escapeHtml(layer.color)}"><span>${escapeHtml(layer.text)}</span><i class="design-resize-handle" data-design-resize-id="${escapeHtml(layer.id)}" title="拖动调整大小"></i></div>`;
    }
    return `<div class="design-layer design-image-layer${selected}" data-design-layer-id="${escapeHtml(layer.id)}" style="left:${layer.x * 100}%;top:${layer.y * 100}%;width:${layer.width * 100}%"><img src="${escapeHtml(layer.previewUrl)}" alt="${escapeHtml(layer.name)}"><i class="design-resize-handle" data-design-resize-id="${escapeHtml(layer.id)}" title="拖动调整大小"></i></div>`;
  }).join("");
  stage.querySelectorAll("[data-design-layer-id]").forEach((element) => {
    element.addEventListener("pointerdown", (event) => startDesignDrag(event, element));
  });
  stage.querySelectorAll("[data-design-resize-id]").forEach((handle) => {
    handle.addEventListener("pointerdown", (event) => startDesignResize(event, handle));
  });
}

function startDesignDrag(event, element) {
  if (event.button !== undefined && event.button !== 0) return;
  const id = element.dataset.designLayerId;
  state.selectedDesignLayerId = id;
  document.querySelectorAll("[data-design-layer-id]").forEach((entry) => entry.classList.toggle("selected", entry === element));
  renderDesignImageList();
  syncDesignControls();
  const layer = state.designLayers.find((entry) => entry.id === id);
  const canvas = document.querySelector("#designCanvas");
  if (!layer || !canvas) return;
  event.preventDefault();
  const startX = event.clientX;
  const startY = event.clientY;
  const originX = layer.x;
  const originY = layer.y;
  const move = (moveEvent) => {
    const bounds = canvas.getBoundingClientRect();
    const layerBounds = element.getBoundingClientRect();
    const maxX = Math.max(0, 1 - layerBounds.width / Math.max(1, bounds.width));
    const maxY = Math.max(0, 1 - layerBounds.height / Math.max(1, bounds.height));
    layer.x = clamp(originX + (moveEvent.clientX - startX) / bounds.width, 0, maxX);
    layer.y = clamp(originY + (moveEvent.clientY - startY) / bounds.height, 0, maxY);
    element.style.left = `${layer.x * 100}%`;
    element.style.top = `${layer.y * 100}%`;
    syncDesignPositionControls(layer);
  };
  const stop = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", stop);
    window.removeEventListener("pointercancel", stop);
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", stop);
  window.addEventListener("pointercancel", stop);
}

function startDesignResize(event, handle) {
  if (event.button !== undefined && event.button !== 0) return;
  const id = handle.dataset.designResizeId;
  const layer = state.designLayers.find((entry) => entry.id === id);
  const element = handle.closest("[data-design-layer-id]");
  const canvas = document.querySelector("#designCanvas");
  if (!layer || !element || !canvas) return;
  event.preventDefault();
  event.stopPropagation();
  state.selectedDesignLayerId = id;
  document.querySelectorAll("[data-design-layer-id]").forEach((entry) => entry.classList.toggle("selected", entry === element));
  renderDesignImageList();
  syncDesignControls();
  const startX = event.clientX;
  const startY = event.clientY;
  const bounds = canvas.getBoundingClientRect();
  const originWidth = layer.type === "text" ? Number(layer.maxWidth || 0.84) : Number(layer.width || 0.2);
  const originFont = Number(layer.fontSizeRatio || 0.033);
  const move = (moveEvent) => {
    const deltaRatio = (moveEvent.clientX - startX) / Math.max(1, bounds.width);
    if (layer.type === "text") {
      const verticalRatio = (moveEvent.clientY - startY) / Math.max(1, bounds.height);
      layer.maxWidth = clamp(originWidth + deltaRatio, 0.1, 1);
      layer.fontSizeRatio = clamp(originFont + verticalRatio, 0.01, 0.25);
      element.style.maxWidth = `${layer.maxWidth * 100}%`;
      element.style.fontSize = `${Math.max(8, layer.fontSizeRatio * canvas.clientHeight)}px`;
    } else {
      layer.width = clamp(originWidth + deltaRatio, 0.03, 1);
      element.style.width = `${layer.width * 100}%`;
    }
    syncDesignControls();
  };
  const stop = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", stop);
    window.removeEventListener("pointercancel", stop);
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", stop);
  window.addEventListener("pointercancel", stop);
}

function selectDesignLayer(id) {
  state.selectedDesignLayerId = id;
  renderDesignLayers();
  renderDesignImageList();
  syncDesignControls();
}

function syncDesignPositionControls(layer) {
  void layer;
}

function syncDesignControls() {
  const textLayer = designTextLayer();
  const canvasHeight = Math.max(1, Number(state.designCandidate?.design_canvas_height || 1920));
  const textPixels = Math.round(textLayer.fontSizeRatio * canvasHeight);
  document.querySelector("#designTextSize").value = clamp(textPixels, 16, 180);
  document.querySelector("#designTextSizeValue").textContent = `${textPixels} px`;
  document.querySelector("#designTextWidth").value = Math.round(textLayer.maxWidth * 100);
  document.querySelector("#designTextWidthValue").textContent = `${Math.round(textLayer.maxWidth * 100)}%`;
  syncDesignPositionControls(textLayer);
  const image = selectedDesignImage();
  document.querySelector("#designImageControls").hidden = !image;
  document.querySelector("#designSelectionLabel").textContent = image ? image.name : "文案";
  if (!image) return;
  document.querySelector("#designImageSize").value = Math.round(image.width * 100);
  document.querySelector("#designImageSizeValue").textContent = `${Math.round(image.width * 100)}%`;
  syncDesignPositionControls(image);
}

function renderDesignImageList() {
  const list = document.querySelector("#designImageList");
  const images = state.designLayers.filter((layer) => layer.type === "image");
  list.innerHTML = images.length ? images.map((layer) => `<div class="design-image-item${layer.id === state.selectedDesignLayerId ? " active" : ""}" data-select-design-image="${escapeHtml(layer.id)}"><img src="${escapeHtml(layer.previewUrl)}" alt=""><span>${escapeHtml(layer.name)}</span><button class="design-image-remove" data-remove-design-image="${escapeHtml(layer.id)}" type="button" title="删除图片" aria-label="删除图片">×</button></div>`).join("") : `<div class="design-empty-list">尚未添加图片</div>`;
  list.querySelectorAll("[data-select-design-image]").forEach((item) => item.addEventListener("click", () => selectDesignLayer(item.dataset.selectDesignImage)));
  list.querySelectorAll("[data-remove-design-image]").forEach((button) => button.addEventListener("click", (event) => {
    event.stopPropagation();
    removeDesignImage(button.dataset.removeDesignImage);
  }));
}

function addDesignImages(files) {
  Array.from(files || []).forEach((file, index) => {
    const id = `design-image-${Date.now()}-${index}`;
    state.designLayers.push({
      id,
      type: "image",
      name: file.name,
      file,
      previewUrl: URL.createObjectURL(file),
      serverPath: "",
      width: 0.20,
      x: clamp(0.05 + index * 0.03, 0, 0.75),
      y: clamp(0.05 + index * 0.03, 0, 0.75),
    });
    state.selectedDesignLayerId = id;
  });
  renderDesignEditor();
}

function removeDesignImage(id) {
  const layer = state.designLayers.find((entry) => entry.id === id);
  if (layer?.previewUrl) URL.revokeObjectURL(layer.previewUrl);
  state.designLayers = state.designLayers.filter((entry) => entry.id !== id);
  state.selectedDesignLayerId = "design-text";
  renderDesignEditor();
}

function setDesignUploadProgress(percent, message) {
  const panel = document.querySelector("#designUploadProgress");
  panel.hidden = false;
  document.querySelector("#designUploadMessage").textContent = message;
  document.querySelector("#designUploadPercent").textContent = `${Math.round(percent)}%`;
  document.querySelector("#designUploadBar").style.width = `${clamp(percent, 0, 100)}%`;
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

function setDiscoverUploadProgress(percent, message) {
  const panel = document.querySelector("#discoverUploadProgress");
  if (!panel) return;
  panel.hidden = false;
  document.querySelector("#discoverUploadMessage").textContent = message;
  document.querySelector("#discoverUploadPercent").textContent = `${percent}%`;
  document.querySelector("#discoverUploadBar").style.width = `${Math.max(0, Math.min(100, percent))}%`;
}

document.querySelector("#assetUploadForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = document.querySelector("#assetUploadButton");
  const file = document.querySelector("#assetUploadFile").files[0];
  const kind = document.querySelector("#assetUploadKind").value;
  if (!file) return toast("请选择要上传的视频", "error");
  button.disabled = true;
  try {
    const uploaded = await uploadServerFile(file, kind, "", setUploadProgress);
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

document.querySelectorAll(".nav-item").forEach((button) => button.addEventListener("click", () => openView(button.dataset.view)));
document.querySelectorAll("[data-open-view]").forEach((button) => button.addEventListener("click", () => openView(button.dataset.openView)));
document.querySelector("#refreshButton").addEventListener("click", () => refreshAll(true));
document.querySelector("#discoverButton").addEventListener("click", () => {
  document.querySelector("#discoverUploadProgress").hidden = true;
  updateDiscoverMode();
  document.querySelector("#discoverDialog").showModal();
});
document.querySelector("#closeDiscoverDialog").addEventListener("click", () => document.querySelector("#discoverDialog").close());
document.querySelector("#cancelDiscoverDialog").addEventListener("click", () => document.querySelector("#discoverDialog").close());
document.querySelector("#closePublishDialog").addEventListener("click", () => document.querySelector("#publishDialog").close());
document.querySelector("#cancelPublishDialog").addEventListener("click", () => document.querySelector("#publishDialog").close());
document.querySelector("#closeClaimMetricsDialog").addEventListener("click", () => document.querySelector("#claimMetricsDialog").close());
document.querySelector("#cancelClaimMetricsDialog").addEventListener("click", () => document.querySelector("#claimMetricsDialog").close());
document.querySelector("#globalSearch").addEventListener("input", (event) => { state.search = event.target.value; renderInventory(); });
document.querySelector("#selectVisible").addEventListener("change", (event) => {
  filteredCandidates().forEach((item) => event.target.checked ? state.selectedCandidates.add(item.id) : state.selectedCandidates.delete(item.id));
  renderInventory();
});
document.querySelector("#batchDownload").addEventListener("click", () => runBatchAction("download", ["DISCOVERED", "DOWNLOAD_FAILED"]));
document.querySelector("#batchProduce").addEventListener("click", () => {
  const ids = selectedRows().filter((item) => ["DISCOVERED", "DOWNLOAD_FAILED", "DOWNLOADED", "PRODUCTION_FAILED", "REVISION_REQUIRED", "BLOCKED_RIGHTS"].includes(item.status) || (item.status === "APPROVED" && !outputAssetsFor(item).length)).map((item) => item.id);
  if (!ids.length) return toast("所选内容中没有可制作项目", "error");
  openProductionDialog(ids);
});
document.querySelector("#batchDelete").addEventListener("click", () => deleteCandidates(selectedRows().map((item) => item.id)));
document.querySelector("#runTrendsNow").addEventListener("click", runTrendsNow);
document.querySelectorAll("#statusFilters button").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll("#statusFilters button").forEach((item) => item.classList.toggle("active", item === button));
  state.status = button.dataset.status;
  renderInventory();
}));
document.querySelectorAll("#posterStatusFilters button").forEach((button) => button.addEventListener("click", () => {
  if (state.posterLoading || state.posterStatus === (button.dataset.posterStatus || "")) return;
  document.querySelectorAll("#posterStatusFilters button").forEach((item) => item.classList.toggle("active", item === button));
  state.posterStatus = button.dataset.posterStatus || "";
  state.posterPagination.page = 1;
  loadPosters();
}));
document.querySelector("#posterPreviousPage").addEventListener("click", () => {
  if (state.posterLoading || state.posterPagination.page <= 1) return;
  state.posterPagination.page -= 1;
  loadPosters();
});
document.querySelector("#posterNextPage").addEventListener("click", () => {
  if (state.posterLoading || state.posterPagination.page >= state.posterPagination.pages) return;
  state.posterPagination.page += 1;
  loadPosters();
});
document.querySelector("#closePosterPreview").addEventListener("click", () => document.querySelector("#posterPreviewDialog").close());
document.querySelector("#posterPreviewPrevious").addEventListener("click", () => showPosterPreview(state.posterPreviewIndex - 1));
document.querySelector("#posterPreviewNext").addEventListener("click", () => showPosterPreview(state.posterPreviewIndex + 1));
document.querySelector("#posterZoomOut").addEventListener("click", () => applyPosterZoom(state.posterZoom - 0.25));
document.querySelector("#posterZoomIn").addEventListener("click", () => applyPosterZoom(state.posterZoom + 0.25));
document.querySelector("#posterZoomReset").addEventListener("click", () => applyPosterZoom(1));
document.querySelector("#posterPreviewStage").addEventListener("wheel", (event) => {
  event.preventDefault();
  applyPosterZoom(state.posterZoom + (event.deltaY < 0 ? 0.15 : -0.15));
}, { passive: false });
document.querySelector("#closePosterDelete").addEventListener("click", () => document.querySelector("#posterDeleteDialog").close());
document.querySelector("#cancelPosterDelete").addEventListener("click", () => document.querySelector("#posterDeleteDialog").close());
document.querySelector("#posterDeleteForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const item = state.pendingPosterDelete;
  if (!item) return;
  const pendingKey = `delete:${item.id}`;
  if (state.posterPendingActions.has(pendingKey)) return;
  state.posterPendingActions.add(pendingKey);
  const button = document.querySelector("#confirmPosterDelete");
  button.disabled = true;
  button.textContent = "删除中";
  try {
    await api(`/api/posters/${encodeURIComponent(item.id)}/delete`, {
      method: "POST",
      headers: { "X-Request-ID": posterRequestId("delete", item.id) },
      body: JSON.stringify({ actor: localStorage.getItem("jaguartvOperatorName") || "dashboard" }),
    });
    document.querySelector("#posterDeleteDialog").close();
    state.pendingPosterDelete = null;
    toast(`已删除海报“${item.name}”`);
    const remainingOnPage = Math.max(0, state.posters.length - 1);
    if (!remainingOnPage && state.posterPagination.page > 1) state.posterPagination.page -= 1;
    await loadPosters();
  } catch (error) {
    toast(`删除失败：${error.message}`, "error");
  } finally {
    state.posterPendingActions.delete(pendingKey);
    button.disabled = false;
    button.textContent = "确认删除";
    if (!state.posterLoading) renderPosterInventory();
  }
});

document.querySelector("#publishPlatform").addEventListener("change", async (event) => {
  await loadPublishAccounts(event.target.value);
  await generatePublishCopy();
});
document.querySelector("#publishAccount").addEventListener("change", renderPublishPreview);
document.querySelector("#publishScheduleMode").addEventListener("change", (event) => {
  document.querySelector("#publishScheduleField").hidden = event.target.value !== "scheduled";
  renderPublishPreview();
});
["#publishScheduledLocal", "#publishTitle", "#publishDescription", "#publishTags"].forEach((selector) => {
  document.querySelector(selector).addEventListener("input", renderPublishPreview);
});
document.querySelector("#regeneratePublishCopy").addEventListener("click", generatePublishCopy);

document.querySelector("#publishForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const asset = state.pendingPublishAsset;
  if (!asset?.download_url) return toast("这个成片没有可下载链接", "error");
  const platform = document.querySelector("#publishPlatform").value;
  const capability = state.publishCapabilities[platform] || {};
  const account = document.querySelector("#publishAccount").value;
  if (capability.requires_account && !account) return toast("YouTube 必须选择可用授权账号", "error");
  const title = document.querySelector("#publishTitle").value.trim();
  const description = document.querySelector("#publishDescription").value.trim();
  const tags = tagsFromInput(document.querySelector("#publishTags").value);
  if (!title || !description || !tags.length) return toast("请先生成或填写标题、文案和标签", "error");
  try {
    const result = await api("/api/publications", { method: "POST", body: JSON.stringify({
      candidate_id: String(asset.id).split(":", 1)[0],
      asset_id: asset.id,
      filename: asset.filename,
      variant: asset.variant || "",
      platform,
      account,
      schedule_mode: document.querySelector("#publishScheduleMode").value,
      scheduled_local_at: document.querySelector("#publishScheduledLocal").value,
      title,
      description,
      tags,
    }) });
    document.querySelector("#publishDialog").close();
    if (result.operation_type === "LOCAL_DOWNLOAD") {
      toast("该平台暂未配置自动发布，本次仅下载到本地");
      const link = document.createElement("a");
      link.href = result.download_url || asset.download_url;
      link.download = asset.filename || "";
      link.target = "_blank";
      link.rel = "noopener";
      document.body.appendChild(link);
      link.click();
      link.remove();
    } else {
      toast(result.status === "SCHEDULED" ? "已创建预约发布任务" : "已创建 YouTube 发布任务");
    }
    await refreshAll();
  } catch (error) {
    toast(`发布失败：${error.message}`, "error");
  }
});

document.querySelector("#claimMetricsForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/api/download-claims/metrics", { method: "POST", body: JSON.stringify({
      claim_id: document.querySelector("#claimMetricsId").value,
      views: document.querySelector("#claimMetricsViews").value,
      clicks: document.querySelector("#claimMetricsClicks").value,
      registrations: document.querySelector("#claimMetricsRegistrations").value,
    }) });
    document.querySelector("#claimMetricsDialog").close();
    toast("发布表现已回传");
    await refreshAll();
  } catch (error) {
    toast(`回传失败：${error.message}`, "error");
  }
});

function updateDiscoverMode() {
  const mode = document.querySelector("#discoverMode").value;
  const platform = document.querySelector("#discoverPlatform").value;
  document.querySelector("#discoverPlatformField").hidden = mode === "upload";
  document.querySelector("#discoverUrlField").hidden = mode !== "url";
  document.querySelector("#discoverUploadFields").hidden = mode !== "upload";
  document.querySelector("#discoverUrl").required = mode === "url";
  document.querySelector("#discoverUploadFile").required = mode === "upload";
  const notes = {
    youtube: "粘贴 YouTube 视频 URL；系统会优先使用服务器保存的 YouTube 登录态解析。",
    bilibili: "粘贴 Bilibili 视频 URL；登录态可提高稳定性和画质。",
    douyin: "粘贴抖音作品 URL；服务器采集服务和登录态必须可用。",
    xiaohongshu: "小红书当前通过作品 URL 导入，需要先启动本机 5556 端口的 XHS 服务。",
    tiktok: "粘贴 TikTok 具体视频 URL；服务器 TikTok 登录态会用于解析和下载。",
    facebook: "请粘贴具体视频或 Reels URL；服务器登录态必须有权访问该视频。",
  };
  const modeNotes = {
    url: "粘贴单条视频 URL 后会在服务器解析并下载，成功后直接进入待制作；超过 30 分钟的素材只允许删除。",
    upload: "上传自有或已授权源视频后会直接进入待制作；后续制作规则与爬取视频完全一致。",
  };
  document.querySelector("#discoverPlatformNote").textContent = modeNotes[mode] || notes[platform];
  document.querySelector("#submitDiscovery").textContent = mode === "upload" ? "上传并入库" : "开始运行";
}

document.querySelector("#discoverMode").addEventListener("change", updateDiscoverMode);
document.querySelector("#discoverPlatform").addEventListener("change", updateDiscoverMode);
updateDiscoverMode();

document.querySelector("#closeProductionDialog").addEventListener("click", () => document.querySelector("#productionDialog").close());
document.querySelector("#cancelProductionDialog").addEventListener("click", () => document.querySelector("#productionDialog").close());
document.querySelector("#closeDesignDialog").addEventListener("click", () => document.querySelector("#designDialog").close());
document.querySelector("#cancelDesignDialog").addEventListener("click", () => document.querySelector("#designDialog").close());
document.querySelector("#designDialog").addEventListener("close", () => document.querySelector("#designSourceVideo").pause());
window.addEventListener("resize", () => {
  if (document.querySelector("#designDialog")?.open) {
    sizeDesignCanvas();
    renderDesignLayers();
  }
});

document.querySelector("#designText").addEventListener("input", (event) => {
  designTextLayer().text = event.target.value;
  renderDesignLayers();
});
document.querySelector("#designTextColor").addEventListener("input", (event) => {
  designTextLayer().color = event.target.value;
  renderDesignLayers();
});
document.querySelector("#designTextSize").addEventListener("input", (event) => {
  const pixels = Number(event.target.value);
  designTextLayer().fontSizeRatio = pixels / Math.max(1, Number(state.designCandidate?.design_canvas_height || 1920));
  document.querySelector("#designTextSizeValue").textContent = `${pixels} px`;
  renderDesignLayers();
});
document.querySelector("#designTextWidth").addEventListener("input", (event) => {
  const percentValue = Number(event.target.value);
  designTextLayer().maxWidth = percentValue / 100;
  document.querySelector("#designTextWidthValue").textContent = `${percentValue}%`;
  renderDesignLayers();
});
document.querySelector("#designImageFiles").addEventListener("change", (event) => {
  addDesignImages(event.target.files);
  event.target.value = "";
});
document.querySelector("#designImageSize").addEventListener("input", (event) => {
  const image = selectedDesignImage();
  if (!image) return;
  image.width = Number(event.target.value) / 100;
  document.querySelector("#designImageSizeValue").textContent = `${event.target.value}%`;
  renderDesignLayers();
});
document.querySelector("#productionForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const mode = document.querySelector("#productionReactionMode").value;
  const file = document.querySelector("#productionReactionFile").files[0];
  let reactionSource = document.querySelector("#productionReactionSource").value.trim();
  try {
    if (mode !== "none" && file) {
      const uploaded = await uploadReactionFile(file, "");
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
      batch_label: document.querySelector("#productionBatchLabel").value.trim(),
    };
    document.querySelector("#productionDialog").close();
    await runBatchAction("produce", [], options, state.pendingProductionIds);
  } catch (error) { toast(error.message, "error"); }
});

document.querySelector("#designForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = document.querySelector("#submitDesign");
  submit.disabled = true;
  try {
    const imageLayers = state.designLayers.filter((layer) => layer.type === "image");
    for (let index = 0; index < imageLayers.length; index += 1) {
      const layer = imageLayers[index];
      if (layer.serverPath) continue;
      const uploaded = await uploadServerFile(layer.file, "design_image", "", (percent, message) => {
        const overall = ((index + percent / 100) / Math.max(1, imageLayers.length)) * 100;
        setDesignUploadProgress(overall, `上传 ${layer.name} · ${message}`);
      });
      layer.serverPath = uploaded.path;
    }
    const layers = state.designLayers.map((layer) => layer.type === "text" ? {
      id: layer.id,
      type: "text",
      text: layer.text,
      color: layer.color,
      font_size_ratio: layer.fontSizeRatio,
      max_width: layer.maxWidth,
      font_weight: layer.fontWeight,
      x: layer.x,
      y: layer.y,
    } : {
      id: layer.id,
      type: "image",
      path: layer.serverPath,
      width: layer.width,
      x: layer.x,
      y: layer.y,
    });
    const variants = Array.isArray(state.designCandidate?.design_variants) && state.designCandidate.design_variants.length
      ? state.designCandidate.design_variants
      : ["通用版"];
    const baseAssetIds = state.designCandidate?.design_base_asset_ids || {};
    const options = {
      content_type: "auto",
      segment_strategy: "uniform",
      audio_policy: "auto",
      rights_status: "MANUAL_REVIEW",
      batch_label: "文案设计版",
      design: { layers, variants, base_asset_ids: baseAssetIds, base_asset_id: baseAssetIds["通用版"] || baseAssetIds["FB版"] || "" },
    };
    document.querySelector("#designDialog").close();
    await runBatchAction("produce", [], options, state.pendingProductionIds);
  } catch (error) {
    toast(error.message, "error");
  } finally {
    submit.disabled = false;
  }
});

document.querySelector("#discoverForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = document.querySelector("#submitDiscovery");
  button.disabled = true;
  try {
    const mode = document.querySelector("#discoverMode").value;
    const platform = document.querySelector("#discoverPlatform").value;
    if (mode === "upload") {
      const file = document.querySelector("#discoverUploadFile").files[0];
      if (!file) throw new Error("请选择要上传的源视频");
      const uploaded = await uploadServerFile(file, "source", "", setDiscoverUploadProgress);
      document.querySelector("#discoverUploadFile").value = "";
      document.querySelector("#discoverDialog").close();
      toast(`源视频已入库：${uploaded.candidate_id}，可在待制作中生成成片`);
      state.status = "DOWNLOADED";
      await refreshAll();
      return;
    }
    document.querySelector("#discoverDialog").close();
    const url = document.querySelector("#discoverUrl").value.trim();
    const payload = { action: "ingest", platform, url };
    const result = await api("/api/actions", { method: "POST", body: JSON.stringify(payload) });
    state.status = "DOWNLOADED";
    toast(`URL 导入下载任务 ${result.task_id} 已启动，完成后进入待制作`);
    pollTask(result.task_id);
  } catch (error) { toast(error.message, "error"); }
  finally { button.disabled = false; }
});

document.querySelector("#scheduleForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/api/publications", { method: "POST", body: JSON.stringify({ candidate_id: document.querySelector("#scheduleCandidate").value, platform: document.querySelector("#schedulePlatform").value, account: document.querySelector("#scheduleAccount").value, scheduled_at: document.querySelector("#scheduleTime").value || null }) });
    toast("已加入发布队列");
    await refreshAll();
  } catch (error) { toast(error.message, "error"); }
});

document.querySelector("#xAuthForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const account = document.querySelector("#xAuthAccount").value || "consumer_main";
  window.open(`/oauth/x/start?account=${encodeURIComponent(account)}`, "_blank", "noopener");
  toast("已打开 X 授权页面；完成后回到这里刷新并确认账号");
});

document.querySelector("#xAuthTable").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-x-auth-action]");
  if (!button) return;
  const action = button.dataset.xAuthAction;
  const account = button.dataset.account;
  button.disabled = true;
  try {
    await api("/api/x-auths", { method: "POST", body: JSON.stringify({ account, action }) });
    toast(action === "confirm" ? "X 账号已确认，可发布" : "X 授权已撤销");
    state.xAuths = await api("/api/x-auths");
    renderXAuths();
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
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

document.querySelector("#youtubeGrowthFilters").addEventListener("submit", (event) => {
  event.preventDefault();
  loadYouTubeAnalytics({ resetPage: true });
});
document.querySelector("#youtubeGrowthRange").addEventListener("change", () => {
  updateYouTubeCustomDates();
  if (document.querySelector("#youtubeGrowthRange").value !== "custom") {
    loadYouTubeAnalytics({ resetPage: true });
  }
});
document.querySelector("#youtubeGrowthAccount").addEventListener("change", () => loadYouTubeAnalytics({ resetPage: true }));
document.querySelector("#youtubeGrowthMetric").addEventListener("change", () => loadYouTubeAnalytics({ resetPage: true }));
document.querySelector("#youtubeGrowthRetry").addEventListener("click", () => loadYouTubeAnalytics());
document.querySelector("#youtubeGrowthPrevious").addEventListener("click", () => {
  if (state.youtubeGrowth.page <= 1) return;
  state.youtubeGrowth.page -= 1;
  loadYouTubeAnalytics();
});
document.querySelector("#youtubeGrowthNext").addEventListener("click", () => {
  if (state.youtubeGrowth.page >= state.youtubeGrowth.pages) return;
  state.youtubeGrowth.page += 1;
  loadYouTubeAnalytics();
});
updateYouTubeCustomDates();

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

refreshAll();
