from __future__ import annotations

from typing import Any, Callable

PUBLIC_REAL_USER_REGISTRATION_MARKER = "_public_real_user_registration"


def prepare_signup_user(*, form: Any) -> Any:
    user = form.save(commit=False)
    user._signup_region = form.cleaned_data.get("region", "overseas")
    user._signup_manor_name = (form.cleaned_data.get("manor_name") or "").strip()
    setattr(user, PUBLIC_REAL_USER_REGISTRATION_MARKER, True)
    return user


def save_signup_user(
    user: Any,
    *,
    transaction_atomic: Callable[[], Any],
) -> Any:
    with transaction_atomic():
        user.save()
    return user


def apply_registration_integrity_errors(
    *,
    form: Any,
    user_model: Any,
    manor_model: Any,
) -> None:
    normalized_email = (form.cleaned_data.get("email") or "").strip().lower()
    username = (form.cleaned_data.get("username") or "").strip()
    manor_name = (form.cleaned_data.get("manor_name") or "").strip()

    if normalized_email and user_model.objects.filter(email__iexact=normalized_email).exists():
        form.add_error("email", "该邮箱已注册")
        return
    if username and user_model.objects.filter(username=username).exists():
        form.add_error("username", "该用户名已存在")
        return
    if manor_name and manor_model.objects.filter(name__iexact=manor_name).exists():
        form.add_error("manor_name", "该庄园名称已被使用")
        return
    form.add_error(None, "注册失败，请稍后重试")
