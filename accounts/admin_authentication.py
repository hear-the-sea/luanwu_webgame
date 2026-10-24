from typing import Any

from django.contrib.admin.forms import AdminAuthenticationForm
from django.core.exceptions import ValidationError


class ThrottledAdminAuthenticationForm(AdminAuthenticationForm):
    """后台与玩家登录共用账号、IP 失败计数及降级锁。"""

    def clean(self) -> dict[str, Any]:
        from .views import _check_login_attempts, _clear_login_attempts, _record_failed_attempt

        if self.request is None:
            raise ValidationError("登录请求无效，请刷新后重试", code="invalid_request")
        username = self.cleaned_data.get("username") or (self.data.get("username", "") or "").strip()
        locked, _remaining = _check_login_attempts(self.request, username)
        if locked:
            raise ValidationError("登录尝试次数过多，请稍后再试", code="too_many_attempts")
        try:
            cleaned_data = super().clean()
        except ValidationError:
            _record_failed_attempt(self.request, username)
            raise
        if self.get_user() is None:
            # 必填字段错误同样计入 IP 桶，不能借畸形提交绕过限制。
            _record_failed_attempt(self.request, username)
        else:
            # AdminAuthenticationForm 已校验 is_active/is_staff；成功仍保留 IP 桶。
            _clear_login_attempts(self.request, username, clear_ip=False)
        return cleaned_data
