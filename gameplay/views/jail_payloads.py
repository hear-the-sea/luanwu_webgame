from __future__ import annotations

from typing import Any


def build_jail_status_payload(manor: Any, prisoners: list[Any]) -> dict[str, object]:
    if prisoners and isinstance(prisoners[0], dict):
        return {
            "capacity": int(getattr(manor, "jail_capacity", 0) or 0),
            "count": len(prisoners),
            "prisoners": prisoners,
        }
    return {
        "capacity": int(getattr(manor, "jail_capacity", 0) or 0),
        "count": len(prisoners),
        "prisoners": [
            {
                "id": prisoner.id,
                "name": prisoner.display_name,
                "template_key": getattr(prisoner.guest_template, "key", ""),
                "rarity": getattr(prisoner.guest_template, "rarity", ""),
                "loyalty": int(prisoner.loyalty),
                "captured_at": prisoner.captured_at.isoformat() if prisoner.captured_at else "",
                "original_manor": getattr(getattr(prisoner, "original_manor", None), "display_name", ""),
            }
            for prisoner in prisoners
        ],
    }


def build_oath_status_payload(manor: Any, bonds: list[Any]) -> dict[str, object]:
    return {
        "capacity": int(getattr(manor, "oath_capacity", 0) or 0),
        "count": len(bonds),
        "bonds": [
            {
                "guest_id": bond.guest_id,
                "name": bond.guest.display_name,
                "template_key": getattr(bond.guest.template, "key", ""),
                "rarity": getattr(bond.guest.template, "rarity", ""),
                "created_at": bond.created_at.isoformat() if bond.created_at else "",
            }
            for bond in bonds
        ],
    }
