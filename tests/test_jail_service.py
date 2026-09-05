"""Tests for jail/prisoner service logic."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import gameplay.services.jail as jail_service
from core.exceptions import ItemInsufficientError

pytestmark = pytest.mark.django_db


class _SuccessfulRecruitmentRng:
    def __init__(self):
        self.first_randint = True

    def randint(self, start, end):
        if self.first_randint:
            self.first_randint = False
            return 1
        return 0 if start <= 0 <= end else start

    def choice(self, values):
        return values[0]


# ============ list_held_prisoners tests ============


def test_list_held_prisoners_returns_empty_list_when_no_prisoners():
    """Test that empty list is returned when manor has no prisoners."""
    manor = SimpleNamespace(pk=1)

    with patch.object(jail_service.JailPrisoner, "objects") as mock_qs:
        mock_qs.filter.return_value.select_related.return_value.order_by.return_value = []

        result = jail_service.list_held_prisoners(manor)

        assert result == []
        assert mock_qs.filter.call_count == 2


# ============ list_oath_bonds tests ============


def test_list_oath_bonds_returns_empty_list_when_no_bonds():
    """Test that empty list is returned when manor has no oath bonds."""
    manor = SimpleNamespace(pk=1)

    with patch.object(jail_service.OathBond, "objects") as mock_qs:
        mock_qs.filter.return_value.select_related.return_value.order_by.return_value = []

        result = jail_service.list_oath_bonds(manor)

        assert result == []


# ============ add_oath_bond tests ============


@patch("gameplay.services.jail.Manor")
def test_add_oath_bond_raises_when_guest_not_found(mock_manor_model):
    """Test that OathGuestNotFoundError is raised when guest doesn't exist."""
    manor = SimpleNamespace(pk=1, oath_capacity=5)
    mock_manor_model.objects.select_for_update.return_value.get.return_value = manor

    with patch.object(jail_service.Guest, "objects") as mock_qs:
        mock_qs.select_for_update.return_value.select_related.return_value.filter.return_value.first.return_value = None

        with pytest.raises(jail_service.OathGuestNotFoundError, match="门客不存在"):
            jail_service.add_oath_bond(manor, guest_id=999)


@patch("gameplay.services.jail.Manor")
def test_add_oath_bond_raises_when_capacity_full(mock_manor_model):
    """Test that OathCapacityFullError is raised when oath capacity is full."""
    manor = SimpleNamespace(pk=1, oath_capacity=2)
    mock_manor_model.objects.select_for_update.return_value.get.return_value = manor

    guest = SimpleNamespace(pk=10, status=jail_service.GuestStatus.IDLE)

    with patch.object(jail_service.Guest, "objects") as mock_guest_qs:
        mock_guest_qs.select_for_update.return_value.select_related.return_value.filter.return_value.first.return_value = (
            guest
        )

        with patch.object(jail_service.OathBond, "objects") as mock_bond_qs:
            mock_bond_qs.filter.return_value.count.return_value = 2  # At capacity

            with pytest.raises(jail_service.OathCapacityFullError, match="结义人数已满"):
                jail_service.add_oath_bond(manor, guest_id=10)


@patch("gameplay.services.jail.Manor")
def test_add_oath_bond_raises_when_already_bonded(mock_manor_model):
    """Test that OathBondAlreadyExistsError is raised when guest is already bonded."""
    manor = SimpleNamespace(pk=1, oath_capacity=5)
    mock_manor_model.objects.select_for_update.return_value.get.return_value = manor

    guest = SimpleNamespace(pk=10, status=jail_service.GuestStatus.IDLE)
    existing_bond = SimpleNamespace(pk=1)

    with patch.object(jail_service.Guest, "objects") as mock_guest_qs:
        mock_guest_qs.select_for_update.return_value.select_related.return_value.filter.return_value.first.return_value = (
            guest
        )

        with patch.object(jail_service.OathBond, "objects") as mock_bond_qs:
            mock_bond_qs.filter.return_value.count.return_value = 1
            mock_bond_qs.get_or_create.return_value = (existing_bond, False)  # Not created

            with pytest.raises(jail_service.OathBondAlreadyExistsError, match="该门客已结义"):
                jail_service.add_oath_bond(manor, guest_id=10)


@patch("gameplay.services.jail.Manor")
def test_add_oath_bond_rejects_non_idle_guest(mock_manor_model):
    manor = SimpleNamespace(pk=1, oath_capacity=5)
    mock_manor_model.objects.select_for_update.return_value.get.return_value = manor

    guest = SimpleNamespace(pk=10, status=jail_service.GuestStatus.DEPLOYED, display_name="测试门客")

    with patch.object(jail_service.Guest, "objects") as mock_guest_qs:
        mock_guest_qs.select_for_update.return_value.select_related.return_value.filter.return_value.first.return_value = (
            guest
        )

        with pytest.raises(jail_service.GuestNotIdleError):
            jail_service.add_oath_bond(manor, guest_id=10)


# ============ remove_oath_bond tests ============


def test_remove_oath_bond_returns_deleted_count():
    """Test that remove_oath_bond returns correct deleted count."""
    manor = SimpleNamespace(pk=1)

    with patch.object(jail_service.OathBond, "objects") as mock_qs:
        mock_qs.filter.return_value.delete.return_value = (1, {})

        result = jail_service.remove_oath_bond(manor, guest_id=10)

        assert result == 1


def test_remove_oath_bond_returns_zero_when_not_found():
    """Test that remove_oath_bond returns 0 when bond doesn't exist."""
    manor = SimpleNamespace(pk=1)

    with patch.object(jail_service.OathBond, "objects") as mock_qs:
        mock_qs.filter.return_value.delete.return_value = (0, {})

        result = jail_service.remove_oath_bond(manor, guest_id=999)

        assert result == 0


@patch("gameplay.services.jail.Guest")
def test_remove_oath_bond_rejects_non_idle_guest(mock_guest_model):
    manor = SimpleNamespace(pk=1)
    guest = SimpleNamespace(status=jail_service.GuestStatus.WORKING, display_name="测试门客")
    mock_guest_model.objects.select_for_update.return_value.filter.return_value.first.return_value = guest

    with pytest.raises(jail_service.GuestNotIdleError):
        jail_service.remove_oath_bond(manor, guest_id=10)


# ============ release_prisoner tests ============


def test_release_prisoner_raises_when_not_found():
    """Test that PrisonerUnavailableError is raised when prisoner doesn't exist."""
    manor = SimpleNamespace(pk=1)

    with patch.object(jail_service.JailPrisoner, "objects") as mock_qs:
        mock_qs.select_for_update.return_value.filter.return_value.first.return_value = None

        with pytest.raises(jail_service.PrisonerUnavailableError, match="囚徒不存在或已处理"):
            jail_service.release_prisoner(manor, prisoner_id=999)


def test_release_prisoner_sets_status_to_released():
    """Test that release_prisoner sets status to RELEASED."""
    manor = SimpleNamespace(pk=1)
    prisoner = MagicMock()
    prisoner.status = jail_service.JailPrisoner.Status.HELD

    with patch.object(jail_service.JailPrisoner, "objects") as mock_qs:
        mock_qs.select_for_update.return_value.filter.return_value.first.return_value = prisoner

        result = jail_service.release_prisoner(manor, prisoner_id=1)

        assert result.status == jail_service.JailPrisoner.Status.RELEASED
        prisoner.save.assert_called_once_with(update_fields=["status"])


# ============ draw_pie tests ============


def test_draw_pie_maps_to_lazy_bribe_interaction():
    manor = SimpleNamespace(pk=1)
    prisoner = MagicMock()
    result = SimpleNamespace(prisoner=prisoner, heart_delta=-7)

    with patch.object(jail_service, "interact_prisoner", return_value=result) as mock_interact:
        returned = jail_service.draw_pie(manor, prisoner_id=1)

    mock_interact.assert_called_once_with(manor, 1, method="bribe", lazy_observe=True)
    assert returned is prisoner
    assert returned._reduction == 7
    assert returned._persuasion_result is result


def test_draw_pie_does_not_report_negative_reduction_for_bad_outcome():
    manor = SimpleNamespace(pk=1)
    prisoner = MagicMock()
    result = SimpleNamespace(prisoner=prisoner, heart_delta=3)

    with patch.object(jail_service, "interact_prisoner", return_value=result):
        returned = jail_service.draw_pie(manor, prisoner_id=1)

    assert returned._reduction == 0


def test_draw_pie_propagates_new_interaction_business_errors():
    manor = SimpleNamespace(pk=1)
    with patch.object(jail_service, "interact_prisoner", side_effect=jail_service.JailError("金条不足")):
        with pytest.raises(jail_service.JailError, match="金条不足"):
            jail_service.draw_pie(manor, prisoner_id=1)


# ============ recruit_prisoner tests ============


@patch("gameplay.services.jail.Manor")
def test_recruit_prisoner_raises_when_not_found(mock_manor_model):
    """Test that PrisonerNotFoundError is raised when prisoner doesn't exist."""
    manor = SimpleNamespace(pk=1)
    mock_manor_model.objects.select_for_update.return_value.get.return_value = manor

    with patch.object(jail_service.JailPrisoner, "objects") as mock_qs:
        mock_qs.select_for_update.return_value.select_related.return_value.filter.return_value.first.return_value = None

        with pytest.raises(jail_service.PrisonerNotFoundError, match="囚徒不存在"):
            jail_service.recruit_prisoner(manor, prisoner_id=999)


@patch("gameplay.services.jail.Manor")
def test_recruit_prisoner_raises_when_already_processed(mock_manor_model):
    """Test that PrisonerAlreadyHandledError is raised when prisoner is already processed."""
    manor = SimpleNamespace(pk=1)
    mock_manor_model.objects.select_for_update.return_value.get.return_value = manor

    prisoner = MagicMock()
    prisoner.status = jail_service.JailPrisoner.Status.RELEASED

    with patch.object(jail_service.JailPrisoner, "objects") as mock_qs:
        mock_qs.select_for_update.return_value.select_related.return_value.filter.return_value.first.return_value = (
            prisoner
        )

        with pytest.raises(jail_service.PrisonerAlreadyHandledError, match="囚徒已处理"):
            jail_service.recruit_prisoner(manor, prisoner_id=1)


@patch("gameplay.services.jail.Manor")
def test_recruit_prisoner_raises_when_loyalty_too_high(mock_manor_model):
    """Test that JailError is raised when prisoner loyalty is too high."""
    manor = SimpleNamespace(pk=1)
    mock_manor_model.objects.select_for_update.return_value.get.return_value = manor

    prisoner = MagicMock()
    prisoner.status = jail_service.JailPrisoner.Status.HELD
    prisoner.loyalty = 50  # Above threshold (30)

    with patch.object(jail_service.JailPrisoner, "objects") as mock_qs:
        mock_qs.select_for_update.return_value.select_related.return_value.filter.return_value.first.return_value = (
            prisoner
        )

        with pytest.raises(jail_service.JailError, match="忠诚度过高"):
            jail_service.recruit_prisoner(manor, prisoner_id=1)


@patch("gameplay.services.jail.Manor")
def test_recruit_prisoner_raises_when_guest_capacity_full(mock_manor_model):
    """Test that GuestCapacityFullError is raised when guest capacity is full."""
    # Create a real Mock that can handle property access
    manor = MagicMock()
    manor.guest_capacity = 10
    manor.guests.count.return_value = 10  # At capacity
    mock_manor_model.objects.select_for_update.return_value.get.return_value = manor

    prisoner = MagicMock()
    prisoner.status = jail_service.JailPrisoner.Status.HELD
    prisoner.loyalty = 20  # Below threshold

    with patch.object(jail_service.JailPrisoner, "objects") as mock_qs:
        mock_qs.select_for_update.return_value.select_related.return_value.filter.return_value.first.return_value = (
            prisoner
        )

        with pytest.raises(jail_service.GuestCapacityFullError):
            jail_service.recruit_prisoner(manor, prisoner_id=1)


@patch("gameplay.services.jail.Manor")
def test_recruit_prisoner_raises_when_gold_insufficient(mock_manor_model):
    """Test that JailError is raised when gold bars are insufficient."""
    manor = MagicMock()
    manor.guest_capacity = 10
    manor.guests.count.return_value = 5
    manor.guests.filter.return_value.exists.return_value = False
    mock_manor_model.objects.select_for_update.return_value.get.return_value = manor

    prisoner = MagicMock()
    prisoner.status = jail_service.JailPrisoner.Status.HELD
    prisoner.loyalty = 20
    prisoner.guest_template = SimpleNamespace(key="hist_sljnbc_0013", name="吕布")

    with patch.object(jail_service.JailPrisoner, "objects") as mock_qs:
        mock_qs.select_for_update.return_value.select_related.return_value.filter.return_value.first.return_value = (
            prisoner
        )

        with patch.object(jail_service.JailInteractionLog, "objects") as mock_logs:
            mock_logs.filter.return_value.exists.return_value = False
            with patch.object(
                jail_service,
                "consume_available_gold_bars_locked",
                side_effect=ItemInsufficientError("金条", 1, 0),
            ):
                with patch.object(jail_service, "get_item_quantity", return_value=0):
                    with pytest.raises(jail_service.JailError, match="金条不足"):
                        jail_service.recruit_prisoner(manor, prisoner_id=1)


@patch("gameplay.services.jail.Manor")
def test_recruit_prisoner_rejects_duplicate_standard_guest(mock_manor_model):
    manor = MagicMock()
    manor.guest_capacity = 10
    manor.guests.count.return_value = 2
    manor.guests.filter.return_value.exists.return_value = True
    mock_manor_model.objects.select_for_update.return_value.get.return_value = manor

    prisoner = MagicMock()
    prisoner.status = jail_service.JailPrisoner.Status.HELD
    prisoner.loyalty = 20
    prisoner.guest_template = SimpleNamespace(
        key="hist_sljnbc_0013",
        name="吕布",
        default_gender="male",
        default_morality=60,
        base_attack=100,
        base_intellect=80,
        base_defense=90,
        base_agility=70,
        base_luck=50,
        base_hp=1000,
        rarity="purple",
        archetype="military",
    )
    prisoner.original_guest_name = ""

    with patch.object(jail_service.JailPrisoner, "objects") as mock_qs:
        mock_qs.select_for_update.return_value.select_related.return_value.filter.return_value.first.return_value = (
            prisoner
        )

        with patch.object(jail_service, "get_item_quantity", return_value=1):
            with patch.object(jail_service, "apply_recruitment_variance") as mock_variance:
                mock_variance.return_value = {
                    "force": 100,
                    "intellect": 80,
                    "defense": 90,
                    "agility": 70,
                    "luck": 50,
                }
                with patch.object(jail_service.Guest.objects, "create", return_value=MagicMock()):
                    with patch.object(jail_service, "grant_template_skills"):
                        with patch.object(jail_service, "consume_available_gold_bars_locked") as mock_consume:
                            with pytest.raises(jail_service.JailError, match="不可重复招募"):
                                jail_service.recruit_prisoner(manor, prisoner_id=1)

    mock_consume.assert_not_called()


@patch("gameplay.services.jail.Manor")
def test_recruit_prisoner_allows_duplicate_repeatable_standard_guest(mock_manor_model, monkeypatch):
    manor = MagicMock()
    manor.guest_capacity = 10
    manor.guests.count.return_value = 2
    manor.guests.filter.return_value.exists.return_value = True
    mock_manor_model.objects.select_for_update.return_value.get.return_value = manor
    monkeypatch.setattr(
        jail_service,
        "PRISONER_RECRUIT_REPEATABLE_TEMPLATE_GROUPS",
        {"hist_sljnbc_0013": ("hist_sljnbc_0013",)},
    )

    prisoner = MagicMock()
    prisoner.status = jail_service.JailPrisoner.Status.HELD
    prisoner.loyalty = 20
    prisoner.guest_template = SimpleNamespace(
        key="hist_sljnbc_0013",
        name="吕布",
        default_gender="male",
        default_morality=60,
        base_attack=100,
        base_intellect=80,
        base_defense=90,
        base_agility=70,
        base_luck=50,
        base_hp=1000,
        rarity="purple",
        archetype="military",
    )
    prisoner.original_guest_name = ""

    created_guest = MagicMock()
    auto_training_guests = []
    monkeypatch.setattr(
        jail_service,
        "ensure_auto_training",
        lambda guest: auto_training_guests.append(guest),
        raising=False,
    )

    with patch.object(jail_service.JailPrisoner, "objects") as mock_qs:
        mock_qs.select_for_update.return_value.select_related.return_value.filter.return_value.first.return_value = (
            prisoner
        )

        with patch.object(jail_service, "get_item_quantity", return_value=1):
            with patch.object(jail_service, "apply_recruitment_variance") as mock_variance:
                mock_variance.return_value = {
                    "force": 100,
                    "intellect": 80,
                    "defense": 90,
                    "agility": 70,
                    "luck": 50,
                }
                with patch.object(jail_service.Guest.objects, "create", return_value=created_guest):
                    with patch.object(jail_service, "grant_template_skills"):
                        with patch.object(jail_service, "consume_available_gold_bars_locked") as mock_consume:
                            with patch.object(jail_service.JailInteractionLog, "objects") as mock_logs:
                                mock_logs.filter.return_value.exists.return_value = False
                                result = jail_service.recruit_prisoner(
                                    manor,
                                    prisoner_id=1,
                                    rng=_SuccessfulRecruitmentRng(),
                                )

    assert result.recruited is True
    assert result.guest is created_guest
    mock_consume.assert_called_once()
    assert auto_training_guests == [created_guest]


@pytest.mark.parametrize(
    ("template_key", "template_name"),
    [
        ("pubayi_blue", "蒲巴乙"),
        ("orig_edward_blue", "爱德华"),
    ],
)
@patch("gameplay.services.jail.Manor")
def test_recruit_prisoner_allows_duplicate_configured_repeatable_guest(
    mock_manor_model, template_key, template_name, monkeypatch
):
    manor = MagicMock()
    manor.guest_capacity = 10
    manor.guests.count.return_value = 2
    manor.guests.filter.return_value.exists.return_value = True
    mock_manor_model.objects.select_for_update.return_value.get.return_value = manor

    prisoner = MagicMock()
    prisoner.status = jail_service.JailPrisoner.Status.HELD
    prisoner.loyalty = 20
    prisoner.guest_template = SimpleNamespace(
        key=template_key,
        name=template_name,
        default_gender="male",
        default_morality=60,
        base_attack=100,
        base_intellect=80,
        base_defense=90,
        base_agility=70,
        base_luck=50,
        base_hp=1000,
        rarity="blue",
        archetype="military",
    )
    prisoner.original_guest_name = ""

    created_guest = MagicMock()
    auto_training_guests = []
    monkeypatch.setattr(
        jail_service,
        "ensure_auto_training",
        lambda guest: auto_training_guests.append(guest),
    )

    with patch.object(jail_service.JailPrisoner, "objects") as mock_qs:
        mock_qs.select_for_update.return_value.select_related.return_value.filter.return_value.first.return_value = (
            prisoner
        )

        with patch.object(jail_service, "get_item_quantity", return_value=1):
            with patch.object(jail_service, "apply_recruitment_variance") as mock_variance:
                mock_variance.return_value = {
                    "force": 100,
                    "intellect": 80,
                    "defense": 90,
                    "agility": 70,
                    "luck": 50,
                }
                with patch.object(jail_service.Guest.objects, "create", return_value=created_guest):
                    with patch.object(jail_service, "grant_template_skills"):
                        with patch.object(jail_service, "consume_available_gold_bars_locked") as mock_consume:
                            with patch.object(jail_service.JailInteractionLog, "objects") as mock_logs:
                                mock_logs.filter.return_value.exists.return_value = False
                                result = jail_service.recruit_prisoner(
                                    manor,
                                    prisoner_id=1,
                                    rng=_SuccessfulRecruitmentRng(),
                                )

    assert result.recruited is True
    assert result.guest is created_guest
    mock_consume.assert_called_once()
    assert auto_training_guests == [created_guest]


@patch("gameplay.services.jail.Manor")
def test_recruit_prisoner_rejects_duplicate_unique_original_guest(mock_manor_model):
    manor = MagicMock()
    manor.guest_capacity = 10
    manor.guests.count.return_value = 2
    manor.guests.filter.return_value.exists.return_value = True
    mock_manor_model.objects.select_for_update.return_value.get.return_value = manor

    prisoner = MagicMock()
    prisoner.status = jail_service.JailPrisoner.Status.HELD
    prisoner.loyalty = 20
    prisoner.guest_template = SimpleNamespace(key="orig_zhu_yingtai", name="祝英台")

    with patch.object(jail_service.JailPrisoner, "objects") as mock_qs:
        mock_qs.select_for_update.return_value.select_related.return_value.filter.return_value.first.return_value = (
            prisoner
        )

        with patch.object(jail_service, "consume_available_gold_bars_locked") as mock_consume:
            with pytest.raises(jail_service.JailError, match="不可重复招募"):
                jail_service.recruit_prisoner(manor, prisoner_id=1)

    mock_consume.assert_not_called()


@patch("gameplay.services.jail.Manor")
def test_recruit_prisoner_rejects_duplicate_panfeng_variant(mock_manor_model):
    manor = MagicMock()
    manor.guest_capacity = 10
    manor.guests.count.return_value = 2
    manor.guests.filter.return_value.exists.return_value = True
    mock_manor_model.objects.select_for_update.return_value.get.return_value = manor

    prisoner = MagicMock()
    prisoner.status = jail_service.JailPrisoner.Status.HELD
    prisoner.loyalty = 20
    prisoner.guest_template = SimpleNamespace(key="hist_sljnbc_0590_blue", name="潘凤")

    with patch.object(jail_service.JailPrisoner, "objects") as mock_qs:
        mock_qs.select_for_update.return_value.select_related.return_value.filter.return_value.first.return_value = (
            prisoner
        )

        with pytest.raises(jail_service.JailError, match="不可重复招募"):
            jail_service.recruit_prisoner(manor, prisoner_id=1)
