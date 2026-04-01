(() => {
  const initGuildHeroPool = () => {
    const page = document.querySelector('[data-ghp-page="hero-pool"]');
    if (!page) {
      return;
    }

    const chips = Array.from(page.querySelectorAll(".ghp-chip"));
    const rows = Array.from(page.querySelectorAll(".ghp-roster-row"));
    const searchInput = page.querySelector(".ghp-search-input");
    const filterEmptyState = page.querySelector(".ghp-filter-empty");
    const slotTabs = Array.from(page.querySelectorAll("[data-ghp-slot-target]"));
    const slotCards = Array.from(page.querySelectorAll("[data-ghp-slot-card]"));

    if (!rows.length) {
      return;
    }

    const setActiveFilter = (nextFilter) => {
      chips.forEach((chip) => {
        chip.classList.toggle("is-active", chip.dataset.statusFilter === nextFilter);
      });
    };

    const applyFilters = () => {
      const activeFilter = page.querySelector(".ghp-chip.is-active")?.dataset.statusFilter || "all";
      const keyword = (searchInput?.value || "").trim().toLowerCase();
      let visibleCount = 0;

      rows.forEach((row) => {
        const statusKey = row.dataset.statusKey || "";
        const searchText = (row.dataset.searchText || "").toLowerCase();
        const matchesFilter = activeFilter === "all" || statusKey === activeFilter;
        const matchesKeyword = keyword === "" || searchText.includes(keyword);
        const isVisible = matchesFilter && matchesKeyword;

        row.hidden = !isVisible;
        if (isVisible) {
          visibleCount += 1;
        }
      });

      if (filterEmptyState) {
        filterEmptyState.hidden = visibleCount > 0;
      }
    };

    const setActiveSlot = (slotIndex) => {
      slotTabs.forEach((tab) => {
        const isActive = tab.dataset.ghpSlotTarget === slotIndex;
        tab.classList.toggle("is-active", isActive);
        tab.setAttribute("aria-pressed", isActive ? "true" : "false");
      });

      slotCards.forEach((card) => {
        card.hidden = card.dataset.ghpSlotCard !== slotIndex;
      });
    };

    chips.forEach((chip) => {
      if (chip.dataset.ghpBound === "1") {
        return;
      }
      chip.dataset.ghpBound = "1";
      chip.addEventListener("click", () => {
        setActiveFilter(chip.dataset.statusFilter || "all");
        applyFilters();
      });
    });

    if (searchInput && searchInput.dataset.ghpBound !== "1") {
      searchInput.dataset.ghpBound = "1";
      searchInput.addEventListener("input", applyFilters);
    }

    slotTabs.forEach((tab) => {
      if (tab.dataset.ghpBound === "1") {
        return;
      }
      tab.dataset.ghpBound = "1";
      tab.addEventListener("click", () => {
        setActiveSlot(tab.dataset.ghpSlotTarget || "1");
      });
    });

    const joinableCount = Number.parseInt(page.dataset.filterCountJoinable || "", 10);
    if (Number.isFinite(joinableCount) && joinableCount <= 0) {
      setActiveFilter("all");
    }

    setActiveSlot(page.querySelector('[data-ghp-slot-target][aria-pressed="true"]')?.dataset.ghpSlotTarget || "1");
    applyFilters();
  };

  document.addEventListener("DOMContentLoaded", initGuildHeroPool);
  document.addEventListener("partial-nav:loaded", initGuildHeroPool);
})();
