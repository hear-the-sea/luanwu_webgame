/**
 * 通用工具提示定位模块
 * 统一处理 PC 悬浮与触屏点击的提示框定位
 */
(function(root, factory) {
    'use strict';

    const tooltipApi = factory(root);

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = tooltipApi;
    }

    if (root) {
        root.ItemTooltipCore = tooltipApi;
        root.initItemTooltip = tooltipApi.initTooltip;
    }
})(typeof window !== 'undefined' ? window : globalThis, function(root) {
    'use strict';

    const tooltipRegistry = root
        ? (root.__webgame_tooltip = root.__webgame_tooltip || {})
        : {};

    function clamp(value, min, max) {
        return Math.min(Math.max(value, min), max);
    }

    function throttle(fn, delay) {
        let lastCall = 0;
        let timeoutId = null;
        return function(...args) {
            const now = Date.now();
            const remaining = delay - (now - lastCall);
            if (remaining <= 0) {
                if (timeoutId) {
                    clearTimeout(timeoutId);
                    timeoutId = null;
                }
                lastCall = now;
                fn.apply(this, args);
            } else if (!timeoutId) {
                timeoutId = setTimeout(() => {
                    lastCall = Date.now();
                    timeoutId = null;
                    fn.apply(this, args);
                }, remaining);
            }
        };
    }

    function createRelativeAnchor(cellRect, clientX, clientY) {
        if (!cellRect || !Number.isFinite(clientX) || !Number.isFinite(clientY)) {
            return null;
        }

        return {
            relativeX: clamp(clientX - cellRect.left, 0, cellRect.width),
            relativeY: clamp(clientY - cellRect.top, 0, cellRect.height),
        };
    }

    function resolveAnchorPoint(cellRect, anchor) {
        if (!cellRect) {
            return { x: 0, y: 0 };
        }

        if (!anchor) {
            return {
                x: cellRect.left,
                y: cellRect.bottom,
            };
        }

        return {
            x: cellRect.left + clamp(anchor.relativeX, 0, cellRect.width),
            y: cellRect.top + clamp(anchor.relativeY, 0, cellRect.height),
        };
    }

    function computeTooltipPosition(options) {
        const {
            anchorX,
            anchorY,
            tooltipWidth,
            tooltipHeight,
            viewportWidth,
            viewportHeight,
            viewportPadding,
            offset,
        } = options;

        const minLeft = viewportPadding;
        const maxLeft = Math.max(minLeft, viewportWidth - tooltipWidth - viewportPadding);
        const minTop = viewportPadding;
        const maxTop = Math.max(minTop, viewportHeight - tooltipHeight - viewportPadding);

        let left = anchorX + offset;
        if (left + tooltipWidth > viewportWidth - viewportPadding) {
            left = anchorX - tooltipWidth - offset;
        }

        let top = anchorY + offset;
        if (top + tooltipHeight > viewportHeight - viewportPadding) {
            top = anchorY - tooltipHeight - offset;
        }

        return {
            left: clamp(left, minLeft, maxLeft),
            top: clamp(top, minTop, maxTop),
        };
    }

    function measureTooltip(tooltip) {
        let rect = tooltip.getBoundingClientRect();
        if (rect.width > 0 && rect.height > 0) {
            return rect;
        }

        const previousDisplay = tooltip.style.display;
        const previousVisibility = tooltip.style.visibility;

        tooltip.style.display = 'block';
        tooltip.style.visibility = 'hidden';
        rect = tooltip.getBoundingClientRect();
        tooltip.style.display = previousDisplay;
        tooltip.style.visibility = previousVisibility;

        return rect;
    }

    function initTooltip(options) {
        if (!root || typeof document === 'undefined') {
            return;
        }

        options = options || {};
        const supportsHover = !!(
            root.matchMedia
            && root.matchMedia('(hover: hover) and (pointer: fine)').matches
        );
        const config = {
            key: options.key || 'default',
            cellSelector: options.cellSelector || '.tw-item-cell',
            tooltipSelector: options.tooltipSelector || '.tw-item-tooltip',
            viewportPadding: options.viewportPadding || 20,
            offset: options.offset || 8,
            trackPointer: options.trackPointer !== undefined ? !!options.trackPointer : true,
            enableHover: options.enableHover !== undefined ? !!options.enableHover : supportsHover,
            enableTouch: options.enableTouch !== undefined ? !!options.enableTouch : !supportsHover,
        };

        if (!document.querySelector(config.cellSelector)) {
            return;
        }

        if (tooltipRegistry[config.key]) {
            return;
        }
        tooltipRegistry[config.key] = true;

        let activeCell = null;
        let activeAnchor = null;
        let rafId = null;

        function clearTooltipPosition(cell) {
            const targetCell = cell || activeCell;
            const tooltip = targetCell?.querySelector?.(config.tooltipSelector);
            if (tooltip) {
                tooltip.style.top = '';
                tooltip.style.left = '';
            }
        }

        function clearActiveCell() {
            if (activeCell?.classList) {
                clearTooltipPosition(activeCell);
                activeCell.classList.remove('is-tooltip-active');
            }
            activeCell = null;
            activeAnchor = null;
        }

        function updateAnchorFromEvent(cell, event) {
            if (!cell || !event) {
                activeAnchor = null;
                return;
            }

            activeAnchor = createRelativeAnchor(
                cell.getBoundingClientRect(),
                event.clientX,
                event.clientY
            );
        }

        function requestUpdate() {
            if (rafId) {
                return;
            }

            rafId = requestAnimationFrame(() => {
                rafId = null;
                if (!activeCell || !activeCell.isConnected) {
                    clearActiveCell();
                    return;
                }

                const tooltip = activeCell.querySelector(config.tooltipSelector);
                if (!tooltip) {
                    return;
                }

                const cellRect = activeCell.getBoundingClientRect();
                const tooltipRect = measureTooltip(tooltip);
                if (tooltipRect.width === 0 || tooltipRect.height === 0) {
                    return;
                }

                const anchorPoint = resolveAnchorPoint(cellRect, activeAnchor);
                const nextPosition = computeTooltipPosition({
                    anchorX: anchorPoint.x,
                    anchorY: anchorPoint.y,
                    tooltipWidth: tooltipRect.width,
                    tooltipHeight: tooltipRect.height,
                    viewportWidth: root.innerWidth,
                    viewportHeight: root.innerHeight,
                    viewportPadding: config.viewportPadding,
                    offset: config.offset,
                });

                tooltip.style.top = nextPosition.top + 'px';
                tooltip.style.left = nextPosition.left + 'px';
            });
        }

        function setActiveCell(cell, event) {
            if (activeCell !== cell && activeCell?.classList) {
                clearTooltipPosition(activeCell);
                activeCell.classList.remove('is-tooltip-active');
            }

            activeCell = cell;
            activeAnchor = null;
            activeCell.classList.add('is-tooltip-active');
            if (config.trackPointer) {
                updateAnchorFromEvent(activeCell, event);
            }
            requestUpdate();
        }

        if (config.enableHover) {
            document.addEventListener('mouseover', function(event) {
                const cell = event.target?.closest?.(config.cellSelector);
                if (cell) {
                    setActiveCell(cell, event);
                }
            });

            if (config.trackPointer) {
                document.addEventListener('mousemove', throttle(function(event) {
                    const cell = event.target?.closest?.(config.cellSelector);
                    if (activeCell && cell === activeCell) {
                        updateAnchorFromEvent(activeCell, event);
                        requestUpdate();
                    }
                }, 32));
            }

            document.addEventListener('mouseout', function(event) {
                const cell = event.target?.closest?.(config.cellSelector);
                if (cell && cell === activeCell && (!event.relatedTarget || !cell.contains(event.relatedTarget))) {
                    clearActiveCell();
                }
            });
        }

        if (config.enableTouch) {
            document.addEventListener('click', function(event) {
                const cell = event.target?.closest?.(config.cellSelector);
                if (cell) {
                    if (activeCell === cell) {
                        clearActiveCell();
                    } else {
                        setActiveCell(cell, event);
                    }
                    return;
                }

                if (activeCell) {
                    clearActiveCell();
                }
            });
        }

        const handleScroll = throttle(function() {
            if (activeCell) {
                requestUpdate();
            }
        }, 16);

        root.addEventListener('scroll', handleScroll, { passive: true });
        document.addEventListener('scroll', handleScroll, { passive: true, capture: true });

        root.addEventListener('resize', throttle(function() {
            if (activeCell) {
                requestUpdate();
            }
        }, 100));

        document.addEventListener('keydown', function(event) {
            if (event.key === 'Escape' && activeCell) {
                clearActiveCell();
            }
        });
    }

    return {
        clamp,
        throttle,
        createRelativeAnchor,
        resolveAnchorPoint,
        computeTooltipPosition,
        measureTooltip,
        initTooltip,
    };
});
