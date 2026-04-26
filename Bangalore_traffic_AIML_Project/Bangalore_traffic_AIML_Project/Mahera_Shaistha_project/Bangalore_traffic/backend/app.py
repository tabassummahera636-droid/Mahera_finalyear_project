from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import hmac
import math
import os
import secrets
import sqlite3
from typing import Any

try:
    import joblib
except ModuleNotFoundError:
    joblib = None
try:
    import pandas as pd
except ModuleNotFoundError:
    pd = None
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

load_dotenv(Path(__file__).resolve().parent / '.env')

from ai_service import generate_ai_response, generate_chat_response

app = FastAPI(title='Bangalore Traffic Intelligence API', version='1.2.0')

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        'http://localhost:5173',
        'http://127.0.0.1:5173',
        'http://localhost:5174',
        'http://127.0.0.1:5174',
        'http://localhost:5175',
        'http://127.0.0.1:5175',
    ],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = PROJECT_ROOT / 'ML' / 'route_realtime_model.pkl'
if not MODEL_PATH.exists():
    # Fallback to other possible model paths
    for p in [PROJECT_ROOT / 'models' / 'trafic_model.pkl', PROJECT_ROOT / 'ML' / 'trafic_model.pkl']:
        if p.exists():
            MODEL_PATH = p
            break

DATA_PATH = PROJECT_ROOT / 'data' / 'bangalore_traffic_with_coordinates_FINAL.csv'

TOMTOM_API_KEY = os.getenv('TOMTOM_API_KEY', '').strip()
OPENWEATHER_API_KEY = os.getenv('OPENWEATHER_API_KEY', '').strip()
GEOAPIFY_API_KEY = os.getenv('GEOAPIFY_API_KEY', '').strip()
MAPMYINDIA_API_KEY = os.getenv('MAPMYINDIA_API_KEY', '').strip()
ORS_API_KEY = os.getenv('ORS_API_KEY', '').strip()
OVM_API_KEY = os.getenv('OVM_API_KEY', '').strip()

BLR_LAT = 12.9716
BLR_LON = 77.5946
BLR_BBOX = {
    'min_lon': 77.38,
    'min_lat': 12.82,
    'max_lon': 77.82,
    'max_lat': 13.16,
}

bundle = None
if MODEL_PATH.exists() and joblib is not None:
    try:
        bundle = joblib.load(MODEL_PATH)
    except Exception as exc:
        # Allow API to boot even if the ML bundle is incompatible/corrupt.
        print(f"Warning: failed to load model bundle at {MODEL_PATH}: {exc}")
        bundle = None

traffic_df = None
if DATA_PATH.exists() and pd is not None:
    traffic_df = pd.read_csv(DATA_PATH)

AREA_NAMES: list[str] = []
if bundle and bundle.get('area_to_roads'):
    AREA_NAMES = sorted(list(bundle['area_to_roads'].keys()))
elif traffic_df is not None and 'Area Name' in traffic_df.columns:
    AREA_NAMES = sorted(traffic_df['Area Name'].dropna().astype(str).unique().tolist())

AREA_GEO: dict[str, tuple[float, float]] = {}
if traffic_df is not None and {'Area Name', 'Latitude', 'Longitude'}.issubset(set(traffic_df.columns)):
    _geo = (
        traffic_df.groupby('Area Name')[['Latitude', 'Longitude']]
        .mean(numeric_only=True)
        .dropna()
    )
    for _area, _row in _geo.iterrows():
        AREA_GEO[str(_area)] = (float(_row['Latitude']), float(_row['Longitude']))

DB_PATH = Path(__file__).resolve().parent / 'app_data.db'


def _db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    with _db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS route_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                source TEXT NOT NULL,
                destination TEXT NOT NULL,
                eta REAL,
                delay REAL,
                distance REAL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )
        conn.commit()


def _hash_password(password: str, salt_hex: str) -> str:
    salt = bytes.fromhex(salt_hex)
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 120_000).hex()


def _create_user(username: str, email: str, password: str) -> int:
    salt_hex = secrets.token_hex(16)
    pwhash = _hash_password(password, salt_hex)
    now = datetime.utcnow().isoformat()
    with _db_conn() as conn:
        cur = conn.execute(
            'INSERT INTO users (username, email, password_hash, salt, created_at) VALUES (?, ?, ?, ?, ?)',
            (username, email, pwhash, salt_hex, now),
        )
        conn.commit()
        return int(cur.lastrowid)


def _verify_user(username: str, password: str) -> sqlite3.Row | None:
    with _db_conn() as conn:
        row = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
    if not row:
        return None
    test_hash = _hash_password(password, row['salt'])
    if not hmac.compare_digest(test_hash, row['password_hash']):
        return None
    return row


def _issue_token(user_id: int) -> str:
    token = secrets.token_urlsafe(36)
    now = datetime.utcnow()
    exp = now + timedelta(days=7)
    with _db_conn() as conn:
        conn.execute('INSERT INTO sessions (token, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)', (token, user_id, exp.isoformat(), now.isoformat()))
        conn.commit()
    return token


def _get_user_by_token(authorization: str | None) -> sqlite3.Row:
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(status_code=401, detail='Missing auth token')
    token = authorization.replace('Bearer ', '', 1).strip()
    now_iso = datetime.utcnow().isoformat()
    with _db_conn() as conn:
        row = conn.execute(
            """
            SELECT u.*
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token = ? AND s.expires_at > ?
            """,
            (token, now_iso),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail='Invalid or expired token')
    return row


def _safe_email_from_username(username: str) -> str:
    base = ''.join(ch for ch in username.lower() if ch.isalnum() or ch in '._')
    if not base:
        base = f"user{secrets.randbelow(10000)}"
    return f"{base}@routenova.local"


class PredictRequest(BaseModel):
    source_area: str
    destination: str
    hour: int = Field(ge=0, le=23)
    day_of_week: int = Field(ge=0, le=6)
    month: int = Field(ge=1, le=12)
    day: int = Field(ge=1, le=31)
    latitude: float = BLR_LAT
    longitude: float = BLR_LON


class RoutePlanRequest(BaseModel):
    source_text: str
    destination_text: str
    depart_in_minutes: int = Field(default=0, ge=0, le=1440)


class SignUpRequest(BaseModel):
    username: str = Field(min_length=3)
    email: str
    password: str = Field(min_length=6)


class LoginRequest(BaseModel):
    username: str
    password: str


class HistoryRequest(BaseModel):
    source: str
    destination: str
    eta: float | None = None
    delay: float | None = None
    distance: float | None = None


class AIInsightsRequest(BaseModel):
    context: str = 'general'
    traffic: dict[str, Any] | None = None
    weather: dict[str, Any] | None = None
    route: dict[str, Any] | None = None
    history: dict[str, Any] | None = None
    urban: dict[str, Any] | None = None
    question: str | None = None


class AIChatRequest(BaseModel):
    message: str
    context: str = 'general'
    snapshot: dict[str, Any] | None = None


def _require_key(key: str, name: str) -> None:
    if not key:
        raise HTTPException(status_code=503, detail=f'{name} missing')


def _get_json(url: str, timeout: int = 12) -> dict[str, Any]:
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        return {'ok': True, 'data': resp.json()}
    except Exception as exc:
        return {'ok': False, 'error': str(exc), 'url': url}


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None, timeout: int = 12) -> dict[str, Any]:
    try:
        resp = requests.post(url, json=payload, headers=headers or {}, timeout=timeout)
        resp.raise_for_status()
        return {'ok': True, 'data': resp.json()}
    except Exception as exc:
        return {'ok': False, 'error': str(exc), 'url': url}


def _is_in_blr(lat: float | None, lon: float | None) -> bool:
    if lat is None or lon is None:
        return False
    return (
        BLR_BBOX['min_lat'] <= float(lat) <= BLR_BBOX['max_lat']
        and BLR_BBOX['min_lon'] <= float(lon) <= BLR_BBOX['max_lon']
    )


def _is_greeting(text: str) -> bool:
    t = (text or '').strip().lower()
    return t in {'hi', 'hello', 'hey', 'hi!', 'hello!', 'hey!'}

def _is_thanks(text: str) -> bool:
    t = (text or '').strip().lower()
    return t in {'thanks', 'thank you', 'thankyou', 'ty', 'thx', 'thanks!', 'thank you!'}


def _is_domain_query(text: str) -> bool:
    t = (text or '').lower()
    keywords = [
        'traffic', 'route', 'routes', 'eta', 'delay', 'congestion', 'incident', 'incidents',
        'weather', 'rain', 'storm', 'wind', 'travel', 'time', 'km', 'speed', 'flow',
        'hotspot', 'hotspots', 'best route', 'alternate', 'distance', 'map', 'road',
    ]
    return any(k in t for k in keywords)




def _tomtom_geocode_one(text: str) -> dict[str, Any]:
    _require_key(TOMTOM_API_KEY, 'TOMTOM_API_KEY')
    q = f'{text}, Bangalore'
    url = (
        f"https://api.tomtom.com/search/2/geocode/{requests.utils.quote(q)}.json"
        f"?key={TOMTOM_API_KEY}&limit=1&countrySet=IN"
    )
    payload = _get_json(url)
    if not payload.get('ok'):
        return payload
    results = payload['data'].get('results', [])
    if not results:
        return {'ok': False, 'error': 'No geocode result', 'text': text}
    item = results[0]
    pos = item.get('position', {})
    return {
        'ok': True,
        'data': {
            'label': item.get('address', {}).get('freeformAddress', text),
            'lat': pos.get('lat'),
            'lon': pos.get('lon'),
        },
    }


def _tomtom_search_one(text: str) -> dict[str, Any]:
    _require_key(TOMTOM_API_KEY, 'TOMTOM_API_KEY')
    q = f'{text}, Bengaluru'
    url = (
        f"https://api.tomtom.com/search/2/search/{requests.utils.quote(q)}.json"
        f"?key={TOMTOM_API_KEY}&limit=5&countrySet=IN&lat={BLR_LAT}&lon={BLR_LON}&radius=30000"
    )
    payload = _get_json(url)
    if not payload.get('ok'):
        return payload
    for item in payload['data'].get('results', []):
        pos = item.get('position', {})
        lat = pos.get('lat')
        lon = pos.get('lon')
        if _is_in_blr(lat, lon):
            return {
                'ok': True,
                'data': {
                    'label': item.get('address', {}).get('freeformAddress', text),
                    'lat': lat,
                    'lon': lon,
                },
            }
    return {'ok': False, 'error': 'No TomTom search result in Bangalore', 'text': text}


def _geoapify_geocode_one(text: str) -> dict[str, Any]:
    _require_key(GEOAPIFY_API_KEY, 'GEOAPIFY_API_KEY')
    bbox = f"{BLR_BBOX['min_lon']},{BLR_BBOX['min_lat']},{BLR_BBOX['max_lon']},{BLR_BBOX['max_lat']}"
    url = (
        'https://api.geoapify.com/v1/geocode/search'
        f"?text={requests.utils.quote(text)}"
        f"&filter=rect:{bbox}"
        f"&bias=proximity:{BLR_LON},{BLR_LAT}"
        '&limit=1'
        f"&apiKey={GEOAPIFY_API_KEY}"
    )
    result = _get_json(url)
    if not result.get('ok'):
        return result
    features = result['data'].get('features', [])
    if not features:
        return {'ok': False, 'error': 'No Geoapify result', 'text': text}
    p = (features[0] or {}).get('properties', {})
    lat = p.get('lat')
    lon = p.get('lon')
    if not _is_in_blr(lat, lon):
        return {'ok': False, 'error': 'Geoapify result outside Bangalore', 'text': text}
    return {
        'ok': True,
        'data': {
            'label': p.get('formatted') or text,
            'lat': lat,
            'lon': lon,
        },
    }


def _mapmyindia_geocode_one(text: str) -> dict[str, Any]:
    _require_key(MAPMYINDIA_API_KEY, 'MAPMYINDIA_API_KEY')
    url = (
        'https://search.mappls.com/search/places/autosuggest/json'
        f"?query={requests.utils.quote(text)}"
        f"&location={BLR_LAT},{BLR_LON}"
        f"&access_token={MAPMYINDIA_API_KEY}"
    )
    result = _get_json(url)
    if not result.get('ok'):
        return result
    for item in result['data'].get('suggestedLocations', []):
        lat = item.get('latitude')
        lon = item.get('longitude')
        if _is_in_blr(lat, lon):
            return {
                'ok': True,
                'data': {
                    'label': item.get('placeName') or item.get('address') or text,
                    'lat': lat,
                    'lon': lon,
                },
            }
    return {'ok': False, 'error': 'No MapMyIndia result in Bangalore', 'text': text}


def _resolve_location_any_provider(text: str) -> dict[str, Any]:
    # Multi-provider resolution so any valid Bangalore locality can work.
    attempts: list[dict[str, Any]] = []
    for fn in [_tomtom_geocode_one, _tomtom_search_one, _geoapify_geocode_one, _mapmyindia_geocode_one]:
        try:
            out = fn(text)
            attempts.append(out)
            if out.get('ok') and _is_in_blr(out['data'].get('lat'), out['data'].get('lon')):
                return out
        except HTTPException:
            continue
        except Exception:
            continue
    return {'ok': False, 'error': 'Unable to geocode location inside Bangalore', 'attempts': attempts[:3]}


def _tomtom_reverse_name(lat: float, lon: float) -> str | None:
    if not TOMTOM_API_KEY:
        return None
    url = (
        'https://api.tomtom.com/search/2/reverseGeocode/'
        f'{lat},{lon}.json?key={TOMTOM_API_KEY}&radius=500&number=1&language=en-IN'
    )
    result = _get_json(url)
    if not result.get('ok'):
        return None
    addresses = result['data'].get('addresses', [])
    if not addresses:
        return None
    addr = addresses[0].get('address', {}) or {}
    return (
        addr.get('localName')
        or addr.get('neighbourhood')
        or addr.get('municipalitySubdivision')
        or addr.get('municipality')
        or addr.get('streetName')
        or addr.get('freeformAddress')
    )


def _geoapify_reverse_name(lat: float, lon: float) -> str | None:
    if not GEOAPIFY_API_KEY:
        return None
    url = (
        'https://api.geoapify.com/v1/geocode/reverse'
        f'?lat={lat}&lon={lon}&format=json&apiKey={GEOAPIFY_API_KEY}'
    )
    result = _get_json(url)
    if not result.get('ok'):
        return None
    results = (result['data'] or {}).get('results') or []
    if not results:
        return None
    r0 = results[0] or {}
    return (
        r0.get('neighbourhood')
        or r0.get('suburb')
        or r0.get('locality')
        or r0.get('hamlet')
        or r0.get('village')
        or r0.get('town')
        or r0.get('city')
        or r0.get('county')
        or r0.get('formatted')
    )

def _ors_route(start_lat: float, start_lon: float, end_lat: float, end_lon: float) -> dict[str, Any]:
    _require_key(ORS_API_KEY, 'ORS_API_KEY')
    url = 'https://api.openrouteservice.org/v2/directions/driving-car'
    params = {
        'api_key': ORS_API_KEY,
        'start': f'{start_lon},{start_lat}',
        'end': f'{end_lon},{end_lat}',
    }
    payload = _get_json(f"{url}?{requests.compat.urlencode(params)}")
    if not payload.get('ok'):
        return payload
    feature = (payload['data'].get('features') or [None])[0]
    if not feature:
        return {'ok': False, 'error': 'No ORS route result'}
    summary = (feature.get('properties') or {}).get('summary') or {}
    coords = (feature.get('geometry') or {}).get('coordinates') or []
    poly = [[float(lat), float(lon)] for lon, lat in coords if lon is not None and lat is not None]
    return {
        'ok': True,
        'data': {
            'distance_km': round(float(summary.get('distance', 0.0)) / 1000, 2),
            'travel_minutes': round(float(summary.get('duration', 0.0)) / 60, 1),
            'polyline': _downsample_points(poly),
        },
    }


def _ors_alternative_routes(start_lat: float, start_lon: float, end_lat: float, end_lon: float, target_count: int = 3) -> list[dict[str, Any]]:
    if not ORS_API_KEY:
        return []
    # ORS alternative routes are limited to ~100km; avoid 400s for longer trips.
    if _haversine_km(start_lat, start_lon, end_lat, end_lon) > 100:
        return []
    url = 'https://api.openrouteservice.org/v2/directions/driving-car/geojson'
    payload = {
        'coordinates': [[start_lon, start_lat], [end_lon, end_lat]],
        'alternative_routes': {
            'target_count': max(1, min(int(target_count), 3)),
            'weight_factor': 1.5,
            'share_factor': 0.6,
        },
        'instructions': False,
    }
    headers = {
        'Authorization': ORS_API_KEY,
        'Content-Type': 'application/json; charset=utf-8',
        'Accept': 'application/geo+json',
    }
    result = _post_json(url, payload, headers=headers)
    if not result.get('ok'):
        return []
    features = result['data'].get('features', []) or []
    routes = []
    for feat in features:
        geom = (feat.get('geometry') or {}).get('coordinates') or []
        poly = [[float(lat), float(lon)] for lon, lat in geom if lon is not None and lat is not None]
        summary = ((feat.get('properties') or {}).get('summary') or {})
        distance_km = round(float(summary.get('distance', 0.0)) / 1000, 2)
        travel_minutes = round(float(summary.get('duration', 0.0)) / 60, 1)
        if poly:
            routes.append(
                {
                    'rank': 0,
                    'distance_km': distance_km,
                    'travel_minutes': travel_minutes,
                    'traffic_delay_minutes': 0.0,
                    'delay_source': 'ors_no_traffic',
                    'route_score': int(travel_minutes * 60),
                    'advice': 'Alternative (ORS)',
                    'polyline': _downsample_points(poly),
                }
            )
    return routes


def _ors_route_single(
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    *,
    avoid_features: list[str] | None = None,
) -> dict[str, Any] | None:
    if not ORS_API_KEY:
        return None
    url = 'https://api.openrouteservice.org/v2/directions/driving-car/geojson'
    payload: dict[str, Any] = {
        'coordinates': [[start_lon, start_lat], [end_lon, end_lat]],
        'instructions': False,
    }
    if avoid_features:
        payload['options'] = {'avoid_features': avoid_features}
    headers = {
        'Authorization': ORS_API_KEY,
        'Content-Type': 'application/json; charset=utf-8',
        'Accept': 'application/geo+json',
    }
    result = _post_json(url, payload, headers=headers)
    if not result.get('ok'):
        return None
    features = result['data'].get('features', []) or []
    if not features:
        return None
    feat = features[0]
    geom = (feat.get('geometry') or {}).get('coordinates') or []
    poly = [[float(lat), float(lon)] for lon, lat in geom if lon is not None and lat is not None]
    summary = ((feat.get('properties') or {}).get('summary') or {})
    distance_km = round(float(summary.get('distance', 0.0)) / 1000, 2)
    travel_minutes = round(float(summary.get('duration', 0.0)) / 60, 1)
    if not poly:
        return None
    label = 'Alternative (ORS)'
    if avoid_features:
        label = f"Alternative (ORS, avoid {', '.join(avoid_features)})"
    return {
        'rank': 0,
        'distance_km': distance_km,
        'travel_minutes': travel_minutes,
        'traffic_delay_minutes': 0.0,
        'delay_source': 'ors_no_traffic',
        'route_score': int(travel_minutes * 60),
        'advice': label,
        'polyline': _downsample_points(poly),
    }


def _xy_tile(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    n = 2**zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - (math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi)) / 2.0 * n)
    return x, y


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _nearest_rows(lat: float, lon: float, limit: int = 80) -> Any:
    if traffic_df is None or pd is None:
        return None
    df = traffic_df.dropna(subset=['Latitude', 'Longitude'])
    if df is None or getattr(df, 'empty', True):
        return df
    df = df.copy()
    df['__dist__'] = df.apply(lambda r: _haversine_km(lat, lon, float(r['Latitude']), float(r['Longitude'])), axis=1)
    return df.sort_values('__dist__').head(limit)

def _downsample_points(points: list[list[float]], max_points: int = 220) -> list[list[float]]:
    if len(points) <= max_points:
        return points
    step = max(1, math.ceil(len(points) / max_points))
    sampled = points[::step]
    if sampled[-1] != points[-1]:
        sampled.append(points[-1])
    return sampled


def _extract_route_polyline(route: dict[str, Any]) -> list[list[float]]:
    coords: list[list[float]] = []
    for leg in route.get('legs', []):
        for point in leg.get('points', []):
            lat = point.get('latitude')
            lon = point.get('longitude')
            if lat is None or lon is None:
                continue
            coords.append([float(lat), float(lon)])
    return _downsample_points(coords)


def _tomtom_route_fetch(
    route_locations: str,
    depart_iso: str,
    *,
    traffic: bool = True,
    route_type: str = 'fastest',
    max_alternatives: int = 3,
) -> list[dict[str, Any]]:
    url = (
        f"https://api.tomtom.com/routing/1/calculateRoute/{route_locations}/json"
        f"?key={TOMTOM_API_KEY}"
        f"&traffic={'true' if traffic else 'false'}"
        f"&maxAlternatives={max_alternatives}"
        f"&routeType={route_type}"
        f"&departAt={depart_iso}"
        f"&routeRepresentation=polyline"
    )
    result = _get_json(url)
    if not result.get('ok'):
        return []
    return result['data'].get('routes', []) or []


def _score_and_pack_route(route: dict[str, Any], idx: int) -> dict[str, Any]:
    summary = route.get('summary', {}) or {}
    travel = int(summary.get('travelTimeInSeconds', 0))
    raw_delay = summary.get('trafficDelayInSeconds')
    no_traffic = summary.get('noTrafficTravelTimeInSeconds')
    if raw_delay is not None:
        delay = int(raw_delay)
        delay_source = 'tomtom_trafficDelayInSeconds'
    elif no_traffic is not None:
        delay = max(0, int(travel) - int(no_traffic))
        delay_source = 'derived_travel_minus_noTraffic'
    else:
        delay = 0
        delay_source = 'fallback_zero'
    length_m = int(summary.get('lengthInMeters', 0))
    score = travel + (2 * delay)
    return {
        'rank': idx,
        'distance_km': round(length_m / 1000, 2),
        'travel_minutes': round(travel / 60, 1),
        'traffic_delay_minutes': round(delay / 60, 1),
        'delay_source': delay_source,
        'route_score': score,
        'advice': 'Alternative',
        'polyline': _extract_route_polyline(route),
    }


def _resolve_source_area(source_area: str | None) -> str | None:
    if not source_area:
        return None
    s = source_area.strip().lower()
    if not s:
        return None
    for area in AREA_NAMES:
        if area.lower() == s:
            return area
    for area in AREA_NAMES:
        if s in area.lower() or area.lower() in s:
            return area
    return None


def _traffic_level(volume: float, low_q: float, high_q: float) -> str:
    if volume >= high_q:
        return 'Heavy Congestion'
    if volume >= low_q:
        return 'Moderate Traffic'
    return 'Light Traffic'


_init_db()


@app.get('/health')
def health() -> dict[str, Any]:
    return {
        'ok': True,
        'city': 'Bangalore',
        'model_loaded': bundle is not None,
        'dataset_loaded': traffic_df is not None,
        'env': {
            'tomtom_key_set': bool(TOMTOM_API_KEY),
            'openweather_key_set': bool(OPENWEATHER_API_KEY),
            'geoapify_key_set': bool(GEOAPIFY_API_KEY),
            'mapmyindia_key_set': bool(MAPMYINDIA_API_KEY),
            'ors_key_set': bool(ORS_API_KEY),
            'ovm_key_set': bool(OVM_API_KEY),
        },
    }


@app.post('/auth/signup')
def auth_signup(payload: SignUpRequest) -> dict[str, Any]:
    username = payload.username.strip()
    email = payload.email.strip().lower()
    password = payload.password
    if len(username) < 3:
        raise HTTPException(status_code=400, detail='Username must have at least 3 characters')
    if len(password) < 6:
        raise HTTPException(status_code=400, detail='Password must have at least 6 characters')

    try:
        user_id = _create_user(username, email, password)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail='Username or email already exists')

    token = _issue_token(user_id)
    return {'ok': True, 'token': token, 'user': {'username': username, 'email': email}}


@app.post('/auth/login')
def auth_login(payload: LoginRequest) -> dict[str, Any]:
    username = payload.username.strip()
    password = payload.password
    if len(username) < 3:
        raise HTTPException(status_code=400, detail='Username must have at least 3 characters')
    if len(password) < 6:
        raise HTTPException(status_code=400, detail='Password must have at least 6 characters')

    user = _verify_user(username, password)
    if not user:
        # Auto-create user on first login for a smoother demo experience.
        try:
            email = _safe_email_from_username(username)
            user_id = _create_user(username, email, password)
            token = _issue_token(user_id)
            return {'ok': True, 'token': token, 'user': {'username': username, 'email': email}}
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=401, detail='Incorrect username or password')
    token = _issue_token(int(user['id']))
    return {'ok': True, 'token': token, 'user': {'username': user['username'], 'email': user['email']}}


@app.get('/auth/me')
def auth_me(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    user = _get_user_by_token(authorization)
    return {'ok': True, 'user': {'id': int(user['id']), 'username': user['username'], 'email': user['email']}}


@app.post('/history')
def add_history(payload: HistoryRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    user = _get_user_by_token(authorization)
    now = datetime.utcnow().isoformat()
    with _db_conn() as conn:
        conn.execute(
            'INSERT INTO route_history (user_id, source, destination, eta, delay, distance, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (int(user['id']), payload.source, payload.destination, payload.eta, payload.delay, payload.distance, now),
        )
        conn.commit()
    return {'ok': True}


@app.get('/history')
def get_history(authorization: str | None = Header(default=None), limit: int = 50) -> dict[str, Any]:
    user = _get_user_by_token(authorization)
    with _db_conn() as conn:
        rows = conn.execute(
            'SELECT source, destination, eta, delay, distance, created_at FROM route_history WHERE user_id = ? ORDER BY id DESC LIMIT ?',
            (int(user['id']), max(1, min(limit, 200))),
        ).fetchall()
    items = [
        {
            'source': r['source'],
            'destination': r['destination'],
            'eta': r['eta'],
            'delay': r['delay'],
            'distance': r['distance'],
            'ts': r['created_at'],
        }
        for r in rows
    ]
    return {'ok': True, 'items': items}


@app.post('/ai-insights')
def ai_insights(payload: AIInsightsRequest) -> dict[str, Any]:
    data = {
        'traffic': payload.traffic or {},
        'weather': payload.weather or {},
        'route': payload.route or {},
        'history': payload.history or {},
        'urban': payload.urban or {},
        'question': payload.question or '',
    }
    insight = generate_ai_response(f"insights:{payload.context}", data)
    if not insight:
        insight = (
            "Traffic is moderate across central Bangalore with minor congestion in peak zones. "
            "Consider alternate routes during busy hours."
        )
    return {'ok': True, 'insight': insight}


@app.post('/ai-chat')
def ai_chat(payload: AIChatRequest) -> dict[str, Any]:
    snapshot = payload.snapshot or {}
    data = {
        'traffic': snapshot.get('traffic') or {},
        'weather': snapshot.get('weather') or {},
        'route': snapshot.get('route') or {},
        'history': snapshot.get('history') or {},
        'urban': snapshot.get('urban') or {},
        'question': payload.message,
    }
    reply = generate_ai_response(f"chat:{payload.context}", data)
    return {'ok': True, 'reply': reply}


@app.post('/chat')
def chat(payload: AIChatRequest) -> dict[str, Any]:
    # Alias endpoint for frontend chatbot integration.
    if _is_greeting(payload.message):
        return {'ok': True, 'reply': 'Hello! How can I assist you with routes or traffic today?'}
    if _is_thanks(payload.message):
        return {'ok': True, 'reply': "You're welcome! If you need traffic or route help, just ask."}
    if not _is_domain_query(payload.message):
        return {'ok': True, 'reply': 'I can assist only with traffic, routes, and travel insights.'}
    snapshot = payload.snapshot or {}
    route_data = snapshot.get('route') or {}
    if ('route' in (payload.message or '').lower() or 'best route' in (payload.message or '').lower()) and (not route_data or route_data.get('eta') in (None, '', 0)):
        return {'ok': True, 'reply': 'Please compute a route first to get accurate suggestions.'}
    data = {
        'traffic': snapshot.get('traffic') or {},
        'weather': snapshot.get('weather') or {},
        'route': route_data,
        'history': snapshot.get('history') or {},
        'urban': snapshot.get('urban') or {},
        'question': payload.message,
    }
    reply = generate_chat_response(f"chat:{payload.context}", data)
    return {'ok': True, 'reply': reply}


@app.get('/area-suggestions')
def area_suggestions(query: str = Query('', min_length=0), limit: int = 8) -> dict[str, Any]:
    q = query.strip().lower()
    names = AREA_NAMES
    if q:
        names = [a for a in AREA_NAMES if q in a.lower()]
    return {'city': 'Bangalore', 'suggestions': names[: max(1, min(limit, 20))]}


@app.get('/autocomplete')
def autocomplete(text: str = Query(..., min_length=1)) -> dict[str, Any]:
    suggestions: list[dict[str, Any]] = []
    seen: set[str] = set()

    if GEOAPIFY_API_KEY:
        url = (
            'https://api.geoapify.com/v1/geocode/autocomplete'
        f"?text={requests.utils.quote(text)}&filter=countrycode:in"
        f"&bias=proximity:{BLR_LON},{BLR_LAT}&limit=12"
        f"&apiKey={GEOAPIFY_API_KEY}"
    )
        result = _get_json(url)
        if result.get('ok'):
            for feature in result['data'].get('features', []):
                p = feature.get('properties', {})
                lat = p.get('lat')
                lon = p.get('lon')
                if not _is_in_blr(lat, lon):
                    continue
                name = str(p.get('formatted') or '').strip()
                if not name:
                    continue
                key = name.lower()
                if key in seen:
                    continue
                seen.add(key)
                suggestions.append({'name': name, 'lat': lat, 'lon': lon, 'source': 'geoapify'})

    if TOMTOM_API_KEY and len(suggestions) < 8:
        url = (
            f"https://api.tomtom.com/search/2/search/{requests.utils.quote(text)}.json"
            f"?key={TOMTOM_API_KEY}&limit=12&countrySet=IN&lat={BLR_LAT}&lon={BLR_LON}&radius=30000"
        )
        result = _get_json(url)
        if result.get('ok'):
            for item in result['data'].get('results', []):
                pos = item.get('position', {})
                lat = pos.get('lat')
                lon = pos.get('lon')
                if not _is_in_blr(lat, lon):
                    continue
                name = str(item.get('address', {}).get('freeformAddress') or item.get('poi', {}).get('name') or '').strip()
                if not name:
                    continue
                key = name.lower()
                if key in seen:
                    continue
                seen.add(key)
                suggestions.append({'name': name, 'lat': lat, 'lon': lon, 'source': 'tomtom'})

    if MAPMYINDIA_API_KEY and len(suggestions) < 12:
        url = (
            'https://search.mappls.com/search/places/autosuggest/json'
            f"?query={requests.utils.quote(text)}"
            f"&location={BLR_LAT},{BLR_LON}"
            f"&access_token={MAPMYINDIA_API_KEY}"
        )
        result = _get_json(url)
        if result.get('ok'):
            for item in result['data'].get('suggestedLocations', []):
                lat = item.get('latitude')
                lon = item.get('longitude')
                if not _is_in_blr(lat, lon):
                    continue
                name = str(item.get('placeName') or item.get('address') or '').strip()
                if not name:
                    continue
                key = name.lower()
                if key in seen:
                    continue
                seen.add(key)
                suggestions.append({'name': name, 'lat': lat, 'lon': lon, 'source': 'mapmyindia'})

    q = text.strip().lower()
    starts = [s for s in suggestions if s['name'].lower().startswith(q)]
    others = [s for s in suggestions if s['name'].lower() not in {x['name'].lower() for x in starts}]
    ordered = starts + others
    return {'city': 'Bangalore', 'suggestions': ordered[:12]}


@app.get('/mmi-autosuggest')
def mmi_autosuggest(
    text: str = Query(..., min_length=2),
    lat: float = BLR_LAT,
    lon: float = BLR_LON,
) -> dict[str, Any]:
    _require_key(MAPMYINDIA_API_KEY, 'MAPMYINDIA_API_KEY')
    url = (
        'https://search.mappls.com/search/places/autosuggest/json'
        f"?query={requests.utils.quote(text)}"
        f"&location={lat},{lon}"
        f"&access_token={MAPMYINDIA_API_KEY}"
    )
    result = _get_json(url)
    if not result.get('ok'):
        raise HTTPException(status_code=502, detail=result.get('error', 'MapMyIndia autosuggest failed'))
    return {'suggestions': result['data'].get('suggestedLocations', [])}


@app.get('/mmi-route')
def mmi_route(
    origin: str = Query(..., description='lon,lat or eLoc'),
    destination: str = Query(..., description='lon,lat or eLoc'),
    rtype: int = 1,
    steps: bool = False,
) -> dict[str, Any]:
    _require_key(MAPMYINDIA_API_KEY, 'MAPMYINDIA_API_KEY')
    url = (
        'https://route.mappls.com/route/direction/route_adv/driving/'
        f"{origin};{destination}"
        f"?steps={'true' if steps else 'false'}"
        f"&rtype={rtype}"
        f"&access_token={MAPMYINDIA_API_KEY}"
    )
    result = _get_json(url)
    if not result.get('ok'):
        raise HTTPException(status_code=502, detail=result.get('error', 'MapMyIndia route failed'))
    return result['data']


@app.get('/hotspots')
def hotspots(limit: int = 8) -> dict[str, Any]:
    if traffic_df is None:
        return {'city': 'Bangalore', 'hotspots': []}

    req_cols = {'Area Name', 'Road/Intersection Name', 'Traffic_Volume', 'Incident Reports', 'Latitude', 'Longitude'}
    missing = [c for c in req_cols if c not in traffic_df.columns]
    if missing:
        return {'city': 'Bangalore', 'hotspots': []}

    g = (
        traffic_df.groupby(['Area Name', 'Road/Intersection Name'], dropna=False)
        .agg(
            traffic_volume=('Traffic_Volume', 'mean'),
            incidents=('Incident Reports', 'mean'),
            lat=('Latitude', 'mean'),
            lon=('Longitude', 'mean'),
        )
        .reset_index()
    )
    g['score'] = g['traffic_volume'].fillna(0) + (g['incidents'].fillna(0) * 120)
    g = g.sort_values('score', ascending=False).head(max(1, min(limit, 30)))

    out = []
    for _, r in g.iterrows():
        out.append(
            {
                'area': str(r['Area Name']),
                'road': str(r['Road/Intersection Name']),
                'traffic_volume': float(r['traffic_volume']) if pd.notna(r['traffic_volume']) else None,
                'incidents': float(r['incidents']) if pd.notna(r['incidents']) else None,
                'lat': float(r['lat']) if pd.notna(r['lat']) else None,
                'lon': float(r['lon']) if pd.notna(r['lon']) else None,
            }
        )

    return {'city': 'Bangalore', 'hotspots': out}


@app.get('/weather')
def weather(lat: float = BLR_LAT, lon: float = BLR_LON) -> dict[str, Any]:
    if OPENWEATHER_API_KEY:
        url = (
            'https://api.openweathermap.org/data/2.5/weather'
            f"?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric"
        )
        result = _get_json(url)
        if result.get('ok'):
            data = result['data']
            return {
                'temperature_c': data.get('main', {}).get('temp'),
                'feels_like_c': data.get('main', {}).get('feels_like'),
                'humidity': data.get('main', {}).get('humidity'),
                'weather': (data.get('weather') or [{}])[0].get('main'),
                'description': (data.get('weather') or [{}])[0].get('description'),
                'wind_speed': data.get('wind', {}).get('speed'),
                'source': 'openweather',
            }

    # Fallback to Open-Meteo (no key required).
    meteourl = (
        'https://api.open-meteo.com/v1/forecast'
        f"?latitude={lat}&longitude={lon}"
        "&current_weather=true&hourly=relative_humidity_2m,apparent_temperature,wind_speed_10m"
    )
    result = _get_json(meteourl)
    if not result.get('ok'):
        raise HTTPException(status_code=502, detail=result.get('error', 'Weather failed'))
    data = result['data']
    current = data.get('current_weather', {}) or {}
    hourly = data.get('hourly', {}) or {}
    humidity = (hourly.get('relative_humidity_2m') or [None])[0]
    feels_like = (hourly.get('apparent_temperature') or [None])[0]
    wind_speed = (hourly.get('wind_speed_10m') or [None])[0]
    return {
        'temperature_c': current.get('temperature'),
        'feels_like_c': feels_like,
        'humidity': humidity,
        'weather': 'Clear' if current.get('weathercode') in (0,) else 'Cloudy',
        'description': 'open-meteo fallback',
        'wind_speed': wind_speed,
        'source': 'open-meteo',
    }


@app.get('/current-location')
def current_location(request: Request) -> dict[str, Any]:
    client_ip = request.headers.get('x-forwarded-for', '').split(',')[0].strip() or (request.client.host if request.client else '')
    candidates = [
        ('ipapi', f'https://ipapi.co/json/'),
        ('ipwhois', 'https://ipwho.is/'),
    ]

    for source, url in candidates:
        result = _get_json(url, timeout=8)
        if not result.get('ok'):
            continue
        data = result.get('data') or {}
        lat = data.get('latitude') if source == 'ipapi' else data.get('latitude')
        lon = data.get('longitude') if source == 'ipapi' else data.get('longitude')
        city = data.get('city')
        region = data.get('region') or data.get('region_name')
        country = data.get('country_name') or data.get('country')
        try:
            lat_f = float(lat)
            lon_f = float(lon)
        except Exception:
            continue
        return {
            'lat': lat_f,
            'lon': lon_f,
            'city': city or 'Bengaluru',
            'region': region or 'Karnataka',
            'country': country or 'India',
            'client_ip': client_ip,
            'source': source,
            'inside_bangalore': _is_in_blr(lat_f, lon_f),
        }

    return {
        'lat': BLR_LAT,
        'lon': BLR_LON,
        'city': 'Bengaluru',
        'region': 'Karnataka',
        'country': 'India',
        'client_ip': client_ip,
        'source': 'default',
        'inside_bangalore': True,
    }


@app.get('/location-context')
def location_context(lat: float = BLR_LAT, lon: float = BLR_LON) -> dict[str, Any]:
    area_name = _tomtom_reverse_name(lat, lon) or _geoapify_reverse_name(lat, lon)
    if not area_name and AREA_GEO and _is_in_blr(lat, lon):
        nearest_area = None
        nearest_dist = float('inf')
        for area, (area_lat, area_lon) in AREA_GEO.items():
            dist = math.hypot(float(area_lat) - lat, float(area_lon) - lon)
            if dist < nearest_dist:
                nearest_dist = dist
                nearest_area = area
        area_name = nearest_area
    return {
        'lat': lat,
        'lon': lon,
        'area': area_name or 'Location',
        'source': 'reverse' if area_name else 'default',
        'inside_bangalore': _is_in_blr(lat, lon),
    }


@app.get('/traffic-flow')
def traffic_flow(lat: float = BLR_LAT, lon: float = BLR_LON) -> dict[str, Any]:
    if TOMTOM_API_KEY:
        url = (
            'https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json'
            f"?point={lat},{lon}&key={TOMTOM_API_KEY}"
        )
        result = _get_json(url)
        if result.get('ok'):
            flow = result['data'].get('flowSegmentData', {})
            current_speed = flow.get('currentSpeed')
            free_flow_speed = flow.get('freeFlowSpeed')
            tti = None
            if current_speed and current_speed > 0 and free_flow_speed is not None:
                tti = round(free_flow_speed / current_speed, 3)

            return {
                'current_speed_kmph': current_speed,
                'free_flow_speed_kmph': free_flow_speed,
                'travel_time_index': tti,
                'confidence': flow.get('confidence'),
                'road_closure': flow.get('roadClosure'),
                'source': 'tomtom',
            }

    # Dataset fallback
    df = _nearest_rows(lat, lon, limit=120)
    if df.empty:
        return {
            'current_speed_kmph': None,
            'free_flow_speed_kmph': None,
            'travel_time_index': None,
            'confidence': None,
            'road_closure': None,
            'source': 'dataset',
        }
    avg_speed = float(df['Average Speed'].mean()) if 'Average Speed' in df.columns else None
    tti = float(df['Travel Time Index'].mean()) if 'Travel Time Index' in df.columns else None
    free_flow = avg_speed * (tti or 1.0) if avg_speed is not None else None
    return {
        'current_speed_kmph': round(avg_speed, 2) if avg_speed is not None else None,
        'free_flow_speed_kmph': round(free_flow, 2) if free_flow is not None else None,
        'travel_time_index': round(tti, 3) if tti is not None else None,
        'confidence': None,
        'road_closure': None,
        'source': 'dataset',
    }


@app.get('/air-quality')
def air_quality(lat: float = BLR_LAT, lon: float = BLR_LON) -> dict[str, Any]:
    if not OPENWEATHER_API_KEY:
        return {
            'aqi': None,
            'components': {},
            'source': 'unavailable',
        }
    url = (
        'https://api.openweathermap.org/data/2.5/air_pollution'
        f'?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}'
    )
    result = _get_json(url)
    if not result.get('ok'):
        raise HTTPException(status_code=502, detail=result.get('error', 'Air quality failed'))
    data = result['data'] or {}
    items = data.get('list') or []
    if not items:
        return {
            'aqi': None,
            'components': {},
            'source': 'openweather',
        }
    item = items[0] or {}
    return {
        'aqi': (item.get('main') or {}).get('aqi'),
        'components': item.get('components') or {},
        'dt': item.get('dt'),
        'source': 'openweather',
    }


def _live_flow_hotspots(limit: int = 24) -> list[dict[str, Any]]:
    if not TOMTOM_API_KEY:
        return []

    seeds: list[tuple[str, float, float]] = []
    for area, coords in AREA_GEO.items():
        lat, lon = coords
        if _is_in_blr(lat, lon):
            seeds.append((str(area), float(lat), float(lon)))

    if not seeds and traffic_df is not None and {'Area Name', 'Latitude', 'Longitude'}.issubset(set(traffic_df.columns)):
        g = (
            traffic_df.groupby('Area Name', dropna=False)[['Latitude', 'Longitude']]
            .mean(numeric_only=True)
            .dropna()
            .reset_index()
        )
        for _, row in g.iterrows():
            lat = float(row['Latitude']) if pd.notna(row['Latitude']) else None
            lon = float(row['Longitude']) if pd.notna(row['Longitude']) else None
            if _is_in_blr(lat, lon):
                seeds.append((str(row['Area Name']), lat, lon))

    if not seeds:
        seeds = [
            ('MG Road', 12.9756, 77.6066),
            ('Koramangala', 12.9352, 77.6245),
            ('Indiranagar', 12.9719, 77.6412),
            ('Hebbal', 13.0358, 77.5970),
            ('Electronic City', 12.8399, 77.6770),
            ('Whitefield', 12.9698, 77.7500),
            ('Yeshwanthpur', 13.0285, 77.5400),
            ('Jayanagar', 12.9250, 77.5938),
        ]

    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for name, lat, lon in seeds[: max(limit * 2, 20)]:
        key = f'{round(lat, 4)}:{round(lon, 4)}'
        if key in seen:
            continue
        seen.add(key)
        url = (
            'https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json'
            f'?point={lat},{lon}&key={TOMTOM_API_KEY}'
        )
        result = _get_json(url, timeout=10)
        if not result.get('ok'):
            continue
        flow = result['data'].get('flowSegmentData', {}) or {}
        current_speed = flow.get('currentSpeed')
        free_flow_speed = flow.get('freeFlowSpeed')
        if current_speed is None or free_flow_speed in (None, 0):
            continue
        try:
            current_speed_f = float(current_speed)
            free_flow_speed_f = float(free_flow_speed)
        except Exception:
            continue
        if current_speed_f <= 0 or free_flow_speed_f <= 0:
            continue

        tti = max(1.0, free_flow_speed_f / current_speed_f)
        delay_seconds = max(0, int(round((tti - 1.0) * 900)))
        intensity = min(1.0, max(0.15, (tti - 1.0) / 1.8))
        severity = 'low'
        if delay_seconds >= 900:
            severity = 'high'
        elif delay_seconds >= 420:
            severity = 'medium'

        out.append(
            {
                'lat': round(lat, 6),
                'lon': round(lon, 6),
                'intensity': round(float(intensity), 3),
                'delay_seconds': delay_seconds,
                'label': name,
                'source': 'tomtom-flow',
                'speed_kmph': round(current_speed_f, 2),
                'free_flow_speed_kmph': round(free_flow_speed_f, 2),
                'severity': severity,
                'description': f'Live congestion at {name}',
            }
        )

    out.sort(key=lambda item: (item.get('delay_seconds') or 0, item.get('intensity') or 0), reverse=True)
    return out[: max(1, limit)]


@app.get('/incidents')
def incidents(
    min_lon: float = BLR_BBOX['min_lon'],
    min_lat: float = BLR_BBOX['min_lat'],
    max_lon: float = BLR_BBOX['max_lon'],
    max_lat: float = BLR_BBOX['max_lat'],
) -> dict[str, Any]:
    if TOMTOM_API_KEY:
        bbox = f'{min_lon},{min_lat},{max_lon},{max_lat}'
        fields = requests.utils.quote('{incidents{type,geometry{type,coordinates},properties{iconCategory,magnitudeOfDelay,events{description}}}}')
        url = (
            'https://api.tomtom.com/traffic/services/5/incidentDetails'
            f"?key={TOMTOM_API_KEY}&bbox={bbox}&fields={fields}&language=en-GB&timeValidityFilter=present"
        )
        result = _get_json(url)
        if result.get('ok'):
            items = result['data'].get('incidents', [])
            simplified = []
            for i in items[:80]:
                props = i.get('properties', {})
                delay = props.get('magnitudeOfDelay')
                severity = 'low'
                if delay is not None and delay >= 1200:
                    severity = 'high'
                elif delay is not None and delay >= 600:
                    severity = 'medium'

                coords = (i.get('geometry') or {}).get('coordinates')
                lat_i = None
                lon_i = None
                if isinstance(coords, list) and coords:
                    first = coords[0]
                    if isinstance(first, list) and len(first) >= 2:
                        lon_i = first[0]
                        lat_i = first[1]

                simplified.append(
                    {
                        'icon_category': props.get('iconCategory'),
                        'delay_seconds': delay,
                        'severity': severity,
                        'description': ((props.get('events') or [{}])[0].get('description')),
                        'lat': lat_i,
                        'lon': lon_i,
                    }
                )
            return {'count': len(items), 'incidents': simplified, 'source': 'tomtom'}

        live_incidents = _live_flow_hotspots(limit=40)
        if live_incidents:
            simplified = [
                {
                    'icon_category': None,
                    'delay_seconds': item.get('delay_seconds'),
                    'severity': item.get('severity') or 'low',
                    'description': item.get('description') or item.get('label'),
                    'lat': item.get('lat'),
                    'lon': item.get('lon'),
                }
                for item in live_incidents
            ]
            return {'count': len(simplified), 'incidents': simplified, 'source': 'tomtom-flow'}

    # Dataset fallback
    df = traffic_df
    if df is None or df.empty:
        return {'count': 0, 'incidents': [], 'source': 'dataset'}
    if {'Area Name', 'Road/Intersection Name', 'Incident Reports', 'Latitude', 'Longitude', 'Travel Time Index'}.issubset(df.columns):
        g = (
            df.groupby(['Area Name', 'Road/Intersection Name'], dropna=False)
            .agg(
                incidents=('Incident Reports', 'mean'),
                lat=('Latitude', 'mean'),
                lon=('Longitude', 'mean'),
                tti=('Travel Time Index', 'mean'),
            )
            .reset_index()
        )
        g = g.sort_values('incidents', ascending=False).head(60)
        simplified = []
        for _, r in g.iterrows():
            delay_seconds = max(0, float(r['tti'] - 1) * 600) if pd.notna(r['tti']) else None
            simplified.append(
                {
                    'icon_category': None,
                    'delay_seconds': delay_seconds,
                    'severity': 'medium' if delay_seconds and delay_seconds >= 600 else 'low',
                    'description': f"{r['Area Name']} - {r['Road/Intersection Name']}",
                    'lat': float(r['lat']) if pd.notna(r['lat']) else None,
                    'lon': float(r['lon']) if pd.notna(r['lon']) else None,
                }
            )
        return {'count': len(simplified), 'incidents': simplified, 'source': 'dataset'}
    return {'count': 0, 'incidents': [], 'source': 'dataset'}
    bbox = f'{min_lon},{min_lat},{max_lon},{max_lat}'
    fields = requests.utils.quote('{incidents{type,geometry{type,coordinates},properties{iconCategory,magnitudeOfDelay,events{description}}}}')
    url = (
        'https://api.tomtom.com/traffic/services/5/incidentDetails'
        f"?key={TOMTOM_API_KEY}&bbox={bbox}&fields={fields}&language=en-GB&timeValidityFilter=present"
    )
    result = _get_json(url)
    if not result.get('ok'):
        raise HTTPException(status_code=502, detail=result.get('error', 'Incidents API failed'))

    items = result['data'].get('incidents', [])
    simplified = []
    for i in items[:80]:
        props = i.get('properties', {})
        delay = props.get('magnitudeOfDelay')
        severity = 'low'
        if delay is not None and delay >= 1200:
            severity = 'high'
        elif delay is not None and delay >= 600:
            severity = 'medium'

        coords = (i.get('geometry') or {}).get('coordinates')
        lat = None
        lon = None
        if isinstance(coords, list) and coords:
            first = coords[0]
            if isinstance(first, list) and len(first) >= 2:
                lon = first[0]
                lat = first[1]

        simplified.append(
            {
                'icon_category': props.get('iconCategory'),
                'delay_seconds': delay,
                'severity': severity,
                'description': ((props.get('events') or [{}])[0].get('description')),
                'lat': lat,
                'lon': lon,
            }
        )
    return {'count': len(items), 'incidents': simplified}


@app.get('/live-heatspots')
def live_heatspots(limit: int = 120) -> dict[str, Any]:
    out: list[dict[str, Any]] = []
    if TOMTOM_API_KEY:
        bbox = f"{BLR_BBOX['min_lon']},{BLR_BBOX['min_lat']},{BLR_BBOX['max_lon']},{BLR_BBOX['max_lat']}"
        fields = requests.utils.quote('{incidents{type,geometry{type,coordinates},properties{iconCategory,magnitudeOfDelay,events{description}}}}')
        url = (
            'https://api.tomtom.com/traffic/services/5/incidentDetails'
            f"?key={TOMTOM_API_KEY}&bbox={bbox}&fields={fields}&language=en-GB&timeValidityFilter=present"
        )
        result = _get_json(url)
        if result.get('ok'):
            for item in result['data'].get('incidents', []):
                props = item.get('properties', {})
                delay = float(props.get('magnitudeOfDelay') or 0.0)
                coords = (item.get('geometry') or {}).get('coordinates')
                lat = None
                lon = None
                if isinstance(coords, list) and coords:
                    first = coords[0]
                    if isinstance(first, list) and len(first) >= 2:
                        lon = first[0]
                        lat = first[1]
                if not _is_in_blr(lat, lon):
                    continue
                intensity = min(1.0, max(0.2, delay / 1800.0))
                area_name = _tomtom_reverse_name(float(lat), float(lon))
                out.append(
                    {
                        'lat': float(lat),
                        'lon': float(lon),
                        'intensity': round(float(intensity), 3),
                        'delay_seconds': int(delay),
                        'label': area_name
                        or ((props.get('events') or [{}])[0].get('description'))
                        or 'Traffic congestion',
                        'source': 'tomtom',
                    }
                )

        if not out:
            out.extend(_live_flow_hotspots(limit=max(12, min(limit, 80))))

    # Blend in top dataset hotspots for stable city-level coverage.
    if traffic_df is not None:
        req_cols = {'Area Name', 'Road/Intersection Name', 'Traffic_Volume', 'Incident Reports', 'Latitude', 'Longitude'}
        if req_cols.issubset(set(traffic_df.columns)):
            g = (
                traffic_df.groupby(['Area Name', 'Road/Intersection Name'], dropna=False)
                .agg(
                    traffic_volume=('Traffic_Volume', 'mean'),
                    incidents=('Incident Reports', 'mean'),
                    lat=('Latitude', 'mean'),
                    lon=('Longitude', 'mean'),
                )
                .reset_index()
            )
            g['score'] = g['traffic_volume'].fillna(0) + (g['incidents'].fillna(0) * 120)
            top = g.sort_values('score', ascending=False).head(120)
            max_score = float(top['score'].max() or 1.0)
            for _, r in top.iterrows():
                lat = float(r['lat']) if pd.notna(r['lat']) else None
                lon = float(r['lon']) if pd.notna(r['lon']) else None
                if not _is_in_blr(lat, lon):
                    continue
                score = float(r['score']) if pd.notna(r['score']) else 0.0
                intensity = min(0.95, max(0.1, score / (max_score + 1e-6)))
                out.append(
                    {
                        'lat': lat,
                        'lon': lon,
                        'intensity': round(float(intensity), 3),
                        'delay_seconds': None,
                        'label': f"{r['Area Name']} - {r['Road/Intersection Name']}",
                        'source': 'dataset',
                    }
                )

    # Absolute fallback to ensure non-empty map (uses representative dataset points).
    if not out and traffic_df is not None and {'Area Name', 'Latitude', 'Longitude'}.issubset(set(traffic_df.columns)):
        g = (
            traffic_df.groupby('Area Name', dropna=False)[['Latitude', 'Longitude']]
            .mean(numeric_only=True)
            .dropna()
            .reset_index()
            .head(60)
        )
        for _, r in g.iterrows():
            lat = float(r['Latitude']) if pd.notna(r['Latitude']) else None
            lon = float(r['Longitude']) if pd.notna(r['Longitude']) else None
            if not _is_in_blr(lat, lon):
                continue
            out.append(
                {
                    'lat': lat,
                    'lon': lon,
                    'intensity': 0.35,
                    'delay_seconds': None,
                    'label': f"{r['Area Name']}",
                    'source': 'dataset',
                }
            )

    out = sorted(out, key=lambda x: x.get('intensity', 0), reverse=True)[: max(1, min(limit, 800))]
    return {'city': 'Bangalore', 'updated_at': datetime.now().isoformat(), 'count': len(out), 'heatspots': out}

@app.get('/nearby-places')
def nearby_places(
    lat: float = BLR_LAT,
    lon: float = BLR_LON,
    place_type: str = Query(default='hospital', alias='type'),
) -> dict[str, Any]:
    pt = (place_type or '').strip().lower()
    alias_map = {
        'hospital': 'hospital',
        'police': 'police',
        'fuel': 'petrol station',
        'gas': 'petrol station',
        'hotel': 'hotel',
        'hotels': 'hotel',
        'restaurant': 'restaurant',
        'restaurants': 'restaurant',
        'college': 'college',
        'colleges': 'college',
        'university': 'university',
        'school': 'school',
        'atm': 'atm',
        'pharmacy': 'pharmacy',
        'mall': 'shopping mall',
        'metro': 'metro station',
    }
    query_text = alias_map.get(pt, pt or 'hospital')

    # Primary: category search
    results = []
    if TOMTOM_API_KEY:
        url = (
            f"https://api.tomtom.com/search/2/categorySearch/{requests.utils.quote(query_text)}.json"
            f"?lat={lat}&lon={lon}&radius=5000&limit=12&key={TOMTOM_API_KEY}"
        )
        result = _get_json(url)
        results = result['data'].get('results', []) if result.get('ok') else []

        # Fallback: text search (improves hit rate for college/hotel etc.)
        if not results:
            text_url = (
                f"https://api.tomtom.com/search/2/search/{requests.utils.quote(query_text)}.json"
                f"?lat={lat}&lon={lon}&radius=5000&limit=12&key={TOMTOM_API_KEY}"
            )
            text_result = _get_json(text_url)
            if text_result.get('ok'):
                results = text_result['data'].get('results', [])

    # OpenTripMap fallback (free)
    if not results and OVM_API_KEY:
        bbox = f"{BLR_BBOX['min_lon']},{BLR_BBOX['min_lat']},{BLR_BBOX['max_lon']},{BLR_BBOX['max_lat']}"
        kind = query_text.replace(' ', '_')
        otm_url = (
            'https://api.opentripmap.com/0.1/en/places/bbox'
            f"?bbox={bbox}&kinds={requests.utils.quote(kind)}&apikey={OVM_API_KEY}&limit=12"
        )
        otm = _get_json(otm_url)
        if otm.get('ok'):
            features = otm['data'].get('features', [])
            for f in features:
                props = f.get('properties', {})
                name = props.get('name') or 'Place'
                geom = (f.get('geometry') or {}).get('coordinates') or [None, None]
                p_lon, p_lat = geom[0], geom[1]
                if not _is_in_blr(p_lat, p_lon):
                    continue
                results.append(
                    {
                        'position': {'lat': p_lat, 'lon': p_lon},
                        'poi': {'name': name},
                        'address': {'freeformAddress': props.get('address') or name},
                    }
                )

    out = []
    limit = 12
    for r in results:
        pos = r.get('position', {})
        p_lat = pos.get('lat')
        p_lon = pos.get('lon')
        if not _is_in_blr(p_lat, p_lon):
            continue
        out.append(
            {
                'name': r.get('poi', {}).get('name') or r.get('address', {}).get('freeformAddress'),
                'address': r.get('address', {}).get('freeformAddress'),
                'lat': p_lat,
                'lon': p_lon,
            }
        )
    return {'places': out[:limit], 'query': query_text}


@app.get('/traffic-tiles-template')
def traffic_tiles_template() -> dict[str, Any]:
    _require_key(TOMTOM_API_KEY, 'TOMTOM_API_KEY')
    return {
        'url_template': (
            'https://api.tomtom.com/traffic/map/4/tile/flow/relative0/{z}/{x}/{y}.png'
            f'?key={TOMTOM_API_KEY}'
        )
    }


@app.post('/route-plan')
def route_plan(payload: RoutePlanRequest) -> dict[str, Any]:
    _require_key(TOMTOM_API_KEY, 'TOMTOM_API_KEY')

    src_geo = _resolve_location_any_provider(payload.source_text)
    dst_geo = _resolve_location_any_provider(payload.destination_text)
    if not src_geo.get('ok') or not dst_geo.get('ok'):
        raise HTTPException(
            status_code=400,
            detail='Unable to resolve source/destination inside Bangalore. Try selecting from suggestions.',
        )

    s = src_geo['data']
    d = dst_geo['data']

    depart_at = datetime.now(timezone.utc) + timedelta(minutes=payload.depart_in_minutes)
    depart_iso = depart_at.replace(microsecond=0).isoformat().replace('+00:00', 'Z')

    route_locations = f"{s['lat']},{s['lon']}:{d['lat']},{d['lon']}"
    primary_routes = _tomtom_route_fetch(
        route_locations,
        depart_iso,
        traffic=True,
        route_type='fastest',
        max_alternatives=3,
    )

    # If we didn't get enough distinct alternatives, try additional real variants.
    routes_raw: list[dict[str, Any]] = []
    routes_raw.extend(primary_routes)
    if len(routes_raw) < 3:
        routes_raw.extend(
            _tomtom_route_fetch(route_locations, depart_iso, traffic=True, route_type='shortest', max_alternatives=2)
        )
    if len(routes_raw) < 3:
        routes_raw.extend(
            _tomtom_route_fetch(route_locations, depart_iso, traffic=False, route_type='fastest', max_alternatives=2)
        )

    if not routes_raw:
        if ORS_API_KEY:
            routes = []
            ors_alts = _ors_alternative_routes(float(s['lat']), float(s['lon']), float(d['lat']), float(d['lon']), target_count=3)
            routes.extend(ors_alts)
            if len(routes) < 3:
                for avoid in (['highways'], ['tollways']):
                    alt = _ors_route_single(float(s['lat']), float(s['lon']), float(d['lat']), float(d['lon']), avoid_features=avoid)
                    if alt:
                        routes.append(alt)
                    if len(routes) >= 3:
                        break
            if not routes:
                ors = _ors_route(float(s['lat']), float(s['lon']), float(d['lat']), float(d['lon']))
                if not ors.get('ok'):
                    raise HTTPException(status_code=502, detail=ors.get('error', 'Routing failed'))
                routes = [
                    {
                        'rank': 1,
                        'distance_km': ors['data']['distance_km'],
                        'travel_minutes': ors['data']['travel_minutes'],
                        'traffic_delay_minutes': 0.0,
                        'delay_source': 'ors_no_traffic',
                        'route_score': int(ors['data']['travel_minutes'] * 60),
                        'advice': 'Best route (no live traffic)',
                        'polyline': ors['data']['polyline'],
                    }
                ]
            routes = sorted(routes, key=lambda x: x['route_score'])
            if routes:
                routes[0]['advice'] = 'Best route (no live traffic)'
            weather_data = None
            if OPENWEATHER_API_KEY:
                weather_url = (
                    'https://api.openweathermap.org/data/2.5/weather'
                    f"?lat={s['lat']}&lon={s['lon']}&appid={OPENWEATHER_API_KEY}&units=metric"
                )
                w = _get_json(weather_url)
                if w.get('ok'):
                    weather_data = {
                        'at_source': (w['data'].get('weather') or [{}])[0].get('description'),
                        'temp_c': w['data'].get('main', {}).get('temp'),
                    }
            return {
                'city_focus': 'Bangalore',
                'source': s,
                'destination': d,
                'depart_at_utc': depart_iso,
                'routes': routes,
                'weather': weather_data,
                'alternatives_meta': {
                    'total_routes': len(routes),
                    'tomtom_primary': 0,
                    'ors_added': len(routes),
                },
            }
        raise HTTPException(status_code=502, detail='Routing failed')

    routes = []
    # De-dup routes by polyline signature so we only keep distinct paths.
    seen = set()
    deduped = []
    for r in routes_raw:
        line = _extract_route_polyline(r)
        if not line or len(line) < 2:
            continue
        sig = tuple((round(p[0], 5), round(p[1], 5)) for p in line[:: max(1, len(line) // 30)])
        if sig in seen:
            continue
        seen.add(sig)
        r = dict(r)
        r['_polyline_cache'] = line
        deduped.append(r)
        if len(deduped) >= 3:
            break

    for idx, route in enumerate(deduped, start=1):
        packed = _score_and_pack_route(route, idx)
        if route.get('_polyline_cache'):
            packed['polyline'] = route['_polyline_cache']
        routes.append(packed)

    # If still short, supplement with ORS alternative routes (real, but no live traffic).
    ors_alt_count = 0
    if len(routes) < 3 and ORS_API_KEY:
        ors_alts = _ors_alternative_routes(float(s['lat']), float(s['lon']), float(d['lat']), float(d['lon']), target_count=3)
        if ors_alts:
            seen = set()
            for r in routes:
                sig = tuple((round(p[0], 5), round(p[1], 5)) for p in r['polyline'][:: max(1, len(r['polyline']) // 30)])
                seen.add(sig)
            for r in ors_alts:
                line = r.get('polyline') or []
                if not line:
                    continue
                sig = tuple((round(p[0], 5), round(p[1], 5)) for p in line[:: max(1, len(line) // 30)])
                if sig in seen:
                    continue
                seen.add(sig)
                routes.append(r)
                ors_alt_count += 1
                if len(routes) >= 3:
                    break

    # If still short, try ORS single routes with avoid features to create true alternates.
    if len(routes) < 3 and ORS_API_KEY:
        seen = set()
        for r in routes:
            sig = tuple((round(p[0], 5), round(p[1], 5)) for p in r['polyline'][:: max(1, len(r['polyline']) // 30)])
            seen.add(sig)
        for avoid in (['highways'], ['tollways']):
            alt = _ors_route_single(float(s['lat']), float(s['lon']), float(d['lat']), float(d['lon']), avoid_features=avoid)
            if not alt:
                continue
            line = alt.get('polyline') or []
            sig = tuple((round(p[0], 5), round(p[1], 5)) for p in line[:: max(1, len(line) // 30)])
            if sig in seen:
                continue
            seen.add(sig)
            routes.append(alt)
            ors_alt_count += 1
            if len(routes) >= 3:
                break

    routes = sorted(routes, key=lambda x: x['route_score'])
    if routes:
        routes[0]['advice'] = 'Best route (least traffic impact)'

    weather_data = None
    if OPENWEATHER_API_KEY:
        weather_url = (
            'https://api.openweathermap.org/data/2.5/weather'
            f"?lat={s['lat']}&lon={s['lon']}&appid={OPENWEATHER_API_KEY}&units=metric"
        )
        w = _get_json(weather_url)
        if w.get('ok'):
            weather_data = {
                'at_source': (w['data'].get('weather') or [{}])[0].get('description'),
                'temp_c': w['data'].get('main', {}).get('temp'),
            }

    return {
        'city_focus': 'Bangalore',
        'source': s,
        'destination': d,
        'depart_at_utc': depart_iso,
        'routes': routes,
        'weather': weather_data,
        'alternatives_meta': {
            'total_routes': len(routes),
            'tomtom_primary': len(primary_routes),
            'ors_added': ors_alt_count,
        },
    }


@app.get('/traffic-forecast')
def traffic_forecast(source_area: str | None = None) -> dict[str, Any]:
    if not bundle:
        raise HTTPException(status_code=503, detail='Model bundle not available')

    feature_cols = bundle['feature_cols']
    area_to_roads = bundle.get('area_to_roads', {})
    traffic_model = bundle['traffic_model']

    chosen_area = _resolve_source_area(source_area)
    if chosen_area and chosen_area in area_to_roads:
        areas = [chosen_area]
    else:
        areas = list(area_to_roads.keys())[:8]
    if not areas:
        raise HTTPException(status_code=503, detail='No areas available for forecast')

    now_local = datetime.now()

    # Use next 18 hours as trend horizon for same-day and near-future reading.
    trend_points: list[dict[str, Any]] = []
    raw_values: list[float] = []

    def predict_at(dt_local: datetime) -> float:
        rows = []
        for area in areas:
            roads = area_to_roads.get(area, [])
            if not roads:
                continue
            road = roads[0]
            dest = roads[1] if len(roads) > 1 else road
            lat, lon = AREA_GEO.get(area, (BLR_LAT, BLR_LON))
            rows.append(
                {
                    'Source_Area': area,
                    'Destination': dest,
                    'Road/Intersection Name': road,
                    'Hour': dt_local.hour,
                    'is_peak_hour': 1 if dt_local.hour in [8, 9, 10, 17, 18, 19] else 0,
                    'day_of_week': dt_local.weekday(),
                    'month': dt_local.month,
                    'day': dt_local.day,
                    'Latitude': lat,
                    'Longitude': lon,
                }
            )
        if not rows:
            return 0.0
        df = pd.DataFrame(rows)
        pred = traffic_model.predict(df[feature_cols])
        return float(pred.mean())

    for h in range(0, 19):
        dt = now_local + timedelta(hours=h)
        v = predict_at(dt)
        raw_values.append(v)
        trend_points.append(
            {
                'label': dt.strftime('%I %p').lstrip('0'),
                'hour': int(dt.hour),
                'predicted_volume': round(v, 2),
            }
        )

    low_q = float(pd.Series(raw_values).quantile(0.45)) if raw_values else 0.0
    high_q = float(pd.Series(raw_values).quantile(0.75)) if raw_values else 0.0

    for p in trend_points:
        p['level'] = _traffic_level(float(p['predicted_volume']), low_q, high_q)

    later_dt = now_local + timedelta(hours=2)
    tomorrow_dt = (now_local + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
    day3_dt = (now_local + timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)
    card_dts = [('Later Today', later_dt), ('Tomorrow', tomorrow_dt), (day3_dt.strftime('%A'), day3_dt)]

    cards = []
    for title, dt in card_dts:
        v = predict_at(dt)
        cards.append(
            {
                'title': title,
                'time': dt.strftime('%I:%M %p').lstrip('0'),
                'window': f"{dt.strftime('%I:%M %p').lstrip('0')} - {(dt + timedelta(hours=2)).strftime('%I:%M %p').lstrip('0')}",
                'predicted_volume': round(v, 2),
                'level': _traffic_level(v, low_q, high_q),
            }
        )

    return {
        'city': 'Bangalore',
        'area_focus': chosen_area or 'City-wide',
        'generated_at': now_local.isoformat(),
        'source': 'model',
        'cards': cards,
        'trend': trend_points,
        'quantiles': {'low': round(low_q, 2), 'high': round(high_q, 2)},
    }


@app.get('/areas')
def areas() -> dict[str, Any]:
    if not bundle:
        raise HTTPException(status_code=503, detail='Model bundle not available')
    area_to_roads = bundle.get('area_to_roads', {})
    return {'areas': sorted(area_to_roads.keys())}


@app.post('/predict')
def predict(payload: PredictRequest) -> dict[str, Any]:
    if not bundle:
        raise HTTPException(status_code=503, detail='Model bundle not available')

    feature_cols = bundle['feature_cols']
    area_to_roads = bundle['area_to_roads']
    roads = area_to_roads.get(payload.source_area, [])
    if not roads:
        raise HTTPException(status_code=400, detail='Unknown source_area')

    is_peak_hour = 1 if payload.hour in [8, 9, 10, 17, 18, 19] else 0
    candidates = pd.DataFrame(
        {
            'Source_Area': [payload.source_area] * len(roads),
            'Destination': [payload.destination] * len(roads),
            'Road/Intersection Name': roads,
            'Hour': [payload.hour] * len(roads),
            'is_peak_hour': [is_peak_hour] * len(roads),
            'day_of_week': [payload.day_of_week] * len(roads),
            'month': [payload.month] * len(roads),
            'day': [payload.day] * len(roads),
            'Latitude': [payload.latitude] * len(roads),
            'Longitude': [payload.longitude] * len(roads),
        }
    )

    x = candidates[feature_cols]
    candidates['pred_traffic'] = bundle['traffic_model'].predict(x)
    candidates['pred_death'] = bundle['death_model'].predict(x)
    candidates['pred_level'] = bundle['traffic_level_model'].predict(x)
    candidates['pred_weather'] = bundle['weather_model'].predict(x)

    traffic_norm = candidates['pred_traffic'] / (candidates['pred_traffic'].max() + 1e-6)
    death_norm = candidates['pred_death'] / (candidates['pred_death'].max() + 1e-6)
    candidates['route_score'] = traffic_norm + death_norm

    ranked = candidates.sort_values('route_score', ascending=True).head(5)
    best = ranked.iloc[0]

    return {
        'best_route': {
            'road': str(best['Road/Intersection Name']),
            'route_score': float(best['route_score']),
            'predicted_traffic_volume': float(best['pred_traffic']),
            'predicted_death_count': float(best['pred_death']),
            'predicted_traffic_level': str(best['pred_level']),
            'predicted_weather_condition': str(best['pred_weather']),
        },
        'alternatives': ranked[
            [
                'Road/Intersection Name',
                'route_score',
                'pred_traffic',
                'pred_death',
                'pred_level',
                'pred_weather',
            ]
        ]
        .rename(
            columns={
                'Road/Intersection Name': 'road',
                'pred_traffic': 'predicted_traffic_volume',
                'pred_death': 'predicted_death_count',
                'pred_level': 'predicted_traffic_level',
                'pred_weather': 'predicted_weather_condition',
            }
        )
        .to_dict(orient='records'),
    }
