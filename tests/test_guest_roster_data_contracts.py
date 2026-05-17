from guests.management.commands.load_guest_templates import Command


def test_diaochan_is_purple_guest_in_default_roster() -> None:
    payload = Command()._load_heroes_payload("")

    green_keys = {entry["key"] for entry in payload.get("green", [])}
    purple_keys = {entry["key"] for entry in payload.get("purple", [])}
    diaochan = next(entry for entry in payload.get("purple", []) if entry["key"] == "hist_sljnbc_0425")

    assert "hist_sljnbc_0425" not in green_keys
    assert "hist_sljnbc_0425" in purple_keys
    assert diaochan["growth_range"] == [6, 12]
