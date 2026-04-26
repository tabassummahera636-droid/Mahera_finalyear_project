from dataclasses import dataclass
import math
from typing import Any, Dict, Optional
from urllib.parse import quote

import requests


@dataclass
class ApiConfig:
    tomtom_key: str = ""
    openweather_key: str = ""
    geoapify_key: str = ""


class RealtimeApiClient:
    def __init__(self, config: ApiConfig, timeout: int = 10):
        self.config = config
        self.timeout = timeout

    def _get_json(self, url: str, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        try:
            resp = requests.get(url, timeout=self.timeout, headers=headers)
            resp.raise_for_status()
            return {"ok": True, "data": resp.json(), "status": resp.status_code}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "url": url}

    # 1) Weather API
    def get_weather(self, lat: float, lon: float) -> Dict[str, Any]:
        if not self.config.openweather_key:
            url = (
                "https://api.open-meteo.com/v1/forecast"
                f"?latitude={lat}&longitude={lon}&current=temperature_2m,weather_code"
            )
            return self._get_json(url)
        url = (
            f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}"
            f"&appid={self.config.openweather_key}&units=metric"
        )
        return self._get_json(url)

    # 2) Route optimizing API (TomTom calculateRoute)
    def get_tomtom_route(
        self, origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float
    ) -> Dict[str, Any]:
        if not self.config.tomtom_key:
            return {"ok": False, "error": "TOMTOM_API_KEY missing"}
        route_locations = f"{origin_lat},{origin_lon}:{dest_lat},{dest_lon}"
        url = (
            f"https://api.tomtom.com/routing/1/calculateRoute/{route_locations}/json"
            f"?key={self.config.tomtom_key}&traffic=true&maxAlternatives=2&routeType=fastest"
        )
        return self._get_json(url)

    # 3) Traffic data API (TomTom flow segment)
    def get_flow_segment(self, lat: float, lon: float) -> Dict[str, Any]:
        if not self.config.tomtom_key:
            return {"ok": False, "error": "TOMTOM_API_KEY missing"}
        url = (
            f"https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json"
            f"?point={lat},{lon}&key={self.config.tomtom_key}"
        )
        return self._get_json(url)

    # 4) Incident / Accident API (TomTom incidentDetails)
    def get_incidents_bbox(
        self, min_lon: float, min_lat: float, max_lon: float, max_lat: float
    ) -> Dict[str, Any]:
        if not self.config.tomtom_key:
            return {"ok": False, "error": "TOMTOM_API_KEY missing"}
        fields = quote("{incidents{type,geometry{type,coordinates},properties{iconCategory,magnitudeOfDelay}}}")
        bbox = f"{min_lon},{min_lat},{max_lon},{max_lat}"
        url = (
            "https://api.tomtom.com/traffic/services/5/incidentDetails"
            f"?key={self.config.tomtom_key}&bbox={bbox}&fields={fields}&language=en-GB&timeValidityFilter=present"
        )
        return self._get_json(url)

    # 5) Geocoding API (TomTom geocode)
    def geocode(self, query: str) -> Dict[str, Any]:
        if not self.config.tomtom_key:
            return {"ok": False, "error": "TOMTOM_API_KEY missing"}
        q = quote(query)
        url = f"https://api.tomtom.com/search/2/geocode/{q}.json?key={self.config.tomtom_key}&limit=1"
        return self._get_json(url)

    # 6) Address autocomplete and map suggestions (Geoapify)
    def autocomplete(self, query: str) -> Dict[str, Any]:
        if not self.config.geoapify_key:
            return {"ok": False, "error": "GEOAPIFY_API_KEY missing"}
        q = quote(query)
        url = (
            "https://api.geoapify.com/v1/geocode/autocomplete"
            f"?text={q}&filter=countrycode:in&bias=proximity:77.5946,12.9716&limit=6"
            f"&apiKey={self.config.geoapify_key}"
        )
        return self._get_json(url)

    # 7) Traffic Density / Tile API (TomTom raster flow tiles)
    def get_flow_tile_url(self, lat: float, lon: float, zoom: int = 12) -> Dict[str, Any]:
        if not self.config.tomtom_key:
            return {"ok": False, "error": "TOMTOM_API_KEY missing"}
        n = 2**zoom
        x = int((lon + 180.0) / 360.0 * n)
        lat_rad = lat * math.pi / 180.0
        y = int(
            (
                1.0
                - (((math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad))) / math.pi))
            )
            / 2.0
            * n
        )
        tile_url = (
            f"https://api.tomtom.com/traffic/map/4/tile/flow/relative0/{zoom}/{x}/{y}.png"
            f"?key={self.config.tomtom_key}"
        )
        return {"ok": True, "data": {"tile_url": tile_url, "zoom": zoom, "x": x, "y": y}}

    # 8) Places / POI API (TomTom category search)
    def get_nearby_places(self, lat: float, lon: float, place_type: str = "hospital") -> Dict[str, Any]:
        if not self.config.tomtom_key:
            return {"ok": False, "error": "TOMTOM_API_KEY missing"}
        url = (
            f"https://api.tomtom.com/search/2/categorySearch/{quote(place_type)}.json"
            f"?lat={lat}&lon={lon}&radius=2500&limit=5&key={self.config.tomtom_key}"
        )
        return self._get_json(url)
