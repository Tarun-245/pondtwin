"""Physics tests.

These exist to catch the class of bug the old model shipped with: oxygen
appearing from nowhere, aerators working against saturation, and depth having
no effect on anything.
"""

import math

from app.engine import (
    EngineOptions,
    PhysicsEngine,
    PondConfig,
    WaterState,
    HourWeather,
    aerator_transfer,
    build_profile,
    do_saturation,
    mean_column_light,
    wind_reaeration,
)


def weather(hours, solar=0.0, wind=1.5, air=28.0):
    return [
        HourWeather(time=f"2026-01-01T{h:02d}:00", air_temperature=air,
                    wind_speed_ms=wind, solar_radiation=solar)
        for h in range(hours)
    ]


def test_saturation_falls_with_temperature():
    assert do_saturation(20) > do_saturation(30) > do_saturation(35)
    # Textbook freshwater values: 9.08 mg/L at 20 C, 8.26 mg/L at 25 C
    assert 9.0 < do_saturation(20) < 9.2
    assert 8.15 < do_saturation(25) < 8.40


def test_saturation_falls_with_salinity():
    assert do_saturation(28, salinity_ppt=20) < do_saturation(28)


def test_aerator_stops_transferring_at_saturation():
    cfg = PondConfig()
    sat = do_saturation(28)
    assert aerator_transfer(cfg, 28, sat * 1.1, sat) == 0.0
    assert aerator_transfer(cfg, 28, 2.0, sat) > 0.0


def test_aerator_transfer_scales_with_deficit():
    cfg = PondConfig()
    sat = do_saturation(28)
    low = aerator_transfer(cfg, 28, 2.0, sat)
    high = aerator_transfer(cfg, 28, 6.0, sat)
    assert low > high


def test_reaeration_is_weaker_in_deep_ponds():
    assert wind_reaeration(3.0, 1.0) > wind_reaeration(3.0, 3.0)


def test_light_attenuates_with_depth_and_turbidity():
    clear = mean_column_light(800, 0.3, 2.0)
    murky = mean_column_light(800, 2.0, 2.0)
    assert murky < clear < 800


def test_profile_conserves_mass():
    """Stratification redistributes oxygen; it must not create any."""
    for strength in (0.0, 1.0, 3.5):
        layers = build_profile(6.0, strength, 2.0, n_layers=12)
        mean = sum(l["dissolved_oxygen"] for l in layers) / len(layers)
        assert math.isclose(mean, 6.0, abs_tol=0.05)


def test_profile_puts_more_oxygen_at_the_surface():
    layers = build_profile(6.0, 2.0, 2.4)
    assert layers[0]["dissolved_oxygen"] > layers[-1]["dissolved_oxygen"]


def test_do_never_exceeds_saturation_ceiling():
    cfg = PondConfig(stock_count=0)
    run = PhysicsEngine().run(
        cfg, WaterState(dissolved_oxygen=6.0, water_temperature=28.0),
        weather(24, solar=900, wind=4.0),
        EngineOptions(aerator_schedule=[True] * 24),
    )
    for row in run["results"]:
        assert row["mean_do"] <= row["saturation"] * 1.6


def test_night_depletes_oxygen():
    cfg = PondConfig(stock_count=6000, avg_weight_g=200)
    run = PhysicsEngine().run(
        cfg, WaterState(dissolved_oxygen=7.0, water_temperature=30.0),
        weather(10, solar=0.0, wind=0.4),
        EngineOptions(),
    )
    assert run["results"][-1]["mean_do"] < 7.0


def test_aeration_improves_the_outcome():
    cfg = PondConfig(stock_count=8000, avg_weight_g=180, depth_m=2.5)
    initial = WaterState(dissolved_oxygen=5.0, water_temperature=31.0)
    w = weather(12, solar=0.0, wind=0.3)

    off = PhysicsEngine().run(cfg, initial, w, EngineOptions())
    on = PhysicsEngine().run(cfg, initial, w,
                             EngineOptions(aerator_schedule=[True] * 12))
    assert on["lowest_do"] > off["lowest_do"]


def test_depth_changes_behaviour():
    """The old model ignored depth entirely. It must not any more."""
    initial = WaterState(dissolved_oxygen=6.0, water_temperature=30.0)
    w = weather(12, solar=700, wind=1.0)
    shallow = PhysicsEngine().run(PondConfig(depth_m=0.8), initial, w, EngineOptions())
    deep = PhysicsEngine().run(PondConfig(depth_m=3.0), initial, w, EngineOptions())
    assert abs(shallow["results"][-1]["water_temperature"]
               - deep["results"][-1]["water_temperature"]) > 0.5


def test_horizon_length_matches_request():
    run = PhysicsEngine().run(PondConfig(), WaterState(), weather(18), EngineOptions())
    assert len(run["results"]) == 18
    assert run["results"][-1]["hour"] == 18
