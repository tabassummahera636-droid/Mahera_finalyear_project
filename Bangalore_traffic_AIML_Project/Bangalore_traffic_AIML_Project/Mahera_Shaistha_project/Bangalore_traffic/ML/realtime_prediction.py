import argparse
import json
from pathlib import Path

import joblib
import pandas as pd


def load_bundle() -> dict:
    model_path = Path(__file__).resolve().parent / "route_realtime_model.pkl"
    if not model_path.exists():
        raise FileNotFoundError(f"Model bundle not found: {model_path}")
    return joblib.load(model_path)


def score_routes(bundle: dict, source_area: str, destination: str, hour: int, day_of_week: int, month: int, day: int, latitude: float, longitude: float) -> pd.DataFrame:
    area_to_roads = bundle["area_to_roads"]
    feature_cols = bundle["feature_cols"]

    roads = area_to_roads.get(source_area, [])
    if not roads:
        raise ValueError(f"Unknown source area: {source_area}")

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

    x = candidates[feature_cols]
    candidates["Predicted_Traffic_Volume"] = bundle["traffic_model"].predict(x)
    candidates["Predicted_Death_Count"] = bundle["death_model"].predict(x)
    candidates["Predicted_Traffic_Level"] = bundle["traffic_level_model"].predict(x)
    candidates["Predicted_Weather_Condition"] = bundle["weather_model"].predict(x)

    traffic_norm = candidates["Predicted_Traffic_Volume"] / (candidates["Predicted_Traffic_Volume"].max() + 1e-6)
    death_norm = candidates["Predicted_Death_Count"] / (candidates["Predicted_Death_Count"].max() + 1e-6)
    candidates["Route_Score"] = traffic_norm + death_norm

    return candidates.sort_values("Route_Score").reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Realtime route scoring (CLI)")
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--hour", type=int, required=True)
    parser.add_argument("--dow", type=int, required=True, help="Day of week: Monday=0")
    parser.add_argument("--month", type=int, required=True)
    parser.add_argument("--day", type=int, required=True)
    parser.add_argument("--lat", type=float, default=12.9716)
    parser.add_argument("--lon", type=float, default=77.5946)
    args = parser.parse_args()

    bundle = load_bundle()
    result = score_routes(
        bundle,
        source_area=args.source,
        destination=args.destination,
        hour=args.hour,
        day_of_week=args.dow,
        month=args.month,
        day=args.day,
        latitude=args.lat,
        longitude=args.lon,
    )

    print(json.dumps(result.head(5).to_dict(orient="records"), default=str, indent=2))


if __name__ == "__main__":
    main()
