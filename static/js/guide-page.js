(function () {
  "use strict";

  function initGuidePage() {
    var guidePage = document.getElementById("guide-page");
    var guideNav = guidePage && guidePage.querySelector("[data-guide-nav]");
    if (!guidePage || !guideNav || !("IntersectionObserver" in window)) {
      return;
    }

    var links = Array.prototype.slice.call(guideNav.querySelectorAll("a[href^='#']"));
    var sections = links
      .map(function (link) {
        return document.getElementById(link.getAttribute("href").slice(1));
      })
      .filter(Boolean);

    if (!sections.length) {
      return;
    }

    var observer = new IntersectionObserver(
      function (entries) {
        var visible = entries
          .filter(function (entry) {
            return entry.isIntersecting;
          })
          .sort(function (left, right) {
            return right.intersectionRatio - left.intersectionRatio;
          })[0];

        if (!visible) {
          return;
        }

        links.forEach(function (link) {
          var isCurrent = link.getAttribute("href") === "#" + visible.target.id;
          link.classList.toggle("active", isCurrent);
          if (isCurrent) {
            link.setAttribute("aria-current", "true");
          } else {
            link.removeAttribute("aria-current");
          }
        });
      },
      {
        rootMargin: "-18% 0px -68% 0px",
        threshold: [0.05, 0.2, 0.5],
      }
    );

    sections.forEach(function (section) {
      observer.observe(section);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initGuidePage, { once: true });
  } else {
    initGuidePage();
  }
})();
