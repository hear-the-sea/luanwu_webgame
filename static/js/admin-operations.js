"use strict";

(() => {
  const input = document.getElementById("operation-search");
  if (!input) return;
  const groups = [...document.querySelectorAll(".operation-group")];
  let savedOpen = groups.map((group) => group.open);
  let previousQuery = "";
  input.addEventListener("input", () => {
    const query = input.value.trim().toLocaleLowerCase();
    if (query && !previousQuery) savedOpen = groups.map((group) => group.open);
    let matches = 0;
    groups.forEach((group, index) => {
      let count = 0;
      group.querySelectorAll("[data-operation-name]").forEach((row) => {
        row.hidden = !row.dataset.operationName.toLocaleLowerCase().includes(query);
        if (!row.hidden) count += 1;
      });
      group.hidden = count === 0;
      group.open = query ? count > 0 : savedOpen[index];
      matches += count;
    });
    document.getElementById("operation-empty").hidden = matches > 0;
    previousQuery = query;
  });
})();
