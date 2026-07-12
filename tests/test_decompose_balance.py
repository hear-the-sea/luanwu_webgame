from gameplay.services.buildings.decompose_balance import simulate_decompose_means


def test_simulate_decompose_means_is_seeded_and_includes_base_and_chance_rewards():
    config = {
        "base_materials": {"green": {"tong": [2, 2]}},
        "chance_rewards": {"green": {"wood_essence": 0.5}},
    }
    assert simulate_decompose_means(config, "green", rolls=100_000, seed=7) == simulate_decompose_means(
        config, "green", rolls=100_000, seed=7
    )
