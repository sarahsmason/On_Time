"""Offline place lookup: city name -> coordinates + timezone, coordinates -> timezone.

Uses the GeoNames city list bundled with `geonamescache` (cities with population >= 15,000) and
`timezonefinder`, so no external service is called. US places read "Oswego, NY", others
"London, United Kingdom". ZIP codes are not resolved here: the agent turns them into a city
name before calling `set_location`.
"""

from dataclasses import dataclass
from functools import cache

import geonamescache
from timezonefinder import TimezoneFinder


@dataclass(frozen=True)
class Place:
    name: str
    region: str  # US state / admin-1 code, e.g. "NY" (GeoNames code elsewhere, e.g. "ENG", "02")
    country: str  # ISO country code, e.g. "US"
    latitude: float
    longitude: float
    timezone: str
    population: int

    @property
    def label(self) -> str:
        """'Oswego, NY' in the US; 'London, United Kingdom' elsewhere."""
        return f"{self.name}, {self.region}" if self.country == "US" else f"{self.name}, {country_name(self.country)}"


US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "district of columbia": "DC", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI",
    "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY",
}  # fmt: skip
# Everyday names for countries, beyond ISO codes and official names ("UK", "England", "USA").
COUNTRY_ALIASES = {
    "UK": "GB", "U.K.": "GB", "BRITAIN": "GB", "GREAT BRITAIN": "GB",
    "ENGLAND": "GB", "SCOTLAND": "GB", "WALES": "GB", "NORTHERN IRELAND": "GB",
    "USA": "US", "U.S.": "US", "U.S.A.": "US", "UNITED STATES": "US", "AMERICA": "US",
}  # fmt: skip
# Australian states and territories -> GeoNames admin-1 codes ("Sydney, NSW").
AU_STATES = {"ACT": "01", "NSW": "02", "NT": "03", "QLD": "04", "SA": "05", "TAS": "06", "VIC": "07", "WA": "08"}


@cache
def _countries() -> dict[str, str]:
    return {code: c["name"] for code, c in geonamescache.GeonamesCache().get_countries().items()}


def country_name(code: str) -> str:
    return _countries().get(code, code)


@cache
def country_words() -> dict[str, str]:
    """Upper-case country name or alias -> ISO code ("AUSTRALIA" -> "AU", "UK" -> "GB")."""
    return {**{name.upper(): code for code, name in _countries().items()}, **COUNTRY_ALIASES}


def _region_matches(region_key: str, place: Place) -> bool:
    return (
        region_key in (place.region.upper(), place.country.upper())
        or (place.country == "US" and US_STATES.get(region_key.lower()) == place.region)
        or country_words().get(region_key) == place.country
        or (place.country == "AU" and AU_STATES.get(region_key) == place.region)
    )


@cache
def _cities() -> tuple[Place, ...]:
    raw = geonamescache.GeonamesCache().get_cities().values()
    return tuple(
        Place(
            name=c["name"],
            region=c["admin1code"],
            country=c["countrycode"],
            latitude=c["latitude"],
            longitude=c["longitude"],
            timezone=c["timezone"],
            population=c["population"],
        )
        for c in raw
    )


@cache
def _tz_finder() -> TimezoneFinder:
    return TimezoneFinder()


def find_cities(name: str, region: str | None = None, limit: int = 5) -> list[Place]:
    """Exact (case-insensitive) city-name matches, most populous first.

    `region` narrows by US state code or name ("NY", "New York"), country code or name ("GB", "UK",
    "Australia"), or Australian state ("NSW").
    """
    wanted = name.strip().casefold()
    region_key = region.strip().upper() if region else None
    matches = [
        p for p in _cities() if p.name.casefold() == wanted and (region_key is None or _region_matches(region_key, p))
    ]
    return sorted(matches, key=lambda p: p.population, reverse=True)[:limit]


def timezone_at(latitude: float, longitude: float) -> str | None:
    return _tz_finder().timezone_at(lat=latitude, lng=longitude)
