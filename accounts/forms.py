from __future__ import annotations

from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from gameplay.constants import REGION_CHOICES, ManorNameConstants

from .models import User


class SignUpForm(UserCreationForm):
    email = forms.EmailField(label="邮箱", required=True)
    manor_name = forms.CharField(
        label="庄园名称",
        max_length=ManorNameConstants.MAX_LENGTH,
        required=True,
        help_text=(
            f"{ManorNameConstants.MIN_LENGTH}-{ManorNameConstants.MAX_LENGTH}个字符，" "仅支持中文、英文、数字和下划线"
        ),
    )
    region = forms.ChoiceField(
        label="选择地区", choices=REGION_CHOICES, initial="overseas", help_text="选择您庄园所在的地区"
    )

    class Meta(UserCreationForm.Meta):  # type: ignore[name-defined]
        model = User
        fields = ("username", "email", "manor_name", "region", "password1", "password2")

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        autocomplete_by_name = {
            "username": "username",
            "email": "email",
            "manor_name": "off",
            "region": "off",
            "password1": "new-password",
            "password2": "new-password",
        }
        described_by = {
            "manor_name": "id_manor_name_help",
            "region": "id_region_help",
            "password1": "id_password1_help",
        }
        for name, field in self.fields.items():
            field.widget.attrs.update(
                {
                    "class": "input",
                    "placeholder": f"{field.label}…",
                    "autocomplete": autocomplete_by_name.get(name, "off"),
                },
            )
            if name in described_by:
                field.widget.attrs["aria-describedby"] = described_by[name]
            if name in {"username", "email"}:
                field.widget.attrs["spellcheck"] = "false"
            if name == "email":
                field.widget.attrs["inputmode"] = "email"

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("该邮箱已注册")
        return email

    def clean_manor_name(self):
        from gameplay.services.manor.core import is_manor_name_available, validate_manor_name

        name = (self.cleaned_data.get("manor_name") or "").strip()
        is_valid, error_msg = validate_manor_name(name)
        if not is_valid:
            raise forms.ValidationError(error_msg)
        if not is_manor_name_available(name):
            raise forms.ValidationError("该庄园名称已被使用")
        return name


class EmailVerificationRecoveryForm(forms.Form):
    email = forms.EmailField(label="注册邮箱", required=True)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["email"].widget.attrs.update(
            {
                "class": "input",
                "placeholder": "注册邮箱…",
                "autocomplete": "email",
                "spellcheck": "false",
                "inputmode": "email",
            },
        )

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()


class LoginForm(AuthenticationForm):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        autocomplete_by_name = {"username": "username", "password": "current-password"}
        for name, field in self.fields.items():
            field.widget.attrs.update(
                {
                    "class": "input",
                    "placeholder": f"{field.label}…",
                    "autocomplete": autocomplete_by_name.get(name, "off"),
                },
            )
            if name == "username":
                field.widget.attrs["spellcheck"] = "false"
