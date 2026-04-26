from datetime import date
import os
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st
import joblib
from realtime_api_clients import ApiConfig, RealtimeApiClient


st.set_page_config(page_title="Bangalore Traffic Route Predictor", layout="wide")
st.markdown(
    """
    <style>
    .stApp {
        background-image:
            linear-gradient(125deg, rgba(9, 33, 64, 0.88), rgba(28, 95, 126, 0.82)),
            url("https://images.unsplash.com/photo-1477959858617-67f85cf4f1df?auto=format&fit=crop&w=1800&q=80");
        background-size: cover;
        background-position: center;
        background-attachment: fixed;
        color: #f3f7fc;
    }
    .banner {
        background: linear-gradient(120deg, rgba(10, 38, 71, 0.95) 0%, rgba(15, 121, 123, 0.93) 100%);
        padding: 1.1rem 1.3rem;
        border-radius: 16px;
        color: #ffffff;
        margin-bottom: 1rem;
        box-shadow: 0 10px 26px rgba(3, 14, 30, 0.35);
        border: 1px solid rgba(255, 255, 255, 0.18);
        backdrop-filter: blur(6px);
    }
    .banner h2 {
        margin: 0;
        font-size: 1.75rem;
        letter-spacing: 0.3px;
    }
    .banner p {
        margin: 0.2rem 0 0 0;
        opacity: 0.95;
    }
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, rgba(5, 22, 43, 0.95), rgba(9, 56, 67, 0.95));
        border-right: 1px solid rgba(255, 255, 255, 0.12);
    }
    [data-testid="stSidebar"] * {
        color: #eaf4ff !important;
    }
    [data-testid="stSidebar"] [data-baseweb="select"] > div,
    [data-testid="stSidebar"] .stDateInput > div > div,
    [data-testid="stSidebar"] .stSlider > div > div {
        background: rgba(255, 255, 255, 0.12) !important;
        border: 1px solid rgba(255, 255, 255, 0.35) !important;
        border-radius: 10px !important;
    }
    [data-testid="stSidebar"] [data-baseweb="select"] > div:hover,
    [data-testid="stSidebar"] .stDateInput > div > div:hover {
        border: 1px solid #84d6ff !important;
    }
    [data-testid="stSidebar"] .stButton > button {
        width: 100%;
        border-radius: 10px;
        border: none;
        background: linear-gradient(90deg, #38bdf8, #22d3ee);
        color: #04263b !important;
        font-weight: 700;
        box-shadow: 0 6px 16px rgba(6, 182, 212, 0.35);
    }
    [data-testid="stSidebar"] .stButton > button:hover {
        transform: translateY(-1px);
        filter: brightness(1.05);
    }
    .card {
        background: linear-gradient(180deg, rgba(255, 255, 255, 0.96), rgba(245, 251, 255, 0.96));
        border-radius: 12px;
        padding: 1rem;
        border-top: 4px solid #1dc6da;
        box-shadow: 0 8px 20px rgba(9, 27, 56, 0.2);
        margin-bottom: 0.7rem;
    }
    .card .label {
        color: #12355b;
        font-size: 0.88rem;
        font-weight: 600;
    }
    .card .value {
        color: #0b132b;
        font-size: 1.35rem;
        font-weight: 800;
        margin-top: 0.2rem;
    }
    .api-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 10px;
        margin-top: 8px;
        margin-bottom: 8px;
    }
    .api-box {
        border-radius: 12px;
        padding: 0.8rem 0.9rem;
        color: #07233a;
        font-weight: 600;
        border: 1px solid rgba(255, 255, 255, 0.6);
        box-shadow: 0 6px 14px rgba(0, 0, 0, 0.12);
    }
    .api-box h4 {
        margin: 0 0 0.25rem 0;
        color: #05233d;
        font-size: 1rem;
    }
    .api-1 { background: linear-gradient(120deg, #fff176, #ffd54f); }
    .api-2 { background: linear-gradient(120deg, #80deea, #4dd0e1); }
    .api-3 { background: linear-gradient(120deg, #a5d6a7, #81c784); }
    .api-4 { background: linear-gradient(120deg, #ffccbc, #ffab91); }
    .api-5 { background: linear-gradient(120deg, #d1c4e9, #b39ddb); }
    .api-6 { background: linear-gradient(120deg, #ffe082, #ffca28); }
    .api-7 { background: linear-gradient(120deg, #b2ebf2, #80deea); }
    .api-8 { background: linear-gradient(120deg, #f8bbd0, #f48fb1); }
    [data-testid="stDataFrame"] {
        background: rgba(255, 255, 255, 0.92);
        border-radius: 12px;
        padding: 0.4rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="banner">
        <h2>Bangalore Traffic Route Predictor</h2>
        <p>Professional view for best route, weather condition, traffic level, and death count.</p>
    </div>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def load_bundle():
    base_dir = Path(__file__).resolve().parent
    model_path = base_dir / "route_realtime_model.pkl"
    if not model_path.exists():
        alt_path = base_dir.parent / "ML" / "route_realtime_model.pkl"
        if alt_path.exists():
            model_path = alt_path
    if not model_path.exists():
        raise FileNotFoundError("route_realtime_model.pkl not found in ML folder.")
    return joblib.load(model_path)


bundle = load_bundle()
feature_cols = bundle["feature_cols"]
area_to_roads = bundle["area_to_roads"]

DEFAULT_TOMTOM_KEY = "r6cGoI3Uzjtz9axNoohhkRRTkvv80hBN"
DEFAULT_OPENWEATHER_KEY = ""
DEFAULT_GOOGLE_MAPS_KEY = ""


def score_routes(source_area, destination, hour, query_date, latitude=12.9716, longitude=77.5946):
    roads = area_to_roads.get(source_area, [])
    if not roads:
        return None

    day_of_week = query_date.weekday()
    month = query_date.month
    day = query_date.day
    is_peak_hour = 1 if hour in [8, 9, 10, 17, 18, 19] else 0

    candidates = pd.DataFrame(
        {
            "Source_Area": [source_area] * len(roads),
            "Destination": [destination] * len(roads),
            "Road/Intersection Name": roads,
            "Hour": [hour] * len(roads),
            "is_peak_hour": [is_peak_hour] * len(roads),
            "day_of_week": [day_of_week] * len(roads),
            "month": [month] * len(roads),
            "day": [day] * len(roads),
            "Latitude": [latitude] * len(roads),
            "Longitude": [longitude] * len(roads),
        }
    )

    X = candidates[feature_cols]
    traffic_pred = bundle["traffic_model"].predict(X)
    death_pred = bundle["death_model"].predict(X)
    level_pred = bundle["traffic_level_model"].predict(X)
    weather_pred = bundle["weather_model"].predict(X)

    traffic_norm = traffic_pred / (np.max(traffic_pred) + 1e-6)
    death_norm = death_pred / (np.max(death_pred) + 1e-6)
    route_score = traffic_norm + death_norm

    candidates["Predicted_Traffic_Volume"] = traffic_pred
    candidates["Predicted_Death_Count"] = death_pred
    candidates["Predicted_Traffic_Level"] = level_pred
    candidates["Predicted_Weather_Condition"] = weather_pred
    candidates["Route_Score"] = route_score

    candidates = candidates.sort_values("Route_Score", ascending=True).reset_index(drop=True)
    return candidates


areas = sorted(list(area_to_roads.keys()))
if not areas:
    st.error("No areas found in model bundle.")
    st.stop()

st.sidebar.markdown("### Prediction Inputs")
source_area = st.sidebar.selectbox("Source Area", areas)
destination_options = sorted(area_to_roads.get(source_area, []))
destination = st.sidebar.selectbox("Destination", destination_options)
selected_date = st.sidebar.date_input("Date", value=date.today())
selected_hour = st.sidebar.slider("Hour", min_value=0, max_value=23, value=9)
source_location_query = st.sidebar.text_input("Source Location Text", value=f"{source_area}, Bangalore")
destination_location_query = st.sidebar.text_input(
    "Destination Location Text", value=f"{destination}, Bangalore"
)

with st.sidebar.expander("API Keys (Auto-Filled)", expanded=False):
    tomtom_key = st.text_input(
        "TomTom API Key",
        value=os.getenv("TOMTOM_API_KEY", DEFAULT_TOMTOM_KEY),
        type="password",
    )
    openweather_key = st.text_input(
        "OpenWeather API Key",
        value=os.getenv("OPENWEATHER_API_KEY", DEFAULT_OPENWEATHER_KEY),
        type="password",
    )
    google_maps_key = st.text_input(
        "Google Maps API Key",
        value=os.getenv("GOOGLE_MAPS_API_KEY", DEFAULT_GOOGLE_MAPS_KEY),
        type="password",
    )
st.sidebar.caption("Keys are prefilled. Click Predict directly.")

if st.sidebar.button("Predict"):
    api_client = RealtimeApiClient(
        ApiConfig(
            tomtom_key=tomtom_key.strip(),
            openweather_key=openweather_key.strip(),
            google_maps_key=google_maps_key.strip(),
        )
    )

    source_geo = api_client.geocode(source_location_query)
    destination_geo = api_client.geocode(destination_location_query)

    src_lat, src_lon = 12.9716, 77.5946
    dst_lat, dst_lon = 12.9716, 77.5946
    if source_geo.get("ok"):
        results = source_geo.get("data", {}).get("results", [])
        if results:
            pos = results[0].get("position", {})
            src_lat = float(pos.get("lat", src_lat))
            src_lon = float(pos.get("lon", src_lon))
    if destination_geo.get("ok"):
        results = destination_geo.get("data", {}).get("results", [])
        if results:
            pos = results[0].get("position", {})
            dst_lat = float(pos.get("lat", dst_lat))
            dst_lon = float(pos.get("lon", dst_lon))

    table = score_routes(
        source_area,
        destination,
        selected_hour,
        selected_date,
        latitude=src_lat,
        longitude=src_lon,
    )
    if table is None or table.empty:
        st.error("Could not generate route predictions for this area.")
        st.stop()

    # Real-time API integrations (first 8 APIs)
    weather_live = api_client.get_weather(src_lat, src_lon)
    route_opt_live = api_client.get_tomtom_route(src_lat, src_lon, dst_lat, dst_lon)
    flow_live = api_client.get_flow_segment(src_lat, src_lon)
    incidents_live = api_client.get_incidents_bbox(src_lon - 0.03, src_lat - 0.03, src_lon + 0.03, src_lat + 0.03)
    google_route_live = api_client.get_google_directions(source_location_query, destination_location_query)
    tile_live = api_client.get_flow_tile_url(src_lat, src_lon, zoom=12)
    poi_live = api_client.get_nearby_places(src_lat, src_lon, place_type="hospital")

    best = table.iloc[0]
    st.success("Prediction generated")

    best_route = str(best["Road/Intersection Name"])
    best_weather = str(best["Predicted_Weather_Condition"])
    best_level = str(best["Predicted_Traffic_Level"])
    best_death = int(round(float(best["Predicted_Death_Count"])))
    best_traffic = int(round(float(best["Predicted_Traffic_Volume"])))

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.markdown(
        f'<div class="card"><div class="label">Best Route</div><div class="value">{best_route}</div></div>',
        unsafe_allow_html=True,
    )
    c2.markdown(
        f'<div class="card"><div class="label">Weather</div><div class="value">{best_weather}</div></div>',
        unsafe_allow_html=True,
    )
    c3.markdown(
        f'<div class="card"><div class="label">Traffic Level</div><div class="value">{best_level}</div></div>',
        unsafe_allow_html=True,
    )
    c4.markdown(
        f'<div class="card"><div class="label">Death Count</div><div class="value">{best_death}</div></div>',
        unsafe_allow_html=True,
    )
    c5.markdown(
        f'<div class="card"><div class="label">Traffic Volume</div><div class="value">{best_traffic}</div></div>',
        unsafe_allow_html=True,
    )

    st.subheader("Additional Details")
    view_table = table[
        [
            "Road/Intersection Name",
            "Predicted_Traffic_Volume",
            "Predicted_Traffic_Level",
            "Predicted_Death_Count",
            "Predicted_Weather_Condition",
            "Route_Score",
        ]
    ].copy()
    view_table["Predicted_Traffic_Volume"] = (
        view_table["Predicted_Traffic_Volume"].round(0).astype(int)
    )
    view_table["Predicted_Death_Count"] = (
        view_table["Predicted_Death_Count"].round(0).astype(int)
    )
    view_table["Route_Score"] = (view_table["Route_Score"] * 100).round(0).astype(int)
    view_table = view_table.rename(columns={"Route_Score": "Risk_Score"})

    st.dataframe(
        view_table,
        use_container_width=True,
    )

    st.subheader("Real-Time API Results")
    r1, r2, r3, r4 = st.columns(4)
    live_speed = None
    live_incident_count = 0
    live_weather = "N/A"
    live_distance_km = "N/A"

    if flow_live.get("ok"):
        flow_data = flow_live["data"].get("flowSegmentData", {})
        live_speed = int(round(float(flow_data.get("currentSpeed", 0))))
    if incidents_live.get("ok"):
        incident_items = incidents_live["data"].get("incidents", [])
        if isinstance(incident_items, list):
            live_incident_count = int(len(incident_items))
    if weather_live.get("ok"):
        wdata = weather_live.get("data", {})
        weather_items = wdata.get("weather", [])
        if weather_items:
            live_weather = str(weather_items[0].get("main", "N/A"))
        elif "current" in wdata and "weather_code" in wdata["current"]:
            live_weather = f"Code {wdata['current']['weather_code']}"
    if route_opt_live.get("ok"):
        routes = route_opt_live["data"].get("routes", [])
        if routes:
            length_m = float(routes[0].get("summary", {}).get("lengthInMeters", 0))
            live_distance_km = int(round(length_m / 1000))

    r1.metric("Live Speed (km/h)", int(live_speed) if live_speed is not None else "N/A")
    r2.metric("Live Incidents", int(live_incident_count))
    r3.metric("Live Weather", live_weather)
    r4.metric("Route Distance (km)", live_distance_km)

    source_geo_ok = "OK" if source_geo.get("ok") else "FAILED"
    destination_geo_ok = "OK" if destination_geo.get("ok") else "FAILED"
    flow_ok = "OK" if flow_live.get("ok") else "FAILED"
    weather_ok = "OK" if weather_live.get("ok") else "FAILED"
    incidents_ok = "OK" if incidents_live.get("ok") else "FAILED"
    route_ok = "OK" if route_opt_live.get("ok") else "FAILED"
    google_route_ok = "OK" if google_route_live.get("ok") else "FAILED"
    tile_ok = "OK" if tile_live.get("ok") else "FAILED"
    poi_ok = "OK" if poi_live.get("ok") else "FAILED"

    st.markdown(
        f"""
        <div class="api-grid">
            <div class="api-box api-1"><h4>1) Weather API</h4>Status: {weather_ok}<br/>Value: {live_weather}</div>
            <div class="api-box api-2"><h4>2) Route Optimizing API</h4>Status: {route_ok}<br/>Distance: {live_distance_km} km</div>
            <div class="api-box api-3"><h4>3) Traffic Data API</h4>Status: {flow_ok}<br/>Speed: {int(live_speed) if live_speed is not None else 'N/A'} km/h</div>
            <div class="api-box api-4"><h4>4) Incident API</h4>Status: {incidents_ok}<br/>Incidents: {int(live_incident_count)}</div>
            <div class="api-box api-5"><h4>5) Geocoding API</h4>Source: {source_geo_ok}<br/>Destination: {destination_geo_ok}</div>
            <div class="api-box api-6"><h4>6) Google Maps API</h4>Status: {google_route_ok}</div>
            <div class="api-box api-7"><h4>7) Traffic Tile API</h4>Status: {tile_ok}</div>
            <div class="api-box api-8"><h4>8) POI API</h4>Status: {poi_ok}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.expander("Integrated APIs (1 to 8)"):
        st.markdown("1. Weather API")
        st.json(weather_live)
        st.markdown("2. Route Optimizing API (TomTom)")
        st.json(route_opt_live)
        st.markdown("3. Traffic Data API (Flow Segment)")
        st.json(flow_live)
        st.markdown("4. Incident / Accident API")
        st.json(incidents_live)
        st.markdown("5. Geocoding API")
        st.json({"source_geocode": source_geo, "destination_geocode": destination_geo})
        st.markdown("6. Google Map API (Directions)")
        st.json(google_route_live)
        st.markdown("7. Traffic Density / Tile API")
        st.json(tile_live)
        st.markdown("8. Places / POI API")
        st.json(poi_live)

st.subheader("Model Metrics")
st.json(bundle.get("metrics", {}))
