"""Validators for season YAML configs whose schema lives in the season domain layer."""

from __future__ import annotations

from typing import Any, Callable

from django.core.exceptions import ImproperlyConfigured

from .base import ValidationResult

DOMAIN_VALIDATION_ERRORS = (ImproperlyConfigured, ValueError, KeyError, TypeError, AttributeError)


def _run_domain_validator(validator: Callable[[Any], Any], data: Any, *, file: str) -> ValidationResult:
    result = ValidationResult()
    try:
        validator(data)
    except DOMAIN_VALIDATION_ERRORS as exc:
        result.add(file, "<root>", str(exc) or exc.__class__.__name__)
    return result


def validate_season_rules(data: Any, *, file: str = "season_rules.yaml") -> ValidationResult:
    from gameplay.services.season.catalog import validate_rules

    return _run_domain_validator(validate_rules, data, file=file)


def validate_season_campaign(data: Any, *, file: str = "season_campaign.yaml") -> ValidationResult:
    from gameplay.services.season.campaign_rules import validate_campaign_rules

    return _run_domain_validator(validate_campaign_rules, data, file=file)
