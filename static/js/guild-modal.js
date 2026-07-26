(() => {
  const OVERLAY_SELECTOR = ".tw-modal-overlay";
  const DIALOG_SELECTOR = '[role="dialog"]';
  const FOCUSABLE_SELECTOR = [
    "a[href]",
    "button:not([disabled])",
    "input:not([disabled])",
    "select:not([disabled])",
    "textarea:not([disabled])",
    '[tabindex]:not([tabindex="-1"])',
  ].join(",");

  const registeredOverlays = new WeakSet();
  const overlayStates = new WeakMap();
  let activeOverlay = null;

  const getDialog = (overlay) => {
    if (overlay.matches(DIALOG_SELECTOR)) {
      return overlay;
    }
    return overlay.querySelector(DIALOG_SELECTOR);
  };

  const isOverlayVisible = (overlay) => {
    if (!overlay.isConnected) {
      return false;
    }
    return window.getComputedStyle(overlay).display !== "none";
  };

  const getFocusableElements = (dialog) => Array.from(dialog.querySelectorAll(FOCUSABLE_SELECTOR)).filter(
    (element) => (
      !element.disabled
      && element.getAttribute("aria-hidden") !== "true"
      && element.getClientRects().length > 0
    )
  );

  const isolateBackground = (overlay) => {
    const inertedElements = [];
    let branch = overlay;

    while (branch.parentElement && branch.parentElement !== document.documentElement) {
      const parent = branch.parentElement;
      Array.from(parent.children).forEach((sibling) => {
        if (sibling === branch) {
          return;
        }
        inertedElements.push({
          element: sibling,
          hadInert: sibling.hasAttribute("inert"),
        });
        sibling.setAttribute("inert", "");
      });
      branch = parent;
    }

    return inertedElements;
  };

  const restoreBackground = (inertedElements) => {
    inertedElements.forEach(({ element, hadInert }) => {
      if (!hadInert) {
        element.removeAttribute("inert");
      }
    });
  };

  const deactivateOverlay = (overlay, { restoreFocus = true } = {}) => {
    const state = overlayStates.get(overlay);
    if (!state) {
      getDialog(overlay)?.setAttribute("aria-modal", "false");
      overlay.setAttribute("aria-hidden", "true");
      if (activeOverlay === overlay) {
        activeOverlay = null;
      }
      return;
    }

    restoreBackground(state.inertedElements);
    document.body.style.overflow = state.bodyOverflow;
    if (state.addedDialogTabIndex) {
      state.dialog.removeAttribute("tabindex");
    }
    state.dialog.setAttribute("aria-modal", "false");
    overlay.setAttribute("aria-hidden", "true");
    overlayStates.delete(overlay);
    if (activeOverlay === overlay) {
      activeOverlay = null;
    }

    if (
      restoreFocus
      && state.previousFocus?.isConnected
      && typeof state.previousFocus.focus === "function"
    ) {
      state.previousFocus.focus();
    }
  };

  const activateOverlay = (overlay, trigger = null) => {
    if (activeOverlay === overlay && overlayStates.has(overlay)) {
      return;
    }

    if (activeOverlay && activeOverlay !== overlay) {
      activeOverlay.style.display = "none";
      deactivateOverlay(activeOverlay, { restoreFocus: false });
    }

    const dialog = getDialog(overlay);
    if (!dialog) {
      return;
    }

    const previousFocus = trigger?.isConnected
      ? trigger
      : document.activeElement && document.activeElement !== document.body
        ? document.activeElement
      : null;
    const addedDialogTabIndex = !dialog.hasAttribute("tabindex");
    if (addedDialogTabIndex) {
      dialog.setAttribute("tabindex", "-1");
    }

    dialog.setAttribute("aria-modal", "true");
    overlay.setAttribute("aria-hidden", "false");
    overlayStates.set(overlay, {
      dialog,
      previousFocus,
      addedDialogTabIndex,
      inertedElements: isolateBackground(overlay),
      bodyOverflow: document.body.style.overflow,
    });
    document.body.style.overflow = "hidden";
    activeOverlay = overlay;

    const focusTarget = dialog.querySelector("[data-guild-modal-initial-focus], [autofocus]")
      || getFocusableElements(dialog)[0]
      || dialog;
    focusTarget.focus();
  };

  const resolveOverlay = (target) => {
    if (!target) {
      return activeOverlay;
    }
    if (typeof target === "string") {
      return document.getElementById(target);
    }
    return target.matches?.(OVERLAY_SELECTOR) ? target : target.closest?.(OVERLAY_SELECTOR);
  };

  const openOverlay = (target, trigger = null) => {
    const overlay = resolveOverlay(target);
    if (!overlay || !getDialog(overlay)) {
      return false;
    }
    overlay.style.display = "flex";
    activateOverlay(overlay, trigger);
    return true;
  };

  const closeOverlay = (target = null, options = {}) => {
    const overlay = resolveOverlay(target);
    if (!overlay) {
      return false;
    }
    overlay.style.display = "none";
    deactivateOverlay(overlay, options);
    return true;
  };

  const syncOverlay = (overlay) => {
    if (isOverlayVisible(overlay)) {
      activateOverlay(overlay);
      return;
    }
    deactivateOverlay(overlay);
  };

  const registerOverlay = (overlay) => {
    if (registeredOverlays.has(overlay) || !getDialog(overlay)) {
      return;
    }
    registeredOverlays.add(overlay);
    syncOverlay(overlay);
  };

  const scanForOverlays = (root) => {
    if (!(root instanceof Element)) {
      return;
    }
    if (root.matches(OVERLAY_SELECTOR)) {
      registerOverlay(root);
    }
    root.querySelectorAll(OVERLAY_SELECTOR).forEach(registerOverlay);
  };

  const closeActiveOverlay = () => {
    if (!activeOverlay) {
      return;
    }
    const closeControl = activeOverlay.querySelector("[data-guild-modal-close], .tw-modal-close");
    if (closeControl && typeof closeControl.click === "function") {
      closeControl.click();
      return;
    }
    closeOverlay(activeOverlay);
  };

  const trapActiveOverlayFocus = (event) => {
    if (!activeOverlay || event.key !== "Tab") {
      return;
    }
    const state = overlayStates.get(activeOverlay);
    if (!state) {
      return;
    }

    const focusableElements = getFocusableElements(state.dialog);
    if (!focusableElements.length) {
      event.preventDefault();
      state.dialog.focus();
      return;
    }

    const first = focusableElements[0];
    const last = focusableElements[focusableElements.length - 1];
    if (event.shiftKey && (document.activeElement === first || !state.dialog.contains(document.activeElement))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (document.activeElement === last || !state.dialog.contains(document.activeElement))) {
      event.preventDefault();
      first.focus();
    }
  };

  const init = () => {
    scanForOverlays(document.body);

    const observer = new MutationObserver((mutations) => {
      mutations.forEach((mutation) => {
        if (mutation.type === "attributes") {
          const overlay = mutation.target.closest?.(OVERLAY_SELECTOR);
          if (overlay) {
            syncOverlay(overlay);
          }
          return;
        }
        mutation.addedNodes.forEach(scanForOverlays);
      });

      if (activeOverlay && !activeOverlay.isConnected) {
        deactivateOverlay(activeOverlay);
      }
    });
    observer.observe(document.body, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["class", "style"],
    });

    document.addEventListener("keydown", (event) => {
      if (!activeOverlay) {
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        closeActiveOverlay();
        return;
      }
      trapActiveOverlayFocus(event);
    });

    document.addEventListener("click", (event) => {
      const openControl = event.target.closest?.("[data-guild-modal-open]");
      if (openControl) {
        event.preventDefault();
        openOverlay(openControl.getAttribute("data-guild-modal-open"), openControl);
        return;
      }

      const closeControl = event.target.closest?.("[data-guild-modal-close]");
      if (closeControl) {
        closeOverlay(closeControl.closest(OVERLAY_SELECTOR));
        return;
      }

      const overlay = event.target.closest?.(`${OVERLAY_SELECTOR}[data-guild-modal-backdrop-close]`);
      if (overlay && event.target === overlay) {
        closeOverlay(overlay);
      }
    });
  };

  window.GuildModal = Object.freeze({
    open: openOverlay,
    close: closeOverlay,
    refresh() {
      scanForOverlays(document.body);
    },
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
