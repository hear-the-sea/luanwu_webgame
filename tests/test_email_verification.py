from __future__ import annotations

import re
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core import mail
from django.core.cache import cache
from django.core.signing import SignatureExpired, TimestampSigner
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from accounts import views as account_views
from accounts.email_delivery import create_email_verification_delivery, process_email_verification_delivery
from accounts.email_providers import EMAIL_PROVIDER_BREVO, EMAIL_PROVIDER_RESEND
from accounts.email_quota import EmailQuotaExceeded, quota_month, release_email_send_slot, reserve_email_send_slot
from accounts.email_verification import EMAIL_VERIFICATION_LEGACY_SALT, build_email_verification_token
from accounts.forms import EmailVerificationRecoveryForm, SignUpForm
from accounts.models import EmailProviderDailyQuota, EmailSendQuota

# The production flow deliberately schedules delivery with transaction.on_commit.
# These tests need real commits so they exercise the same boundary and can
# observe the LocMem backend after the callback runs.
pytestmark = pytest.mark.django_db(transaction=True)


def _registration_data(*, username: str, email: str, manor_name: str) -> dict[str, str]:
    return {
        "username": username,
        "email": email,
        "manor_name": manor_name,
        "region": "overseas",
        "password1": "StrongPass123!",
        "password2": "StrongPass123!",
    }


def _verification_token_from_email() -> str:
    match = re.search(r"/accounts/verify-email/([^/\s]+)/", mail.outbox[-1].body)
    assert match is not None
    return match.group(1)


def _create_pending_user(django_user_model, *, username: str, email: str):
    user = django_user_model.objects.create_user(username=username, email=email, password="StrongPass123!")
    user.is_active = False
    user.email_verified = False
    user.save(update_fields=("is_active", "email_verified"))
    return user


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_registration_creates_pending_user_and_sends_verification_email(client, django_user_model):
    response = client.post(
        reverse("accounts:register"),
        _registration_data(
            username="email_pending_user",
            email="email-pending@example.com",
            manor_name="邮箱待验证庄园",
        ),
    )

    assert response.status_code == 200
    assert "验证你的邮箱" in response.content.decode()
    user = django_user_model.objects.get(username="email_pending_user")
    assert user.is_active is False
    assert user.email_verified is False
    assert client.session.get("_auth_user_id") is None
    assert client.session.get("pending_email_verification_user_id") == user.pk
    assert len(mail.outbox) == 1
    assert "email-pending@example.com" in mail.outbox[0].to
    assert mail.outbox[0].subject == "完成邮箱验证，开启你的春秋乱世之旅"
    assert "verify-email/" in mail.outbox[0].body
    assert _verification_token_from_email() not in response.content.decode()
    assert EmailSendQuota.objects.get(month=quota_month()).sent_count == 1


@pytest.mark.parametrize("resend", [False, True])
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_pending_page_resend_credential_cannot_activate_account(client, django_user_model, resend):
    cache.clear()
    response = client.post(
        reverse("accounts:register"),
        _registration_data(
            username="email_credential_isolation",
            email="unowned-email@example.com",
            manor_name="邮箱凭据隔离庄园",
        ),
    )
    resend_token_match = re.search(r'name="token" value="([^"]+)"', response.content.decode())
    assert resend_token_match is not None
    resend_token = resend_token_match.group(1)
    if resend:
        # The page credential works without relying on the registration session.
        client.session.flush()
        response = client.post(reverse("accounts:resend_email_verification"), {"token": resend_token})
        assert response.status_code == 200
        assert len(mail.outbox) == 2
        resend_token_match = re.search(r'name="token" value="([^"]+)"', response.content.decode())
        assert resend_token_match is not None
        resend_token = resend_token_match.group(1)

    email_token = _verification_token_from_email()
    assert email_token not in response.content.decode()
    rejected = client.post(reverse("accounts:verify_email", kwargs={"token": resend_token}))
    assert rejected.status_code == 400
    user = django_user_model.objects.get(username="email_credential_isolation")
    assert not user.email_verified and not user.is_active
    assert client.session.get("_auth_user_id") is None

    verified = client.post(reverse("accounts:verify_email", kwargs={"token": email_token}))
    assert verified.status_code == 302
    user.refresh_from_db()
    assert user.email_verified and user.is_active


@pytest.mark.parametrize("legacy_age_hours", [0, 48])
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_queued_legacy_activation_token_only_allows_limited_email_resend(client, django_user_model, legacy_age_hours):
    cache.clear()
    user = _create_pending_user(
        django_user_model, username="email_legacy_rotation", email="legacy-rotation@example.com"
    )
    issued_at = timezone.now().timestamp() - legacy_age_hours * 3600
    with patch("django.core.signing.time.time", return_value=issued_at):
        legacy_token = TimestampSigner(salt=EMAIL_VERIFICATION_LEGACY_SALT).sign_object(
            {"user_id": int(user.pk), "email": str(user.email)}
        )

    # Already-persisted deliveries keep their original URL. The received old
    # link must recover safely without a data migration or direct activation.
    legacy_url = reverse("accounts:verify_email", kwargs={"token": legacy_token})
    delivery = create_email_verification_delivery(
        user=user,
        reservation=reserve_email_send_slot(preferred_provider=EMAIL_PROVIDER_RESEND),
        verification_url=f"https://testserver{legacy_url}",
    )
    assert process_email_verification_delivery(delivery.pk)
    assert _verification_token_from_email() == legacy_token
    landing = client.get(legacy_url)
    assert landing.status_code == 400
    assert "重新发送验证邮件" in landing.content.decode()
    assert client.post(legacy_url).status_code == 400
    user.refresh_from_db()
    assert not user.is_active and not user.email_verified
    assert client.session.get("_auth_user_id") is None

    resent = client.post(reverse("accounts:resend_email_verification"), {"token": legacy_token})
    assert resent.status_code == 200
    assert len(mail.outbox) == 2
    current_token = _verification_token_from_email()
    assert current_token != legacy_token
    assert current_token not in resent.content.decode()
    repeated = client.post(reverse("accounts:resend_email_verification"), {"token": legacy_token})
    assert "验证邮件刚刚发送过" in repeated.content.decode()
    assert len(mail.outbox) == 2
    assert EmailSendQuota.objects.get(month=quota_month()).sent_count == 2

    assert client.post(reverse("accounts:verify_email", kwargs={"token": current_token})).status_code == 302
    user.refresh_from_db()
    assert user.is_active and user.email_verified


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_verification_rechecks_email_after_acquiring_user_lock(client, django_user_model, monkeypatch):
    user = _create_pending_user(
        django_user_model, username="email_changed_during_verification", email="verified-address@example.com"
    )
    token = build_email_verification_token(user)
    original_resolve = account_views.get_user_from_email_verification_token

    def change_email_after_token_validation(value):
        resolved_user = original_resolve(value)
        assert resolved_user is not None
        django_user_model.objects.filter(pk=resolved_user.pk).update(email="different-address@example.com")
        return resolved_user

    monkeypatch.setattr(account_views, "get_user_from_email_verification_token", change_email_after_token_validation)
    response = client.post(reverse("accounts:verify_email", kwargs={"token": token}))
    user.refresh_from_db()
    assert response.status_code == 400
    assert user.email == "different-address@example.com"
    assert not user.is_active and not user.email_verified
    assert client.session.get("_auth_user_id") is None


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_verification_link_requires_post_then_activates_user_and_logs_them_in(client, django_user_model):
    client.post(
        reverse("accounts:register"),
        _registration_data(
            username="email_verify_user",
            email="email-verify@example.com",
            manor_name="邮箱验证庄园",
        ),
    )
    token = _verification_token_from_email()

    verification_url = reverse("accounts:verify_email", kwargs={"token": token})
    response = client.get(verification_url)

    assert response.status_code == 200
    assert "确认并验证邮箱" in response.content.decode()
    user = django_user_model.objects.get(username="email_verify_user")
    assert user.is_active is False
    assert user.email_verified is False
    assert client.session.get("_auth_user_id") is None

    response = client.post(verification_url)

    assert response.status_code == 302
    assert response["Location"] == "/"
    user.refresh_from_db()
    assert user.is_active is True
    assert user.email_verified is True
    assert client.session.get("_auth_user_id") == str(user.pk)

    repeated = client.get(reverse("accounts:verify_email", kwargs={"token": token}))
    assert repeated.status_code == 200
    assert "邮箱已经验证" in repeated.content.decode()


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_VERIFICATION_TOKEN_MAX_AGE_SECONDS=60,
)
def test_invalid_verification_link_is_rejected(client, django_user_model, monkeypatch):
    client.post(
        reverse("accounts:register"),
        _registration_data(
            username="email_expired_user",
            email="email-expired@example.com",
            manor_name="邮箱过期庄园",
        ),
    )
    token = _verification_token_from_email()
    monkeypatch.setattr(
        "accounts.email_verification.TimestampSigner.unsign_object",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("expired")),
    )

    response = client.get(reverse("accounts:verify_email", kwargs={"token": token}))

    assert response.status_code == 400
    assert "验证链接无效" in response.content.decode()
    user = django_user_model.objects.get(username="email_expired_user")
    assert user.is_active is False
    assert user.email_verified is False


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_expired_verification_link_offers_resend_without_registration_session(client, django_user_model, monkeypatch):
    client.post(
        reverse("accounts:register"),
        _registration_data(
            username="email_expired_resend_user",
            email="email-expired-resend@example.com",
            manor_name="邮箱过期重发庄园",
        ),
        REMOTE_ADDR="203.0.113.203",
    )
    token = _verification_token_from_email()
    client.session.flush()

    original_unsign_object = TimestampSigner.unsign_object

    def raise_signature_expired(self, value, **kwargs):
        if kwargs.get("max_age") is not None:
            raise SignatureExpired("expired")
        return original_unsign_object(self, value, **kwargs)

    monkeypatch.setattr(
        "accounts.email_verification.TimestampSigner.unsign_object",
        raise_signature_expired,
    )

    result = client.get(reverse("accounts:verify_email", kwargs={"token": token}))

    assert result.status_code == 400
    assert "重新发送验证邮件" in result.content.decode()

    resent = client.post(
        reverse("accounts:resend_email_verification"),
        {"token": token},
        REMOTE_ADDR="203.0.113.204",
    )

    assert resent.status_code == 200
    assert "新的验证邮件已安排发送" in resent.content.decode()
    assert len(mail.outbox) == 2
    assert EmailSendQuota.objects.get(month=quota_month()).sent_count == 2
    assert django_user_model.objects.get(username="email_expired_resend_user").is_active is False


def test_auth_pages_link_to_email_verification_recovery(client):
    recovery_url = reverse("accounts:email_verification_recovery")

    for page_url in (reverse("accounts:login"), reverse("accounts:register")):
        response = client.get(page_url)

        assert response.status_code == 200
        assert recovery_url in response.content.decode()

    recovery_page = client.get(recovery_url)
    assert recovery_page.status_code == 200
    assert "找回验证邮件" in recovery_page.content.decode()


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_recovery_resends_for_pending_user_without_registration_session(client, django_user_model):
    cache.clear()
    pending_user = _create_pending_user(
        django_user_model,
        username="email_recovery_pending_user",
        email="email-recovery-pending@example.com",
    )
    client.session.flush()

    response = client.post(
        reverse("accounts:email_verification_recovery"),
        {"email": " EMAIL-RECOVERY-PENDING@EXAMPLE.COM "},
        REMOTE_ADDR="203.0.113.230",
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert "如果该邮箱对应未完成验证的账号" in content
    assert pending_user.email not in content
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [pending_user.email]
    assert EmailSendQuota.objects.get(month=quota_month()).sent_count == 1


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_recovery_response_does_not_reveal_email_registration_state(client, django_user_model):
    cache.clear()
    verified_user = django_user_model.objects.create_user(
        username="email_recovery_verified_user",
        email="email-recovery-verified@example.com",
        password="StrongPass123!",
    )

    verified_response = client.post(
        reverse("accounts:email_verification_recovery"),
        {"email": verified_user.email},
        REMOTE_ADDR="203.0.113.231",
    )
    missing_response = client.post(
        reverse("accounts:email_verification_recovery"),
        {"email": "email-recovery-missing@example.com"},
        REMOTE_ADDR="203.0.113.232",
    )

    verified_content = verified_response.content.decode()
    missing_content = missing_response.content.decode()
    assert verified_response.status_code == 200
    assert missing_response.status_code == 200
    assert "如果该邮箱对应未完成验证的账号" in verified_content
    assert "如果该邮箱对应未完成验证的账号" in missing_content
    assert verified_user.email not in verified_content
    assert "email-recovery-missing@example.com" not in missing_content
    assert len(mail.outbox) == 0


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_recovery_reuses_per_user_cooldown(client, django_user_model):
    cache.clear()
    pending_user = _create_pending_user(
        django_user_model,
        username="email_recovery_cooldown_user",
        email="email-recovery-cooldown@example.com",
    )
    url = reverse("accounts:email_verification_recovery")

    first = client.post(url, {"email": pending_user.email}, REMOTE_ADDR="203.0.113.233")
    second = client.post(url, {"email": pending_user.email}, REMOTE_ADDR="203.0.113.234")

    assert first.status_code == 200
    assert second.status_code == 200
    assert "如果该邮箱对应未完成验证的账号" in second.content.decode()
    assert len(mail.outbox) == 1
    assert EmailSendQuota.objects.get(month=quota_month()).sent_count == 1


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_resend_and_recovery_share_increased_ip_limit(client, django_user_model):
    cache.clear()
    pending_user = _create_pending_user(
        django_user_model,
        username="email_shared_ip_limit_user",
        email="email-shared-ip-limit@example.com",
    )
    resend_url = reverse("accounts:resend_email_verification")
    recovery_url = reverse("accounts:email_verification_recovery")
    token = build_email_verification_token(pending_user)
    limit = account_views.EMAIL_VERIFICATION_RESEND_IP_LIMIT

    for index in range(limit):
        cache.delete(account_views._email_verification_resend_cache_key(pending_user.pk))
        # Both entry points must use the same IP budget.
        if index == 0:
            url, data = recovery_url, {"email": pending_user.email}
        else:
            url, data = resend_url, {"token": token}
        response = client.post(url, data, REMOTE_ADDR="203.0.113.170")

        assert response.status_code == 200
        assert len(mail.outbox) == index + 1

    cache.delete(account_views._email_verification_resend_cache_key(pending_user.pk))
    limited = client.post(resend_url, {"token": token}, REMOTE_ADDR="203.0.113.170")

    assert limited.status_code == 302
    assert limited["Location"] == reverse("accounts:register")
    assert len(mail.outbox) == limit
    assert EmailSendQuota.objects.get(month=quota_month()).sent_count == limit


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_recovery_has_increased_normalized_email_limit_across_ips(client, django_user_model):
    cache.clear()
    pending_user = _create_pending_user(
        django_user_model,
        username="email_recovery_limit_user",
        email="email-recovery-limit@example.com",
    )
    url = reverse("accounts:email_verification_recovery")
    limit = account_views.EMAIL_VERIFICATION_RECOVERY_EMAIL_LIMIT

    for index in range(limit):
        cache.delete(account_views._email_verification_resend_cache_key(pending_user.pk))
        response = client.post(
            url,
            {"email": f" {pending_user.email.upper()} " if index % 2 else pending_user.email},
            REMOTE_ADDR=f"203.0.113.{180 + index}",
        )

        assert response.status_code == 200
        assert len(mail.outbox) == index + 1

    cache.delete(account_views._email_verification_resend_cache_key(pending_user.pk))
    limited = client.post(url, {"email": pending_user.email}, REMOTE_ADDR="203.0.113.199")

    assert limited.status_code == 302
    assert limited["Location"] == url
    assert len(mail.outbox) == limit
    assert EmailSendQuota.objects.get(month=quota_month()).sent_count == limit


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_recovery_and_resend_share_per_user_cooldown(client, django_user_model):
    cache.clear()
    pending_user = _create_pending_user(
        django_user_model,
        username="email_shared_cooldown_user",
        email="email-shared-cooldown@example.com",
    )

    recovery = client.post(
        reverse("accounts:email_verification_recovery"),
        {"email": pending_user.email},
        REMOTE_ADDR="203.0.113.171",
    )
    resend = client.post(
        reverse("accounts:resend_email_verification"),
        {"token": build_email_verification_token(pending_user)},
        REMOTE_ADDR="203.0.113.172",
    )

    assert recovery.status_code == 200
    assert resend.status_code == 200
    assert "验证邮件刚刚发送过" in resend.content.decode()
    assert len(mail.outbox) == 1
    assert EmailSendQuota.objects.get(month=quota_month()).sent_count == 1


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_MONTHLY_SEND_LIMIT=0,
)
def test_recovery_respects_monthly_email_quota(client, django_user_model):
    cache.clear()
    pending_user = _create_pending_user(
        django_user_model,
        username="email_recovery_quota_user",
        email="email-recovery-quota@example.com",
    )

    response = client.post(
        reverse("accounts:email_verification_recovery"),
        {"email": pending_user.email},
        REMOTE_ADDR="203.0.113.235",
    )

    assert response.status_code == 200
    assert "本月验证邮件额度已用尽" in response.content.decode()
    assert len(mail.outbox) == 0


def test_email_verification_recovery_form_has_accessible_email_attributes():
    form = EmailVerificationRecoveryForm()

    assert form.fields["email"].widget.attrs["autocomplete"] == "email"
    assert form.fields["email"].widget.attrs["inputmode"] == "email"
    assert form.fields["email"].widget.attrs["spellcheck"] == "false"


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS=60,
)
def test_resend_cooldown_prevents_duplicate_messages(client):
    cache.clear()
    client.post(
        reverse("accounts:register"),
        _registration_data(
            username="email_resend_cooldown_user",
            email="email-resend-cooldown@example.com",
            manor_name="邮箱重发冷却庄园",
        ),
        REMOTE_ADDR="203.0.113.205",
    )

    url = reverse("accounts:resend_email_verification")
    first = client.post(url, REMOTE_ADDR="203.0.113.206")
    second = client.post(url, REMOTE_ADDR="203.0.113.206")

    assert first.status_code == 200
    assert "新的验证邮件已安排发送" in first.content.decode()
    assert second.status_code == 200
    assert "验证邮件刚刚发送过" in second.content.decode()
    assert len(mail.outbox) == 2
    assert EmailSendQuota.objects.get(month=quota_month()).sent_count == 2


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_resend_token_keeps_parallel_registration_bound_to_its_user(client):
    cache.clear()
    registration = client.post(
        reverse("accounts:register"),
        _registration_data(
            username="email_parallel_user_a",
            email="email-parallel-a@example.com",
            manor_name="邮箱并行甲庄园",
        ),
        REMOTE_ADDR="203.0.113.210",
    )
    token_match = re.search(r'name="token" value="([^"]+)"', registration.content.decode())
    assert token_match is not None
    token_a = token_match.group(1)

    client.post(
        reverse("accounts:register"),
        _registration_data(
            username="email_parallel_user_b",
            email="email-parallel-b@example.com",
            manor_name="邮箱并行乙庄园",
        ),
        REMOTE_ADDR="203.0.113.211",
    )

    response = client.post(
        reverse("accounts:resend_email_verification"),
        {"token": token_a},
        REMOTE_ADDR="203.0.113.212",
    )

    assert response.status_code == 200
    assert mail.outbox[-1].to == ["email-parallel-a@example.com"]
    assert EmailSendQuota.objects.get(month=quota_month()).sent_count == 3


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_invalid_link_with_pending_session_offers_resend(client):
    client.post(
        reverse("accounts:register"),
        _registration_data(
            username="email_invalid_session_user",
            email="email-invalid-session@example.com",
            manor_name="邮箱无效链接庄园",
        ),
        REMOTE_ADDR="203.0.113.207",
    )

    response = client.get(reverse("accounts:verify_email", kwargs={"token": "tampered-token"}))

    assert response.status_code == 400
    assert "重新发送验证邮件" in response.content.decode()


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_MONTHLY_SEND_LIMIT=1,
)
def test_resend_respects_monthly_email_quota(client):
    cache.clear()
    client.post(
        reverse("accounts:register"),
        _registration_data(
            username="email_resend_quota_user",
            email="email-resend-quota@example.com",
            manor_name="邮箱重发额度庄园",
        ),
        REMOTE_ADDR="203.0.113.208",
    )

    response = client.post(
        reverse("accounts:resend_email_verification"),
        REMOTE_ADDR="203.0.113.209",
    )

    assert response.status_code == 200
    assert "本月注册验证邮件额度已用尽" in response.content.decode()
    assert len(mail.outbox) == 1
    assert EmailSendQuota.objects.get(month=quota_month()).sent_count == 1


def test_signup_form_has_accessible_auth_attributes():
    form = SignUpForm()

    assert form.fields["username"].widget.attrs["autocomplete"] == "username"
    assert form.fields["username"].widget.attrs["spellcheck"] == "false"
    assert form.fields["email"].widget.attrs["autocomplete"] == "email"
    assert form.fields["email"].widget.attrs["inputmode"] == "email"
    assert form.fields["email"].widget.attrs["spellcheck"] == "false"
    assert form.fields["manor_name"].widget.attrs["autocomplete"] == "off"
    assert form.fields["manor_name"].widget.attrs["aria-describedby"] == "id_manor_name_help"


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_MONTHLY_SEND_LIMIT=1,
)
def test_monthly_email_quota_stops_new_registration(client, django_user_model):
    url = reverse("accounts:register")
    first = client.post(
        url,
        _registration_data(
            username="quota_first_user",
            email="quota-first@example.com",
            manor_name="额度首个庄园",
        ),
        REMOTE_ADDR="203.0.113.201",
    )
    second = client.post(
        url,
        _registration_data(
            username="quota_second_user",
            email="quota-second@example.com",
            manor_name="额度第二庄园",
        ),
        REMOTE_ADDR="203.0.113.202",
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert "本月注册验证邮件额度已用尽" in second.content.decode()
    assert not django_user_model.objects.filter(username="quota_second_user").exists()
    assert len(mail.outbox) == 1
    assert EmailSendQuota.objects.get(month=quota_month()).sent_count == 1


def test_quota_month_uses_first_day_of_local_month():
    current_time = timezone.now() - timedelta(days=1)

    assert quota_month(current_time).day == 1


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_RESEND_DAILY_SEND_LIMIT=1,
    EMAIL_BREVO_DAILY_SEND_LIMIT=1,
    EMAIL_MONTHLY_SEND_LIMIT=10,
)
def test_provider_daily_quota_falls_back_from_resend_to_brevo(client):
    first = reserve_email_send_slot(preferred_provider=EMAIL_PROVIDER_RESEND)
    second = reserve_email_send_slot(preferred_provider=EMAIL_PROVIDER_RESEND)

    assert first.provider == EMAIL_PROVIDER_RESEND
    assert second.provider == EMAIL_PROVIDER_BREVO
    assert EmailSendQuota.objects.get(month=quota_month()).sent_count == 2
    assert (
        EmailProviderDailyQuota.objects.get(
            provider=EMAIL_PROVIDER_RESEND,
            day=first.day,
        ).sent_count
        == 1
    )
    assert (
        EmailProviderDailyQuota.objects.get(
            provider=EMAIL_PROVIDER_BREVO,
            day=second.day,
        ).sent_count
        == 1
    )

    with pytest.raises(EmailQuotaExceeded) as error:
        reserve_email_send_slot(preferred_provider=EMAIL_PROVIDER_RESEND)
    assert error.value.scope == "daily"


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_RESEND_DAILY_SEND_LIMIT=10,
    EMAIL_BREVO_DAILY_SEND_LIMIT=10,
    EMAIL_MONTHLY_SEND_LIMIT=10,
)
def test_resend_alternates_provider_and_persists_last_provider(client, django_user_model):
    cache.clear()
    client.post(
        reverse("accounts:register"),
        _registration_data(
            username="email_provider_switch_user",
            email="email-provider-switch@example.com",
            manor_name="供应商切换庄园",
        ),
        REMOTE_ADDR="203.0.113.240",
    )
    pending_user = django_user_model.objects.get(username="email_provider_switch_user")
    assert pending_user.email_verification_last_provider == EMAIL_PROVIDER_RESEND

    client.post(
        reverse("accounts:resend_email_verification"),
        REMOTE_ADDR="203.0.113.241",
    )
    pending_user.refresh_from_db()
    assert pending_user.email_verification_last_provider == EMAIL_PROVIDER_BREVO

    cache.clear()
    client.post(
        reverse("accounts:resend_email_verification"),
        REMOTE_ADDR="203.0.113.242",
    )
    pending_user.refresh_from_db()
    assert pending_user.email_verification_last_provider == EMAIL_PROVIDER_RESEND
    assert len(mail.outbox) == 3


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_RESEND_DAILY_SEND_LIMIT=1,
    EMAIL_BREVO_DAILY_SEND_LIMIT=1,
    EMAIL_MONTHLY_SEND_LIMIT=10,
)
def test_registration_stops_when_both_provider_daily_quotas_are_exhausted(client, django_user_model):
    cache.clear()
    for index in range(2):
        response = client.post(
            reverse("accounts:register"),
            _registration_data(
                username=f"daily_quota_user_{index}",
                email=f"daily-quota-{index}@example.com",
                manor_name=f"每日额度庄园{index}",
            ),
            REMOTE_ADDR=f"203.0.113.{250 + index}",
        )
        assert response.status_code == 200

    blocked = client.post(
        reverse("accounts:register"),
        _registration_data(
            username="daily_quota_blocked_user",
            email="daily-quota-blocked@example.com",
            manor_name="每日额度阻断庄园",
        ),
        REMOTE_ADDR="203.0.113.252",
    )

    assert blocked.status_code == 200
    assert "今日验证邮件额度已用尽" in blocked.content.decode()
    assert not django_user_model.objects.filter(username="daily_quota_blocked_user").exists()
    assert len(mail.outbox) == 2


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_RESEND_DAILY_SEND_LIMIT=1,
    EMAIL_BREVO_DAILY_SEND_LIMIT=1,
    EMAIL_MONTHLY_SEND_LIMIT=10,
)
def test_releasing_unattempted_reservation_restores_both_counters():
    reservation = reserve_email_send_slot(preferred_provider=EMAIL_PROVIDER_RESEND)
    release_email_send_slot(reservation)

    assert EmailSendQuota.objects.get(month=quota_month()).sent_count == 0
    assert (
        EmailProviderDailyQuota.objects.get(
            provider=EMAIL_PROVIDER_RESEND,
            day=reservation.day,
        ).sent_count
        == 0
    )
