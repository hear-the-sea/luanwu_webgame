/* 专属门客先预览消耗，再在当前页确认。 */
(() => {
  if (document.documentElement.dataset.customGuestConfirmationBound === "1") return;
  document.documentElement.dataset.customGuestConfirmationBound = "1";

  function updatePortraitPagination(group) {
    const page = Number(group.dataset.avatarPage || 1);
    const pages = Number(group.dataset.avatarPages || 1);
    const loading = group.dataset.avatarLoading === "1";
    group.querySelector('[data-avatar-direction="-1"]').disabled = loading || page <= 1;
    group.querySelector('[data-avatar-direction="1"]').disabled = loading || page >= pages;
  }

  async function loadPortraitGroup(group, requestedPage) {
    const page = Number(requestedPage || group.dataset.avatarPage || group.dataset.avatarInitialPage || 1);
    if (!group.open || group.dataset.avatarLoading === "1") return;
    if (group.dataset.avatarLoaded === "1" && Number(group.dataset.avatarPage) === page) return;
    group.dataset.avatarLoading = "1";
    group.setAttribute("aria-busy", "true");
    updatePortraitPagination(group);
    const status = group.querySelector("[data-avatar-status]");
    status.textContent = "正在加载形象…";
    status.hidden = false;
    try {
      const url = new URL(group.dataset.avatarUrl, window.location.href);
      url.searchParams.set("page", String(page));
      const response = await fetch(url, {
        credentials: "same-origin",
        headers: { "X-Requested-With": "XMLHttpRequest", Accept: "application/json" },
      });
      const data = await response.json();
      if (!response.ok || !data.success || !Array.isArray(data.avatars)) {
        throw new Error(data.error || "形象暂未加载，请重试");
      }
      const selected = group.closest("form").querySelector('input[name="avatar_key"]').value;
      const fragment = document.createDocumentFragment();
      for (const avatar of data.avatars) {
        const label = document.createElement("label");
        label.className = "custom-avatar-option";
        label.title = avatar.label;
        const input = document.createElement("input");
        input.type = "radio";
        input.name = "avatar_choice";
        input.value = avatar.key;
        input.checked = selected === avatar.key;
        const image = document.createElement("img");
        image.alt = avatar.label;
        image.width = 72;
        image.height = 72;
        image.loading = "lazy";
        image.src = avatar.url;
        label.append(input, image);
        fragment.appendChild(label);
      }
      const grid = group.querySelector("[data-avatar-grid]");
      grid.replaceChildren(fragment);
      grid.scrollTop = 0;
      group.dataset.avatarLoaded = "1";
      group.dataset.avatarPage = String(data.page);
      group.dataset.avatarPages = String(data.pages);
      group.querySelector("[data-avatar-pagination]").hidden = false;
      group.querySelector("[data-avatar-page-label]").textContent = `第 ${data.page} / ${data.pages} 页`;
      status.textContent = "暂无可选形象";
      status.hidden = data.avatars.length > 0;
    } catch {
      status.textContent = "形象暂未加载，请收起后重新展开，或再次翻页。";
    } finally {
      delete group.dataset.avatarLoading;
      group.removeAttribute("aria-busy");
      updatePortraitPagination(group);
    }
  }

  // details 的 toggle 事件不冒泡，使用捕获监听兼容页面局部更新。
  document.addEventListener("toggle", (event) => {
    const group = event.target;
    if (group instanceof HTMLDetailsElement && group.hasAttribute("data-avatar-group")) loadPortraitGroup(group);
  }, true);

  document.addEventListener("click", (event) => {
    const button = event.target.closest?.("[data-avatar-direction]");
    if (button && !button.disabled) {
      const group = button.closest("[data-avatar-group]");
      loadPortraitGroup(group, Number(group.dataset.avatarPage) + Number(button.dataset.avatarDirection));
    }
  });

  document.addEventListener("change", (event) => {
    const input = event.target;
    if (!(input instanceof HTMLInputElement) || input.name !== "avatar_choice") return;
    const form = input.closest("form[data-custom-guest-action]");
    if (!form) return;
    form.querySelector('input[name="avatar_key"]').value = input.value;
    form.querySelector("[data-avatar-error]").hidden = true;
    const selectedGroup = input.closest("[data-avatar-group]");
    for (const group of form.querySelectorAll("[data-avatar-group]")) {
      group.querySelector("[data-avatar-selected]").hidden = group !== selectedGroup;
    }
  });

  document.addEventListener("submit", async (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.hasAttribute("data-custom-guest-action")) return;
    if (form.querySelector('input[name="confirmation"]')) return;
    event.preventDefault();
    const avatar = form.querySelector('input[name="avatar_key"]');
    if (avatar && !avatar.value) {
      form.querySelector("[data-avatar-error]").hidden = false;
      form.querySelector("[data-avatar-group] summary")?.focus();
      return;
    }
    if (form.dataset.confirmationPending === "1") return;
    form.dataset.confirmationPending = "1";
    const button = form.querySelector('button[type="submit"]');
    if (button) button.disabled = true;
    let submitted = false;
    try {
      const response = await fetch(form.action, {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-Requested-With": "XMLHttpRequest", Accept: "application/json" },
        body: new FormData(form),
      });
      let data;
      try {
        data = await response.json();
      } catch {
        throw new Error("页面已失效，请刷新后重试");
      }
      if (!response.ok || !data.success) throw new Error(data.error || "操作暂未完成，请稍后重试");
      if (!data.confirmation || !data.preview) throw new Error("确认信息已失效，请重新操作");
      const text = `${data.preview.summary}\n\n${data.preview.detail}`;
      const confirmed = window.gameDialog?.confirm
        ? await window.gameDialog.confirm(text, { title: `确认${data.preview.title}`, okText: "确认", cancelText: "取消" })
        : window.confirm(text);
      if (!confirmed) return;
      const token = document.createElement("input");
      token.type = "hidden";
      token.name = "confirmation";
      token.value = data.confirmation;
      form.appendChild(token);
      HTMLFormElement.prototype.submit.call(form);
      submitted = true;
    } catch (error) {
      const message = error instanceof TypeError ? "请求未完成，请稍后重试" : error?.message || "操作暂未完成，请稍后重试";
      if (window.gameDialog?.error) await window.gameDialog.error(message, { title: "操作未完成" });
      else window.alert(message);
    } finally {
      if (!submitted) {
        delete form.dataset.confirmationPending;
        if (button) button.disabled = false;
      }
    }
  });
})();
