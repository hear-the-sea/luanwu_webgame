(function (globalScope) {
  "use strict";

  const AGILITY_BASELINE = 160;
  const AGILITY_FACTOR_DIVISOR = 500;
  const AGILITY_FACTOR_MIN = 0.7;
  const AGILITY_FACTOR_MAX = 1.2;
  const TROOPS_PER_SIZE_POINT = 200;
  const SIZE_FACTOR_BONUS = 0.5;
  const SIZE_FACTOR_SATURATION = 20;

  function parseFiniteNumber(value, fallback) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function parseNonNegativeInt(value) {
    const parsed = Number.parseInt(value, 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
  }

  function calculateAgilityFactor(averageAgility) {
    const resolvedAgility = parseFiniteNumber(averageAgility, AGILITY_BASELINE);
    const factor = 1 - (resolvedAgility - AGILITY_BASELINE) / AGILITY_FACTOR_DIVISOR;
    return Math.min(AGILITY_FACTOR_MAX, Math.max(AGILITY_FACTOR_MIN, factor));
  }

  function calculateSizeFactor(guestCount, troopCount) {
    const sizeScore =
      Math.max(0, parseNonNegativeInt(guestCount) - 1) +
      parseNonNegativeInt(troopCount) / TROOPS_PER_SIZE_POINT;
    return {
      sizeScore,
      sizeFactor: 1 + (SIZE_FACTOR_BONUS * sizeScore) / (sizeScore + SIZE_FACTOR_SATURATION),
    };
  }

  function calculatePvpTravelTime({
    routeSeconds,
    guestAgilities,
    troopCounts,
    externalFactor = 1,
    timeScale = 1,
  }) {
    const agilities = Array.from(guestAgilities || [])
      .map((value) => parseFiniteNumber(value, Number.NaN))
      .filter(Number.isFinite);
    const troops = Array.from(troopCounts || []).map(parseNonNegativeInt);
    const averageAgility = agilities.length
      ? agilities.reduce((total, value) => total + value, 0) / agilities.length
      : AGILITY_BASELINE;
    const troopCount = troops.reduce((total, value) => total + value, 0);
    const agilityFactor = calculateAgilityFactor(averageAgility);
    const { sizeScore, sizeFactor } = calculateSizeFactor(agilities.length, troopCount);
    const resolvedRouteSeconds = Math.max(0, parseFiniteNumber(routeSeconds, 0));
    const resolvedExternalFactor = Math.max(0, parseFiniteNumber(externalFactor, 1));
    const resolvedTimeScale = Math.max(0.000001, parseFiniteNumber(timeScale, 1));
    const rawGameSeconds = resolvedRouteSeconds * agilityFactor * sizeFactor * resolvedExternalFactor;
    const gameSeconds = rawGameSeconds > 0 ? Math.ceil(rawGameSeconds / 60) * 60 : 0;
    const scaledSeconds = gameSeconds > 0 ? Math.max(1, Math.trunc(gameSeconds / resolvedTimeScale)) : 0;

    return {
      averageAgility,
      agilityFactor,
      guestCount: agilities.length,
      troopCount,
      sizeScore,
      sizeFactor,
      gameSeconds,
      scaledSeconds,
    };
  }

  function formatDuration(seconds) {
    const total = Math.max(0, Math.trunc(parseFiniteNumber(seconds, 0)));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const remainingSeconds = total % 60;
    const parts = [];
    if (hours) parts.push(`${hours}小时`);
    if (minutes) parts.push(`${minutes}分钟`);
    if (remainingSeconds || !parts.length) parts.push(`${remainingSeconds}秒`);
    return parts.join("");
  }

  const api = {
    AGILITY_BASELINE,
    calculateAgilityFactor,
    calculatePvpTravelTime,
    calculateSizeFactor,
    formatDuration,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  globalScope.PvpTravel = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
