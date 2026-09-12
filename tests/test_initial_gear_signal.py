import pytest

from guests.models import GearItem, GearSlot, GearTemplate


@pytest.mark.django_db
def test_new_user_does_not_receive_the_first_gear_template(django_user_model):
    GearTemplate.objects.create(
        key="deprecated_first_gear_test",
        name="不应自动发放的装备",
        slot=GearSlot.WEAPON,
    )

    user = django_user_model.objects.create_user(username="no_initial_gear", password="pass12345")

    assert not GearItem.objects.filter(manor=user.manor).exists()
