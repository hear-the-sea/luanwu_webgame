/**
 * Real-time online user statistics via WebSocket
 */

(function() {
    'use strict';

    // 检查是否支持 WebSocket
    if (!window.WebSocket) {
        console.error('WebSocket is not supported by this browser');
        return;
    }

    // 构建 WebSocket URL
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/online-stats/`;

    const STABLE_CONNECTION_DELAY = 30000;
    const reconnectPolicy = window.WebSocketReconnectPolicy.createReconnectPolicy();

    let currentSocket = null;
    let reconnectTimer = null;
    let stabilityTimer = null;
    let disposed = false;
    let disconnected = false;

    function clearReconnectTimer() {
        if (reconnectTimer === null) return;
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
    }

    function clearStabilityTimer() {
        if (stabilityTimer === null) return;
        clearTimeout(stabilityTimer);
        stabilityTimer = null;
    }

    function scheduleReconnect(socket, closeCode) {
        if (disposed || currentSocket !== socket || reconnectTimer !== null) return;
        if (!reconnectPolicy.shouldReconnect(closeCode)) {
            clearReconnectTimer();
            return;
        }

        const scheduledTimer = setTimeout(function() {
            if (disposed || currentSocket !== socket || reconnectTimer !== scheduledTimer) return;
            reconnectTimer = null;
            connectWebSocket();
        }, reconnectPolicy.nextDelay(closeCode));
        reconnectTimer = scheduledTimer;
    }

    function connectWebSocket() {
        if (disposed) return;
        try {
            const socket = new WebSocket(wsUrl);
            currentSocket = socket;

            socket.onopen = function() {
                if (currentSocket !== socket || disposed) return;
                console.log('在线统计 WebSocket 已连接');
                clearStabilityTimer();
                const scheduledTimer = setTimeout(function() {
                    if (currentSocket !== socket || stabilityTimer !== scheduledTimer) return;
                    stabilityTimer = null;
                    reconnectPolicy.markStable();
                    disconnected = false;
                }, STABLE_CONNECTION_DELAY);
                stabilityTimer = scheduledTimer;
            };

            socket.onmessage = function(event) {
                if (currentSocket !== socket || disposed) return;
                try {
                    const data = JSON.parse(event.data);
                    if (!data || typeof data !== 'object') return;
                    updateOnlineStats(data);
                    reconnectPolicy.markStable();
                    disconnected = false;
                    clearStabilityTimer();
                } catch (error) {
                    console.error('解析在线统计数据失败:', error);
                }
            };

            socket.onerror = function(error) {
                if (currentSocket !== socket || disposed) return;
                if (!disconnected) {
                    console.error('WebSocket 错误:', error);
                    disconnected = true;
                }
            };

            socket.onclose = function(event) {
                if (currentSocket !== socket || disposed) return;
                clearStabilityTimer();
                if (!disconnected) {
                    console.log('在线统计 WebSocket 已断开');
                    disconnected = true;
                }
                scheduleReconnect(socket, event && event.code);
            };
        } catch (error) {
            console.error('创建 WebSocket 连接失败:', error);
        }
    }

    function updateOnlineStats(data) {
        const onlineCountElement = document.getElementById('online-user-count');
        const totalCountElement = document.getElementById('total-user-count');

        if (onlineCountElement && data.online_count !== undefined) {
            onlineCountElement.textContent = data.online_count;
        }

        if (totalCountElement && data.total_count !== undefined) {
            totalCountElement.textContent = data.total_count;
        }
    }

    // 页面加载完成后连接 WebSocket
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', connectWebSocket);
    } else {
        connectWebSocket();
    }

    function suspendConnection() {
        if (disposed) return;
        disposed = true;
        clearReconnectTimer();
        clearStabilityTimer();
        const socket = currentSocket;
        currentSocket = null;
        if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
            socket.close();
        }
    }

    function restoreConnection() {
        if (!disposed) return;
        disposed = false;
        disconnected = false;
        connectWebSocket();
    }

    window.addEventListener('pagehide', suspendConnection);
    window.addEventListener('pageshow', restoreConnection);
})();
