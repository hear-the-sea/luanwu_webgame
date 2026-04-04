(function (root, factory) {
  const api = factory();

  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }

  root.WarehousePageCore = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const DEFAULT_SOUL_FUSION_MIN_LEVEL = 30;
  const DEFAULT_SOUL_FUSION_RARITIES = ["green", "blue", "purple"];

  function parsePositiveInt(value, fallbackValue = 0) {
    const parsedValue = Number.parseInt(value, 10);
    return Number.isFinite(parsedValue) && parsedValue > 0 ? parsedValue : fallbackValue;
  }

  function normalizeRarities(rawValue) {
    if (!rawValue) {
      return [...DEFAULT_SOUL_FUSION_RARITIES];
    }

    const normalized = (Array.isArray(rawValue) ? rawValue : String(rawValue).split(","))
      .map((value) => String(value).trim())
      .filter(Boolean);

    return normalized.length > 0 ? normalized : [...DEFAULT_SOUL_FUSION_RARITIES];
  }

  function computeStackedItemCount(items) {
    return Array.from(items || []).reduce(
      (total, item) => total + parsePositiveInt(item && item.quantity, 0),
      0
    );
  }

  function buildWarehouseFilterState(rawState) {
    const source = rawState || {};
    return {
      minLevel: parsePositiveInt(
        source.minLevel !== undefined ? source.minLevel : source.soulFusionMinLevel,
        DEFAULT_SOUL_FUSION_MIN_LEVEL
      ),
      allowedRarities: normalizeRarities(
        source.allowedRarities !== undefined ? source.allowedRarities : source.soulFusionRarities
      ),
    };
  }

  function formatSoulFusionRequirementHint(filterState, rarityLabels = {}) {
    const normalizedState = buildWarehouseFilterState(filterState);
    const rarityText = normalizedState.allowedRarities
      .map((rarity) => rarityLabels[rarity] || rarity)
      .join(" / ");
    return `当前容器要求：${normalizedState.minLevel}级以上，且为${rarityText}门客`;
  }

  function shouldGuestMatchWarehouseFilter(guest, filterState) {
    const normalizedState = buildWarehouseFilterState(filterState);
    const guestLevel = parsePositiveInt(guest && guest.level, 0);
    const guestRarity = String((guest && guest.rarity) || "").trim();
    return guestLevel >= normalizedState.minLevel && normalizedState.allowedRarities.includes(guestRarity);
  }

  return {
    buildWarehouseFilterState,
    computeStackedItemCount,
    formatSoulFusionRequirementHint,
    parsePositiveInt,
    shouldGuestMatchWarehouseFilter,
  };
});
