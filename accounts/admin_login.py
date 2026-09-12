from typing import Any

from django import forms
from django.core.cache import cache
from django.core.validators import validate_ipv46_address

from .login_runtime import login_attempt_keys, login_lock_keys, normalize_login_throttle_username


class LoginRestrictionsClearError(Exception):
    pass


class ClearLoginRestrictionsForm(forms.Form):
    username = forms.CharField(label="用户名", required=False, max_length=150)
    # 保留原始 IPv6 写法，与登录请求中使用的 IP 缓存键一致。
    ip = forms.CharField(label="IP 地址", required=False, validators=[validate_ipv46_address])

    def clean_username(self) -> str:
        return normalize_login_throttle_username(self.cleaned_data["username"]) or ""

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean() or {}
        if not self.errors and not (cleaned_data.get("username") or cleaned_data.get("ip")):
            raise forms.ValidationError("请填写用户名或 IP 地址。")
        return cleaned_data


def clear_login_restrictions(*, username: str = "", ip: str = "") -> None:
    from .views import _local_login_cache_delete

    if not username and not ip:
        raise ValueError("A username or IP address is required")
    keys = []
    for ip_key, username_key in (login_attempt_keys(ip, username), login_lock_keys(ip, username)):
        if ip:
            keys.append(ip_key)
        if username_key:
            keys.append(username_key)

    # 管理操作必须向调用方报告缓存异常，不能沿用登录流程的静默降级删除。
    cache.delete_many(keys)
    if cache.get_many(keys):
        raise LoginRestrictionsClearError("Login restriction keys remain after deletion")
    for key in keys:
        _local_login_cache_delete(key)
