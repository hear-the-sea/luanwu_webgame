"""
Error migration utilities for transitioning from bool/string returns to exceptions.

This module provides decorators to help migrate legacy code that returns
bool or string error codes to code that raises exceptions.

Example usage for bool-to-exception migration:
    @convert_bool_to_exception(
        false_exception=InsufficientResourcesError,
        false_message="Not enough gold to purchase item",
        log_on_false=True,
    )
    def purchase_item(player: Player, item: Item) -> bool:
        if player.gold < item.cost:
            return False
        player.gold -= item.cost
        return True

Example usage for string-to-exception migration:
    @convert_string_result_to_exception(
        error_strings={
            "insufficient_funds": InsufficientResourcesError,
            "inventory_full": InventoryFullError,
            "item_not_found": ItemNotFoundError,
        },
        default_exception=GameError,
    )
    def add_to_inventory(player: Player, item: Item) -> str | None:
        if item not in player.items:
            return "item_not_found"
        if len(player.inventory) >= player.max_slots:
            return "inventory_full"
        player.inventory.append(item)
        return None
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Callable, ParamSpec, TypeVar

from core.exceptions.base import GameError

P = ParamSpec("P")
R = TypeVar("R")

logger = logging.getLogger(__name__)


def convert_bool_to_exception(
    false_exception: type[GameError],
    false_message: str,
    *,
    log_on_false: bool = False,
) -> Callable[[Callable[P, Any]], Callable[P, Any]]:
    """
    Decorator to convert bool-returning functions to exception-raising functions.

    When the wrapped function returns False, raises the specified exception.
    When the function returns True or any truthy value, returns that value.

    Args:
        false_exception: Exception class to raise when function returns False.
        false_message: Message to use when raising the exception.
        log_on_false: If True, logs a warning when False is returned and
            exception is raised. Useful for debugging and monitoring.

    Returns:
        Decorated function that raises exception on False instead of returning it.

    Example:
        @convert_bool_to_exception(
            false_exception=InsufficientResourcesError,
            false_message="Not enough gold",
            log_on_false=True,
        )
        def spend_gold(player: Player, amount: int) -> bool:
            if player.gold < amount:
                return False
            player.gold -= amount
            return True

        # Now raises InsufficientResourcesError instead of returning False
        spend_gold(player, 100)
    """

    def decorator(func: Callable[P, Any]) -> Callable[P, Any]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
            result = func(*args, **kwargs)
            if result is False:
                if log_on_false:
                    logger.warning(
                        "Function %s returned False, raising %s: %s",
                        func.__name__,
                        false_exception.__name__,
                        false_message,
                    )
                raise false_exception(false_message)
            return result

        return wrapper

    return decorator


def convert_string_result_to_exception(
    error_strings: dict[str, type[GameError]],
    default_exception: type[GameError],
) -> Callable[[Callable[P, Any]], Callable[P, Any]]:
    """
    Decorator to convert string error code returns to exceptions.

    When the wrapped function returns a string, looks up the corresponding
    exception class in error_strings and raises it. For unknown strings,
    uses default_exception. Returns normally for non-string results.

    Args:
        error_strings: Dictionary mapping error code strings to exception classes.
        default_exception: Exception class to use for unknown error codes.

    Returns:
        Decorated function that raises exceptions instead of returning error codes.

    Example:
        @convert_string_result_to_exception(
            error_strings={
                "insufficient_funds": InsufficientResourcesError,
                "inventory_full": InventoryFullError,
                "invalid_item": InvalidItemError,
            },
            default_exception=GameError,
        )
        def add_item(player: Player, item_id: int) -> str | None:
            if not Item.exists(item_id):
                return "invalid_item"
            if player.inventory.is_full():
                return "inventory_full"
            if player.gold < Item.get(item_id).cost:
                return "insufficient_funds"
            player.inventory.add(item_id)
            return None

        # Now raises InsufficientResourcesError for "insufficient_funds"
        add_item(player, item_id)
    """

    def decorator(func: Callable[P, Any]) -> Callable[P, Any]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
            result = func(*args, **kwargs)
            if isinstance(result, str):
                exception_class = error_strings.get(result, default_exception)
                raise exception_class(f"Error code: {result}")
            return result

        return wrapper

    return decorator
