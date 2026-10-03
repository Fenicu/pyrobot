from typing import get_args

import pytest
from pydantic import ValidationError

from app.engine.parsing.trips import VEHICLES
from app.engine.settings import FeaturesSection, Settings, TripsSection, TripVehicle, apply_patch
from app.engine.trips import trip_vehicles


def test_defaults_trips_on_in_owner_order() -> None:
    cfg = Settings()
    assert cfg.features.trips is True
    assert cfg.trips.vehicles == ("car", "tram", "sled", "bike", "scooter", "tractor")
    assert trip_vehicles(cfg) == ("car", "tram", "sled", "bike", "scooter", "tractor")


def test_settings_know_exactly_the_catalogue_vehicles() -> None:
    assert set(get_args(TripVehicle)) == set(VEHICLES)


def test_removed_vehicle_is_not_ridden_and_order_is_kept() -> None:
    cfg = Settings(trips=TripsSection(vehicles=("bike", "car")))
    assert trip_vehicles(cfg) == ("bike", "car")
    assert trip_vehicles(Settings(trips=TripsSection(vehicles=()))) == ()


def test_feature_off_means_no_vehicles() -> None:
    assert trip_vehicles(Settings(features=FeaturesSection(trips=False))) == ()


@pytest.mark.parametrize("vehicles", [("car", "car"), ("car", "plane")])
def test_vehicles_unique_and_known(vehicles: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError):
        TripsSection.model_validate({"vehicles": list(vehicles)})


def test_patch_and_old_settings_without_section() -> None:
    patched = apply_patch(Settings(), {"trips": {"vehicles": ["tram", "car"]}})
    assert patched.trips.vehicles == ("tram", "car")
    old = Settings.model_validate({"engine": {"mode": "live"}})
    assert old.features.trips is True and old.trips.vehicles[0] == "car"
