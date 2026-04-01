from __future__ import annotations

from typing import Any, TypedDict


class EnemyGuestConfig(TypedDict):
    key: str
    skills: list[str] | None
    label: str | None


def normalize_enemy_guest_configs(raw: Any) -> list[EnemyGuestConfig]:
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple, set)):
        raise AssertionError(f"invalid mission guest configs: {raw!r}")

    normalized: list[EnemyGuestConfig] = []
    for entry in raw:
        if isinstance(entry, str):
            key = entry.strip()
            if not key:
                raise AssertionError(f"invalid mission guest config entry: {entry!r}")
            normalized.append({"key": key, "skills": None, "label": None})
            continue

        if not isinstance(entry, dict):
            raise AssertionError(f"invalid mission guest config entry: {entry!r}")

        raw_key = entry.get("key")
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise AssertionError(f"invalid mission guest config entry: {entry!r}")

        raw_skills = entry.get("skills")
        skills: list[str] | None = None
        if raw_skills is not None:
            if not isinstance(raw_skills, (list, tuple, set)):
                raise AssertionError(f"invalid mission guest config skills: {raw_skills!r}")
            skills = []
            for skill in raw_skills:
                if not isinstance(skill, str) or not skill.strip():
                    raise AssertionError(f"invalid mission guest config skills entry: {skill!r}")
                skills.append(skill.strip())
            if not skills:
                skills = None

        raw_label = entry.get("label")
        label: str | None = None
        if raw_label is not None:
            if not isinstance(raw_label, str) or not raw_label.strip():
                raise AssertionError(f"invalid mission guest config label: {raw_label!r}")
            label = raw_label.strip()

        normalized.append({"key": raw_key.strip(), "skills": skills, "label": label})

    return normalized
