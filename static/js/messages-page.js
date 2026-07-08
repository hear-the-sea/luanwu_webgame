(() => {
  const createAndSubmitPostForm = (actionUrl, csrfToken) => {
    if (!actionUrl || !csrfToken) {
      return;
    }
    const form = document.createElement("form");
    form.method = "POST";
    form.action = actionUrl;

    const csrfInput = document.createElement("input");
    csrfInput.type = "hidden";
    csrfInput.name = "csrfmiddlewaretoken";
    csrfInput.value = csrfToken;
    form.appendChild(csrfInput);

    document.body.appendChild(form);
    form.submit();
  };

  const initMessagesPage = () => {
    const dashboard = document.querySelector(".dashboard");
    if (!dashboard || !document.getElementById("message-form")) {
      return;
    }

    const deleteAllForm = document.getElementById("delete-all-form");
    if (deleteAllForm && deleteAllForm.dataset.confirmBound !== "1") {
      deleteAllForm.dataset.confirmBound = "1";
      deleteAllForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        try {
          const dialog = window.gameDialog;
          const confirmed = dialog?.danger
            ? await dialog.danger("确认清空所有消息？此操作无法撤销。", { title: "清空消息" })
            : window.confirm("确认清空所有消息？此操作无法撤销。");
          if (confirmed) {
            deleteAllForm.submit();
          }
        } catch (error) {
          console.error("清空消息确认失败:", error);
        }
      });
    }

    const csrfToken = document.querySelector("input[name='csrfmiddlewaretoken']")?.value || "";
    document.querySelectorAll(".js-claim-attachment").forEach((button) => {
      if (button.dataset.clickBound === "1") {
        return;
      }
      button.dataset.clickBound = "1";
      button.addEventListener("click", () => {
        createAndSubmitPostForm(button.dataset.claimUrl || "", csrfToken);
      });
    });

    const unreadCountElement = document.getElementById("unread-count");
    const messageForm = document.getElementById("message-form");
    const messageLinks = document.querySelectorAll(".js-message-link");
    if (!messageLinks.length) {
      return;
    }

    const getCurrentUnreadCount = () => {
      if (!unreadCountElement) {
        return 0;
      }
      const dataValue = unreadCountElement.dataset.unread;
      const textValue = unreadCountElement.textContent.trim();
      const value = dataValue || textValue || "0";
      const parsed = Number.parseInt(value, 10);
      return Number.isNaN(parsed) ? 0 : Math.max(0, parsed);
    };

    const updateUnreadCount = (newCount) => {
      if (!unreadCountElement) {
        return;
      }
      const safeCount = Math.max(0, Number.parseInt(newCount, 10) || 0);
      unreadCountElement.dataset.unread = String(safeCount);
      unreadCountElement.textContent = String(safeCount);
    };

    const markMessageCardAsRead = (link) => {
      link.dataset.isRead = "true";
      const messageRow = link.closest("tr");
      if (messageRow) {
        messageRow.classList.remove("unread");
      }
      const newBadge = link.parentElement?.querySelector(".msg-badge-new");
      if (newBadge) {
        newBadge.remove();
      }
    };

    const shouldBypassInterception = (event) => {
      return event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey;
    };

    const markMessageAsReadAsync = async (messageId, signal) => {
      const markReadUrl = messageForm?.dataset.markReadUrl || "";
      if (!markReadUrl || !messageId) {
        return {};
      }
      const formData = new FormData();
      formData.append("message_ids", messageId);
      const response = await fetch(markReadUrl, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "X-Requested-With": "XMLHttpRequest",
          "X-CSRFToken": csrfToken,
        },
        body: formData,
        credentials: "same-origin",
        signal,
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      return response.json();
    };

    messageLinks.forEach((link) => {
      if (link.dataset.clickBound === "1") {
        return;
      }
      link.dataset.clickBound = "1";
      link.addEventListener("click", (event) => {
        if (shouldBypassInterception(event)) {
          return;
        }
        if (link.dataset.processing === "true") {
          event.preventDefault();
          return;
        }
        if (link.dataset.isRead === "true") {
          return;
        }

        event.preventDefault();
        link.dataset.processing = "true";
        const targetUrl = link.getAttribute("href");
        const controller = new AbortController();
        const timeoutId = window.setTimeout(() => controller.abort(), 4000);

        markMessageAsReadAsync(link.dataset.messageId, controller.signal)
          .then((data) => {
            if (data && typeof data.unread_count === "number") {
              updateUnreadCount(data.unread_count);
            } else {
              updateUnreadCount(getCurrentUnreadCount() - 1);
            }
          })
          .catch((error) => {
            updateUnreadCount(getCurrentUnreadCount() - 1);
            console.warn("Mark message as read failed:", error.message);
          })
          .finally(() => {
            window.clearTimeout(timeoutId);
            markMessageCardAsRead(link);
            window.location.href = targetUrl;
          });
      });
    });
  };

  document.addEventListener("DOMContentLoaded", initMessagesPage);
  document.addEventListener("partial-nav:loaded", initMessagesPage);
})();
