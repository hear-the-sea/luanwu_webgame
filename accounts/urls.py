from django.contrib.auth import views as auth_views
from django.urls import path

from .views import (
    EmailVerificationRecoveryView,
    LoginView,
    ProfileView,
    RegisterView,
    ResendEmailVerificationView,
    VerifyEmailView,
)

app_name = "accounts"

urlpatterns = [
    path("login/", LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("register/", RegisterView.as_view(), name="register"),
    path(
        "recover-email-verification/",
        EmailVerificationRecoveryView.as_view(),
        name="email_verification_recovery",
    ),
    path("resend-email-verification/", ResendEmailVerificationView.as_view(), name="resend_email_verification"),
    path("verify-email/<str:token>/", VerifyEmailView.as_view(), name="verify_email"),
    path("profile/", ProfileView.as_view(), name="profile"),
]
