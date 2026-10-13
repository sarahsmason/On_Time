from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from on_time import geo, server


# -- geo -----------------------------------------------------------------------


def test_find_city_exact_match_ranked_by_population():
    places = geo.find_cities("oswego")
    assert [p.label for p in places] == ["Oswego, IL", "Oswego, NY"]


def test_find_city_with_region():
    [ny] = geo.find_cities("Oswego", region="ny")
    assert ny.timezone == "America/New_York"
    assert round(ny.latitude, 1) == 43.5


def test_timezone_at_coordinates():
    assert geo.timezone_at(43.455, -76.51) == "America/New_York"
    assert geo.timezone_at(47.61, -122.33) == "America/Los_Angeles"


# -- set_location / get_location -----------------------------------------------


def test_location_not_set_initially():
    assert server.get_location()["status"] == "not_set"


def test_ambiguous_city_asks_instead_of_guessing():
    result = server.set_location(city="Oswego")
    assert result["status"] == "needs_clarification"
    assert result["candidates"] == ["Oswego, IL", "Oswego, NY"]
    assert server.get_location()["status"] == "not_set"


def test_city_with_region_is_saved():
    result = server.set_location(city="Oswego", region="NY")
    assert result["status"] == "set"
    loc = server.get_location()["location"]
    assert loc["name"] == "Oswego, NY"
    assert loc["timezone"] == "America/New_York"
    assert loc["source"] == "user"


def test_unique_city_needs_no_region():
    result = server.set_location(city="Albuquerque")
    assert result["location"]["timezone"] == "America/Denver"


def test_device_coordinates_derive_timezone():
    result = server.set_location(latitude=47.61, longitude=-122.33, source="device", place_name="Seattle home")
    loc = result["location"]
    assert loc == {
        "name": "Seattle home",
        "latitude": 47.61,
        "longitude": -122.33,
        "timezone": "America/Los_Angeles",
        "source": "device",
    }


def test_unknown_city_is_a_readable_error():
    with pytest.raises(ToolError, match="couldn't find a city"):
        server.set_location(city="Nowhereville")


def test_missing_arguments_is_a_readable_error():
    with pytest.raises(ToolError, match="city name"):
        server.set_location()


# -- lights --------------------------------------------------------------------


def test_default_lights_exist_and_start_off():
    lights = server.list_lights()["lights"]
    assert {l["id"] for l in lights} == {"bedroom", "living_room", "kitchen", "porch"}
    assert not any(l["is_on"] for l in lights)


def test_set_light_updates_state_and_logs_adjustment(fresh_home):
    ny = ZoneInfo("America/New_York")
    server.set_location(city="Oswego", region="NY")
    fresh_home.clock.travel_to(datetime(2026, 10, 5, 18, 10, tzinfo=ny))

    result = server.set_light(light_id="living_room", action="on")
    assert result["light"]["is_on"] is True
    assert result["logged_at"].startswith("2026-10-05T18:10")

    [adj] = fresh_home.repo.list_adjustments(light_id="living_room")
    assert adj.action == "on"
    assert adj.at.utcoffset() == datetime(2026, 10, 5, tzinfo=ny).utcoffset()

    server.set_light(light_id="living_room", action="off")
    assert fresh_home.repo.get_light("living_room").is_on is False
    assert len(fresh_home.repo.list_adjustments(light_id="living_room")) == 2


def test_unknown_light_lists_valid_ids():
    with pytest.raises(ToolError, match="Valid lights: bedroom, kitchen, living_room, porch"):
        server.set_light(light_id="garage", action="on")
