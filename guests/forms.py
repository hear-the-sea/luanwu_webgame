from __future__ import annotations

from django import forms

from .models import Guest, RecruitmentPool


class RecruitForm(forms.Form):
    pool = forms.ModelChoiceField(queryset=RecruitmentPool.objects.none(), label="招募卡池")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["pool"].queryset = RecruitmentPool.objects.all()


class EquipForm(forms.Form):
    guest = forms.ModelChoiceField(queryset=Guest.objects.all(), label="门客")
    gear = forms.CharField(label="装备")

    def __init__(self, *args, **kwargs):
        manor = kwargs.pop("manor", None)
        super().__init__(*args, **kwargs)
        if manor:
            self.fields["guest"].queryset = manor.guests.select_related("template")


class AllocateSkillPointsForm(forms.Form):
    ATTRIBUTE_CHOICES = [
        ("force", "武力"),
        ("intellect", "智力"),
        ("defense", "防御"),
        ("agility", "敏捷"),
    ]

    guest = forms.ModelChoiceField(queryset=Guest.objects.none(), widget=forms.HiddenInput())
    attribute = forms.ChoiceField(choices=ATTRIBUTE_CHOICES, label="属性")
    points = forms.IntegerField(min_value=1, label="投入技能点")

    def __init__(self, *args, **kwargs):
        manor = kwargs.pop("manor", None)
        super().__init__(*args, **kwargs)
        if manor:
            self.fields["guest"].queryset = manor.guests.all()

    def clean(self):
        cleaned = super().clean()
        guest = cleaned.get("guest")
        points = cleaned.get("points")
        if guest and points and guest.attribute_points < points:
            raise forms.ValidationError("属性点不足")
        return cleaned
