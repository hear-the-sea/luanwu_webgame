(function () {
  "use strict";

  function initGuidePage() {
    var guidePage = document.getElementById("guide-page");
    var guideNav = guidePage && guidePage.querySelector("[data-guide-nav]");
    if (!guideNav || guideNav.dataset.initialized) return;
    guideNav.dataset.initialized = "true";

    var tabs = Array.from(guideNav.querySelectorAll('[role="tab"]'));
    var panels = tabs.map(function (tab) {
      return guidePage.querySelector("#" + tab.getAttribute("aria-controls"));
    });
    var skipLink = guidePage.querySelector(".guide-skip-link");
    var reader = guidePage.querySelector(".guide-reader");

    function selectTab(index, updateUrl) {
      tabs.forEach(function (tab, i) {
        var selected = i === index;
        tab.classList.toggle("active", selected);
        tab.setAttribute("aria-selected", String(selected));
        tab.tabIndex = selected ? 0 : -1;
        panels[i].hidden = !selected;
      });
      skipLink.setAttribute("href", "#" + panels[index].id);
      if (reader) reader.scrollTop = 0;
      if (updateUrl) window.history.replaceState(window.history.state, "", "#" + panels[index].id);
    }

    tabs.forEach(function (tab, index) {
      tab.addEventListener("click", function (event) {
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        selectTab(index, true);
      });
      tab.addEventListener("keydown", function (event) {
        var next = index;
        if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
        else if (event.key === "ArrowLeft") next = (index + tabs.length - 1) % tabs.length;
        else if (event.key === "Home") next = 0;
        else if (event.key === "End") next = tabs.length - 1;
        else if (event.key !== " ") return;
        event.preventDefault();
        selectTab(next, true);
        tabs[next].focus();
      });
    });

    var initial = tabs.findIndex(function (tab) {
      return tab.getAttribute("href") === window.location.hash;
    });
    selectTab(initial >= 0 ? initial : 0, false);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initGuidePage, { once: true });
  } else {
    initGuidePage();
  }
  document.addEventListener("partial-nav:loaded", initGuidePage);
})();
