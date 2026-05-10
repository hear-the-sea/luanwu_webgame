(function () {
  const page = document.getElementById("map-page");
  if (!page) {
    return;
  }

  const mapApiBase = page.dataset.mapApiBase || "";
  const scoutApiUrl = page.dataset.scoutApiUrl || "";
  const raidConfigUrlPrefix = page.dataset.raidConfigUrlPrefix || "";
  const currentManorId = Number(page.dataset.currentManorId || "");

  const regionSelect = document.getElementById("region-select");
  const manorSearch = document.getElementById("manor-search");
  const searchBtn = document.getElementById("search-btn");
  const manorList = document.getElementById("manor-list");
  const manorCount = document.getElementById("manor-count");
  const listTitle = document.getElementById("list-title");
  const pagination = document.getElementById("pagination");
  const prevPageBtn = document.getElementById("prev-page");
  const nextPageBtn = document.getElementById("next-page");
  const pageInfo = document.getElementById("page-info");

  if (
    !regionSelect ||
    !manorSearch ||
    !searchBtn ||
    !manorList ||
    !manorCount ||
    !listTitle ||
    !pagination ||
    !prevPageBtn ||
    !nextPageBtn ||
    !pageInfo ||
    !mapApiBase ||
    !scoutApiUrl
  ) {
    return;
  }

  let currentPage = 1;
  let currentRegion = regionSelect.value;
  let currentSearchQuery = manorSearch.value.trim();
  let totalPages = 1;

  function getCSRFToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    const metaToken = meta ? meta.getAttribute("content") : "";
    if (metaToken && metaToken !== "NOTPROVIDED") {
      return metaToken;
    }

    const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
    if (input && input.value) {
      return input.value;
    }

    const cookie = document.cookie
      .split("; ")
      .find((row) => row.startsWith("csrftoken="));
    return cookie ? decodeURIComponent(cookie.split("=")[1]) : "";
  }

  function setListHint(text, className) {
    manorList.replaceChildren();
    const hint = document.createElement("p");
    hint.className = className;
    hint.textContent = text;
    manorList.appendChild(hint);
  }

  function getRegionName(key) {
    const option = regionSelect.querySelector(`option[value="${key}"]`);
    return option ? option.textContent.replace(" (当前)", "").trim() : key;
  }

  function showToast(message, type) {
    const container = document.getElementById("toast-container");
    if (!container) {
      window.alert(message);
      return;
    }

    const toast = document.createElement("div");
    toast.className = `toast toast-${type || "info"}`;
    toast.textContent = message;
    container.appendChild(toast);

    window.setTimeout(() => {
      toast.classList.add("fade-out");
      window.setTimeout(() => toast.remove(), 300);
    }, 4000);
  }

  async function confirmScoutStart() {
    if (typeof window.gameConfirm === "function") {
      return window.gameConfirm("确定要派出探子侦察该庄园吗？", { title: "派出侦察" });
    }
    return window.confirm("确定要派出探子侦察该庄园吗？");
  }

  async function startScout(targetId) {
    const confirmed = await confirmScoutStart();
    if (!confirmed) {
      return;
    }

    try {
      const response = await fetch(scoutApiUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCSRFToken(),
        },
        body: JSON.stringify({ target_id: Number.parseInt(targetId, 10) }),
      });
      const data = await response.json();
      if (data.success) {
        showToast(data.message, "success");
        window.setTimeout(() => window.location.reload(), 1500);
        return;
      }

      showToast(`侦察失败: ${data.error}`, "error");
    } catch (error) {
      console.error("Scout request failed:", error);
      showToast("请求失败，请稍后重试", "error");
    }
  }

  function renderManorList(manors) {
    if (!Array.isArray(manors) || manors.length === 0) {
      setListHint("暂无庄园数据", "tw-empty-state");
      return;
    }

    manorList.replaceChildren();
    const fragment = document.createDocumentFragment();

    manors.forEach((manor) => {
      const manorId = Number(manor && manor.id);
      if (!Number.isFinite(manorId)) {
        return;
      }

      if (fragment.childNodes.length > 0) {
        const divider = document.createElement("div");
        divider.className = "tw-list-divider-line";
        divider.setAttribute("aria-hidden", "true");
        fragment.appendChild(divider);
      }

      const isSelf = manorId === currentManorId;
      const card = document.createElement("div");
      card.className = `tw-manor-card${isSelf ? " self" : ""}`;
      card.dataset.manorId = String(manorId);

      const info = document.createElement("div");
      info.className = "tw-manor-card-info";

      const nameRow = document.createElement("div");
      nameRow.className = "tw-manor-name";
      nameRow.appendChild(document.createTextNode(String(manor.name || "")));

      if (isSelf) {
        const selfBadge = document.createElement("span");
        selfBadge.className = "tw-self-badge";
        selfBadge.textContent = "我";
        nameRow.appendChild(selfBadge);
      }
      if (manor.is_protected) {
        const protectionBadge = document.createElement("span");
        protectionBadge.className = "tw-protection-badge";
        protectionBadge.textContent = "保护中";
        nameRow.appendChild(protectionBadge);
      }

      const locationRow = document.createElement("div");
      locationRow.className = "tw-manor-location";
      const regionDisplay = String(manor.region_display || "");
      const x = Number(manor.coordinate_x);
      const y = Number(manor.coordinate_y);
      let locationText = `${regionDisplay} (${Number.isFinite(x) ? x : "-"}, ${Number.isFinite(y) ? y : "-"})`;
      if (manor.distance !== undefined && manor.distance !== null && Number.isFinite(Number(manor.distance))) {
        locationText += ` | 距离: ${Number(manor.distance).toFixed(1)}`;
      }
      locationRow.textContent = locationText;

      info.appendChild(nameRow);
      info.appendChild(locationRow);

      const stats = document.createElement("div");
      stats.className = "tw-manor-card-stats";

      if (!isSelf) {
        const actions = document.createElement("div");
        actions.className = "tw-manor-card-actions";

        const scoutBtn = document.createElement("button");
        scoutBtn.type = "button";
        scoutBtn.className = "tw-btn-scout";
        scoutBtn.textContent = "侦察";
        scoutBtn.addEventListener("click", (event) => {
          event.stopPropagation();
          startScout(String(manorId));
        });

        const attackLink = document.createElement("a");
        attackLink.className = "tw-btn-attack";
        attackLink.textContent = "进攻";
        attackLink.href = `${raidConfigUrlPrefix}${manorId}/`;

        actions.appendChild(scoutBtn);
        actions.appendChild(attackLink);
        stats.appendChild(actions);
      }

      card.appendChild(info);
      card.appendChild(stats);
      fragment.appendChild(card);
    });

    manorList.appendChild(fragment);
  }

  async function loadManors(pageNumber = 1) {
    currentPage = pageNumber;
    setListHint("正在加载...", "tw-loading-hint");

    let url = `${mapApiBase}?type=region&region=${encodeURIComponent(currentRegion)}&page=${pageNumber}`;
    if (currentSearchQuery) {
      url = `${mapApiBase}?type=name&q=${encodeURIComponent(currentSearchQuery)}`;
    }

    try {
      const response = await fetch(url);
      const data = await response.json();
      if (!data.success) {
        setListHint("加载失败，请稍后重试", "tw-empty-state");
        return;
      }

      renderManorList(data.results);
      manorCount.textContent = `共 ${data.total} 个庄园`;

      if (currentSearchQuery) {
        listTitle.textContent = `搜索结果: "${currentSearchQuery}"`;
        pagination.style.display = "none";
        return;
      }

      listTitle.textContent = `${getRegionName(currentRegion)}地区的庄园`;
      if (data.has_more || pageNumber > 1) {
        pagination.style.display = "flex";
        totalPages = Math.ceil(data.total / data.page_size);
        prevPageBtn.disabled = pageNumber <= 1;
        nextPageBtn.disabled = !data.has_more;
        pageInfo.textContent = `第 ${pageNumber} / ${totalPages} 页`;
        return;
      }

      pagination.style.display = "none";
    } catch (error) {
      console.error("Load error:", error);
      setListHint("加载失败，请稍后重试", "tw-empty-state");
    }
  }

  regionSelect.addEventListener("change", () => {
    currentRegion = regionSelect.value;
    currentSearchQuery = "";
    manorSearch.value = "";
    loadManors(1);
  });

  searchBtn.addEventListener("click", () => {
    currentSearchQuery = manorSearch.value.trim();
    loadManors(1);
  });

  manorSearch.addEventListener("keypress", (event) => {
    if (event.key !== "Enter") {
      return;
    }
    currentSearchQuery = manorSearch.value.trim();
    loadManors(1);
  });

  prevPageBtn.addEventListener("click", () => {
    if (currentPage > 1) {
      loadManors(currentPage - 1);
    }
  });
  nextPageBtn.addEventListener("click", () => loadManors(currentPage + 1));

  loadManors(1);
})();
