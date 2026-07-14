(function (root, factory) {
  const api = factory(root.WorldChatWidgetCore, root.WebSocketReconnectPolicy);

  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }

  root.WorldChatWidgetConnection = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (core, sharedReconnectPolicyApi) {
  "use strict";

  if (!core) {
    throw new Error("WorldChatWidgetCore is required before loading WorldChatWidgetConnection");
  }

  const DEFAULT_PING_INTERVAL_MS = 25000;
  const STABLE_CONNECTION_DELAY_MS = 30000;

  function createConnectionController(config) {
    const wsUrl = config.wsUrl;
    const WebSocketCtor = config.WebSocketCtor;
    const renderer = config.renderer;
    const setStatus = config.setStatus;
    const setTimeoutFn = config.setTimeoutFn || setTimeout;
    const clearTimeoutFn = config.clearTimeoutFn || clearTimeout;
    const setIntervalFn = config.setIntervalFn || setInterval;
    const clearIntervalFn = config.clearIntervalFn || clearInterval;
    const generateOperationId = config.generateOperationId || core.generateOperationId;
    const reconnectPolicyApi = config.reconnectPolicyApi || sharedReconnectPolicyApi;
    if (!reconnectPolicyApi) {
      throw new Error("WebSocketReconnectPolicy is required before creating a chat connection");
    }
    const reconnectPolicy = reconnectPolicyApi.createReconnectPolicy();
    const userIdValue = typeof config.userId === "number" ? config.userId : parseInt(config.userId || "0", 10);
    const userId = Number.isFinite(userIdValue) ? userIdValue : 0;
    const pingIntervalMs = Number.isFinite(config.pingIntervalMs)
      ? config.pingIntervalMs
      : DEFAULT_PING_INTERVAL_MS;

    let socket = null;
    let reconnectTimer = null;
    let stabilityTimer = null;
    let pingTimer = null;
    let disposed = false;
    let terminal = false;
    const pendingOperations = new Map();

    function sendOperation(operation) {
      socket.send(
        JSON.stringify({
          type: "send",
          text: operation.text,
          operation_id: operation.operationId,
        })
      );
    }

    function clearMatchingPending(payload) {
      if (!payload || typeof payload !== "object") return;
      if (payload.type !== "send_ack" && payload.type !== "message" && payload.type !== "error") {
        return;
      }
      const operationId = payload.operation_id != null ? String(payload.operation_id) : "";
      if (!operationId) return;
      if (payload.type === "message") {
        const sender = payload.sender && typeof payload.sender === "object" ? payload.sender : {};
        const senderIdValue = typeof sender.id === "number" ? sender.id : parseInt(sender.id || "0", 10);
        if (!userId || !Number.isFinite(senderIdValue) || senderIdValue !== userId) return;
      }
      pendingOperations.delete(operationId);
    }

    function clearReconnectTimer() {
      if (reconnectTimer === null) return;
      clearTimeoutFn(reconnectTimer);
      reconnectTimer = null;
    }

    function clearStabilityTimer() {
      if (stabilityTimer === null) return;
      clearTimeoutFn(stabilityTimer);
      stabilityTimer = null;
    }

    function clearPingTimer() {
      if (!pingTimer) return;
      clearIntervalFn(pingTimer);
      pingTimer = null;
    }

    function scheduleReconnect(closeCode) {
      if (disposed || reconnectTimer !== null) return;
      if (!reconnectPolicy.shouldReconnect(closeCode)) {
        terminal = true;
        return;
      }
      setStatus("重连中…", "connecting");

      const scheduledTimer = setTimeoutFn(() => {
        if (disposed || reconnectTimer !== scheduledTimer) return;
        reconnectTimer = null;
        connect();
      }, reconnectPolicy.nextDelay(closeCode));
      reconnectTimer = scheduledTimer;
    }

    function connect() {
      if (disposed || terminal) return;
      if (socket && (socket.readyState === WebSocketCtor.OPEN || socket.readyState === WebSocketCtor.CONNECTING)) {
        return;
      }

      clearReconnectTimer();
      clearStabilityTimer();
      clearPingTimer();
      setStatus("连接中", "connecting");

      const currentSocket = new WebSocketCtor(wsUrl);
      socket = currentSocket;

      currentSocket.onopen = () => {
        if (disposed || socket !== currentSocket) return;
        setStatus("已连接", "connected");
        clearPingTimer();
        for (const operation of pendingOperations.values()) {
          try {
            sendOperation(operation);
          } catch (_error) {
            try {
              currentSocket.close();
            } catch (_closeError) {
              socket = null;
              scheduleReconnect(1006);
            }
            return;
          }
        }
        pingTimer = setIntervalFn(() => {
          try {
            if (socket === currentSocket && currentSocket.readyState === WebSocketCtor.OPEN) {
              currentSocket.send(JSON.stringify({ type: "ping" }));
            }
          } catch (_error) {
            // ignore
          }
        }, pingIntervalMs);
        const scheduledTimer = setTimeoutFn(() => {
          if (socket !== currentSocket || stabilityTimer !== scheduledTimer) return;
          stabilityTimer = null;
          reconnectPolicy.markStable();
        }, STABLE_CONNECTION_DELAY_MS);
        stabilityTimer = scheduledTimer;
      };

      currentSocket.onmessage = (event) => {
        if (disposed || socket !== currentSocket) return;
        try {
          const payload = JSON.parse(event.data);
          clearMatchingPending(payload);
          renderer.handlePayload(payload, setStatus);
          reconnectPolicy.markStable();
          clearStabilityTimer();
        } catch (_error) {
          // ignore malformed messages
        }
      };

      currentSocket.onclose = (event) => {
        if (disposed || socket !== currentSocket) return;
        clearPingTimer();
        clearStabilityTimer();
        setStatus("已断开", "disconnected");
        scheduleReconnect(event && event.code);
      };

      currentSocket.onerror = () => {
        if (disposed || socket !== currentSocket) return;
        try {
          currentSocket.close();
        } catch (_error) {
          // ignore
        }
      };
    }

    function sendText(text) {
      if (!text || terminal) return false;

      if (!socket || socket.readyState !== WebSocketCtor.OPEN) {
        renderer.appendSystem("未连接到世界频道，正在重连…", { kind: "error" });
        connect();
        return false;
      }

      try {
        const operationId = String(generateOperationId());
        const operation = { operationId, text };
        pendingOperations.set(operationId, operation);
        try {
          sendOperation(operation);
        } catch (error) {
          pendingOperations.delete(operationId);
          throw error;
        }
        return true;
      } catch (_error) {
        renderer.appendSystem("发送失败，请稍后再试", { kind: "error" });
        return false;
      }
    }

    function teardown() {
      disposed = true;
      clearReconnectTimer();
      clearStabilityTimer();
      clearPingTimer();

      const currentSocket = socket;
      socket = null;
      if (
        currentSocket &&
        (currentSocket.readyState === WebSocketCtor.OPEN || currentSocket.readyState === WebSocketCtor.CONNECTING)
      ) {
        try {
          currentSocket.close();
        } catch (_error) {
          // ignore
        }
      }
    }

    return {
      connect,
      sendText,
      teardown,
    };
  }

  return {
    createConnectionController,
  };
});
