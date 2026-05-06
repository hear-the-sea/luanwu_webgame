"""Core utilities"""

from .error_migration import convert_bool_to_exception, convert_string_result_to_exception
from .http import accepts_json, is_ajax_request, is_json_request
from .network import get_client_ip
from .responses import json_error, json_success
from .validation import (
    parse_json_object,
    require_positive_int,
    safe_float,
    safe_int,
    safe_int_list,
    safe_non_negative_int,
    safe_ordering,
    safe_positive_int,
    safe_redirect_url,
    sanitize_error_message,
)

__all__ = [
    "require_positive_int",
    "safe_int",
    "safe_non_negative_int",
    "safe_positive_int",
    "safe_float",
    "parse_json_object",
    "safe_int_list",
    "safe_ordering",
    "safe_redirect_url",
    "sanitize_error_message",
    "get_client_ip",
    "accepts_json",
    "is_ajax_request",
    "is_json_request",
    "json_error",
    "json_success",
    "convert_bool_to_exception",
    "convert_string_result_to_exception",
]
