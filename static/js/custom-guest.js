/* 专属门客先预览消耗，再在当前页确认。 */
(() => {
  if (document.documentElement.dataset.customGuestConfirmationBound === "1") return;
  document.documentElement.dataset.customGuestConfirmationBound = "1";

  document.addEventListener("submit", async (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.hasAttribute("data-custom-guest-action")) return;
    if (form.querySelector('input[name="confirmation"]')) return;
    event.preventDefault();
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
