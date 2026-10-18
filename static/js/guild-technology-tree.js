(() => {
  const routeTabs = Array.from(document.querySelectorAll("[data-route-tab]"));
  const routePanels = Array.from(document.querySelectorAll("[data-route-panel]"));
  const trees = Array.from(document.querySelectorAll("[data-tech-tree]"));
  const dialog = document.getElementById("tw-tech-dialog");
  const routeConfirmationDialog = document.getElementById("tw-tech-route-confirm");
  const routeConfirmationName = routeConfirmationDialog?.querySelector("[data-route-confirm-name]");
  const routeConfirmationSubmit = routeConfirmationDialog?.querySelector("[data-route-confirm-submit]");
  let activeNode = null;
  let pendingRouteForm = null;
  let layoutFrame = 0;

  const getRequirements = (node) => (node.dataset.requires || "").split(",").filter(Boolean);

  const getNodeDepth = (node, nodesByKey, cache, visiting = new Set()) => {
    const key = node.dataset.nodeKey;
    if (cache.has(key)) {
      return cache.get(key);
    }
    if (visiting.has(key)) {
      return 0;
    }

    visiting.add(key);
    const parents = getRequirements(node).map((parentKey) => nodesByKey.get(parentKey)).filter(Boolean);
    const depth = parents.length
      ? Math.max(...parents.map((parent) => getNodeDepth(parent, nodesByKey, cache, visiting))) + 1
      : 0;
    visiting.delete(key);
    cache.set(key, depth);
    return depth;
  };

  const layoutTree = (tree) => {
    if (!tree || tree.closest("[hidden]")) {
      return;
    }

    const nodes = Array.from(tree.querySelectorAll("[data-tech-node]"));
    const edgeLayer = tree.querySelector(".tw-tech-edges");
    if (!nodes.length || !edgeLayer) {
      return;
    }

    const nodesByKey = new Map(nodes.map((node) => [node.dataset.nodeKey, node]));
    const sourceOrder = new Map(nodes.map((node, index) => [node.dataset.nodeKey, index]));
    const depthCache = new Map();
    const rows = new Map();
    nodes.forEach((node) => {
      const depth = getNodeDepth(node, nodesByKey, depthCache);
      node.dataset.depth = String(depth);
      if (!rows.has(depth)) {
        rows.set(depth, []);
      }
      rows.get(depth).push(node);
    });

    const rowDepths = Array.from(rows.keys()).sort((a, b) => a - b);
    const nodeWidth = 112;
    const nodeGap = 20;
    const widestRow = Math.max(...Array.from(rows.values(), (row) => row.length));
    const requiredWidth = widestRow * nodeWidth + Math.max(0, widestRow - 1) * nodeGap + 64;
    const scrollArea = tree.parentElement;
    const visibleWidth = scrollArea?.clientWidth || 0;
    const width = Math.max(680, visibleWidth, requiredWidth);
    const rowStep = 150;
    const topInset = 26;
    const height = topInset + rowDepths.length * rowStep + 18;

    tree.style.width = `${width}px`;
    tree.style.height = `${height}px`;
    edgeLayer.setAttribute("viewBox", `0 0 ${width} ${height}`);
    edgeLayer.setAttribute("width", String(width));
    edgeLayer.setAttribute("height", String(height));
    edgeLayer.replaceChildren();

    rowDepths.forEach((depth) => {
      const row = rows.get(depth);
      if (depth > 0) {
        const parentCenter = (node) => {
          const parentPositions = getRequirements(node)
            .map((parentKey) => nodesByKey.get(parentKey))
            .filter((parent) => parent?.style.left)
            .map((parent) => Number.parseFloat(parent.style.left));
          return parentPositions.length
            ? parentPositions.reduce((total, position) => total + position, 0) / parentPositions.length
            : Number.POSITIVE_INFINITY;
        };
        row.sort((left, right) => (
          parentCenter(left) - parentCenter(right)
          || sourceOrder.get(left.dataset.nodeKey) - sourceOrder.get(right.dataset.nodeKey)
        ));
      }
      const rowWidth = row.length * nodeWidth + Math.max(0, row.length - 1) * nodeGap;
      const startX = (width - rowWidth) / 2;
      row.forEach((node, index) => {
        const centerX = startX + index * (nodeWidth + nodeGap) + nodeWidth / 2;
        const topY = topInset + depth * rowStep;
        node.style.left = `${centerX}px`;
        node.style.top = `${topY}px`;
      });
    });

    nodes.forEach((node) => {
      const childDepth = Number(node.dataset.depth || 0);
      if (!childDepth) {
        return;
      }
      const childX = Number.parseFloat(node.style.left);
      const childY = Number.parseFloat(node.style.top) + 4;
      getRequirements(node).forEach((parentKey) => {
        const parent = nodesByKey.get(parentKey);
        if (!parent) {
          return;
        }
        const parentX = Number.parseFloat(parent.style.left);
        const parentY = Number.parseFloat(parent.style.top) + 67;
        const curveY = parentY + (childY - parentY) * 0.52;
        const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
        path.setAttribute("d", `M ${parentX} ${parentY} C ${parentX} ${curveY}, ${childX} ${curveY}, ${childX} ${childY}`);
        path.setAttribute("class", `tw-tech-edge${Number(parent.dataset.level || 0) > 0 ? " is-open" : ""}`);
        edgeLayer.appendChild(path);
      });
    });
  };

  const scheduleLayout = () => {
    window.cancelAnimationFrame(layoutFrame);
    layoutFrame = window.requestAnimationFrame(() => trees.forEach(layoutTree));
  };

  const selectRoute = (routeKey, focusTab = false) => {
    routeTabs.forEach((tab) => {
      const selected = tab.dataset.routeTab === routeKey;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
      tab.classList.toggle("is-selected", selected);
      if (selected && focusTab) {
        tab.focus();
      }
    });
    routePanels.forEach((panel) => {
      panel.hidden = panel.dataset.routePanel !== routeKey;
    });
    scheduleLayout();
  };

  routeTabs.forEach((tab, index) => {
    tab.addEventListener("click", () => selectRoute(tab.dataset.routeTab));
    tab.addEventListener("keydown", (event) => {
      let nextIndex = null;
      if (event.key === "ArrowRight" || event.key === "ArrowDown") {
        nextIndex = (index + 1) % routeTabs.length;
      } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
        nextIndex = (index - 1 + routeTabs.length) % routeTabs.length;
      } else if (event.key === "Home") {
        nextIndex = 0;
      } else if (event.key === "End") {
        nextIndex = routeTabs.length - 1;
      }
      if (nextIndex !== null) {
        event.preventDefault();
        selectRoute(routeTabs[nextIndex].dataset.routeTab, true);
      }
    });
  });

  const cancelRouteConfirmation = () => {
    pendingRouteForm = null;
    routeConfirmationDialog?.close();
  };

  document.querySelectorAll("[data-route-select-form]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (form.dataset.routeConfirmed === "true") {
        delete form.dataset.routeConfirmed;
        return;
      }

      event.preventDefault();
      const routeName = form.dataset.routeName || "这条路线";
      if (!routeConfirmationDialog || typeof routeConfirmationDialog.showModal !== "function") {
        if (window.confirm(`确认开启「${routeName}」吗？本系已开放天赋全部研究完成后，可开启下一条路线；暂未开放的节点不计入完成条件。`)) {
          form.dataset.routeConfirmed = "true";
          form.requestSubmit();
        }
        return;
      }

      pendingRouteForm = form;
      if (routeConfirmationName) {
        routeConfirmationName.textContent = `「${routeName}」`;
      }
      routeConfirmationDialog.showModal();
      routeConfirmationSubmit?.focus();
    });
  });

  routeConfirmationDialog?.querySelector("[data-route-confirm-close]")?.addEventListener("click", cancelRouteConfirmation);
  routeConfirmationDialog?.querySelector("[data-route-confirm-cancel]")?.addEventListener("click", cancelRouteConfirmation);
  routeConfirmationDialog?.addEventListener("cancel", () => {
    pendingRouteForm = null;
  });
  routeConfirmationDialog?.addEventListener("click", (event) => {
    if (event.target === routeConfirmationDialog) {
      cancelRouteConfirmation();
    }
  });
  routeConfirmationSubmit?.addEventListener("click", () => {
    const form = pendingRouteForm;
    if (!form) {
      return;
    }
    pendingRouteForm = null;
    form.dataset.routeConfirmed = "true";
    routeConfirmationDialog.close();
    form.requestSubmit();
  });

  document.querySelectorAll("[data-tech-node]").forEach((node) => {
    node.addEventListener("click", () => {
      if (!dialog || typeof dialog.showModal !== "function") {
        return;
      }
      const detail = Array.from(dialog.querySelectorAll("[data-tech-detail]"))
        .find((item) => item.dataset.techDetail === node.dataset.nodeKey);
      if (!detail) {
        return;
      }
      dialog.querySelectorAll("[data-tech-detail]").forEach((item) => {
        item.hidden = item !== detail;
      });
      dialog.setAttribute("aria-labelledby", detail.getAttribute("aria-labelledby"));
      activeNode = node;
      dialog.showModal();
      dialog.querySelector("[data-tech-dialog-close]")?.focus();
    });
  });

  dialog?.querySelector("[data-tech-dialog-close]")?.addEventListener("click", () => dialog.close());
  dialog?.addEventListener("click", (event) => {
    if (event.target === dialog) {
      dialog.close();
    }
  });
  dialog?.addEventListener("close", () => {
    if (activeNode?.isConnected) {
      activeNode.focus();
    }
    activeNode = null;
  });

  const defaultTab = routeTabs.find((tab) => tab.getAttribute("aria-selected") === "true");
  if (defaultTab) {
    selectRoute(defaultTab.dataset.routeTab);
  }

  document.querySelectorAll("[data-cavalry-training-form]").forEach((form) => {
    const input = form.querySelector('input[name="quantity"]');
    const updateQuote = () => {
      const quantity = Number(input.value);
      const valid = Number.isSafeInteger(quantity) && quantity > 0 && quantity <= Number(input.max);
      const seconds = quantity * Number(form.dataset.secondsPerUnit);
      const hours = Math.floor(seconds / 3600);
      const minutes = Math.floor((seconds % 3600) / 60);
      const remainder = seconds % 60;
      form.querySelector("[data-cavalry-silver]").textContent = valid
        ? (quantity * Number(form.dataset.silverPerUnit)).toLocaleString("zh-CN") : "—";
      form.querySelector("[data-cavalry-grain]").textContent = valid
        ? (quantity * Number(form.dataset.grainPerUnit)).toLocaleString("zh-CN") : "—";
      form.querySelector("[data-cavalry-duration]").textContent = valid
        ? [hours && `${hours}小时`, minutes && `${minutes}分钟`, remainder && `${remainder}秒`].filter(Boolean).join("") : "—";
    };
    input.addEventListener("input", updateQuote);
    updateQuote();
  });

  document.querySelectorAll("[data-route-reset-form]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (form.dataset.routeResetConfirmed === "true") {
        delete form.dataset.routeResetConfirmed;
        return;
      }

      event.preventDefault();
      const routeName = form.dataset.routeName || "这条路线";
      const message = `取消「${routeName}」后，该路线所有科技等级都会清零，并返还总投入资源的50%；之后可以重新选择。确定继续吗？`;
      const confirmAction = window.gameConfirm
        ? window.gameConfirm(message, { title: "取消科技路线", okText: "确认取消" })
        : Promise.resolve(window.confirm(message));
      Promise.resolve(confirmAction).then((confirmed) => {
        if (!confirmed) {
          return;
        }
        form.dataset.routeResetConfirmed = "true";
        form.requestSubmit();
      });
    });
  });

  const requestedNodeKey = new URLSearchParams(window.location.search).get("node");
  const requestedNode = Array.from(document.querySelectorAll("[data-tech-node]"))
    .find((node) => node.dataset.nodeKey === requestedNodeKey);
  if (requestedNode) {
    const routeKey = requestedNode.closest("[data-route-panel]")?.dataset.routePanel;
    if (routeKey) selectRoute(routeKey);
    requestedNode.click();
  }
  scheduleLayout();
  window.addEventListener("resize", scheduleLayout, { passive: true });
})();
