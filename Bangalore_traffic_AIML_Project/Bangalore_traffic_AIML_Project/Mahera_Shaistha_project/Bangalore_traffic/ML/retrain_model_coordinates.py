import joblib
import numpy as np
import pandas as pd
from pathlib import Path
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.metrics import accuracy_score, mean_absolute_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder


BASE_DIR = Path.cwd()
PROJECT_ROOT = BASE_DIR.parent if BASE_DIR.name == "ML" else BASE_DIR

INPUT_DATA_PATH = PROJECT_ROOT / "data" / "bangalore_traffic_with_coordinates_FINAL.csv"
AUGMENTED_DATA_PATH = PROJECT_ROOT / "data" / "bangalore_traffic_with_destination_time.csv"
MODEL_BUNDLE_PATH = PROJECT_ROOT / "ML" / "route_realtime_model.pkl"
PREDICTION_OUTPUT_PATH = PROJECT_ROOT / "data" / "predicted_realtime_route_outputs.csv"


def add_destination_and_time_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    df = df.dropna(subset=["Date", "Area Name", "Road/Intersection Name"]).copy()

    df["Source_Area"] = df["Area Name"]

    # Deterministic destination generation by rotating road names inside each area.
    roads_by_area = (
        df.groupby("Source_Area")["Road/Intersection Name"]
        .apply(lambda s: sorted(s.unique().tolist()))
        .to_dict()
    )
    road_to_destination = {}
    for area, roads in roads_by_area.items():
        if len(roads) == 1:
            road_to_destination[(area, roads[0])] = roads[0]
            continue
        for idx, road in enumerate(roads):
            road_to_destination[(area, road)] = roads[(idx + 1) % len(roads)]

    df["Destination"] = df.apply(
        lambda row: road_to_destination[(row["Source_Area"], row["Road/Intersection Name"])],
        axis=1,
    )

    df["day_of_week"] = df["Date"].dt.dayofweek
    df["month"] = df["Date"].dt.month
    df["day"] = df["Date"].dt.day

    # No hour exists in source data. Create a reproducible synthetic hour feature.
    df["Hour"] = ((df.index.to_series() * 7) + (df["day_of_week"] * 3)) % 24
    df["Time"] = df["Hour"].astype(int).astype(str).str.zfill(2) + ":00"
    df["is_peak_hour"] = df["Hour"].isin([8, 9, 10, 17, 18, 19]).astype(int)

    # Target bucket for classification output.
    df["Traffic_Level"] = pd.qcut(
        df["Traffic_Volume"],
        q=3,
        labels=["Low", "Medium", "High"],
        duplicates="drop",
    ).astype(str)

    return df


def build_models(df: pd.DataFrame):
    feature_cols = [
        "Source_Area",
        "Destination",
        "Road/Intersection Name",
        "Hour",
        "is_peak_hour",
        "day_of_week",
        "month",
        "day",
        "Latitude",
        "Longitude",
    ]
    categorical_features = ["Source_Area", "Destination", "Road/Intersection Name"]
    numeric_features = [
        "Hour",
        "is_peak_hour",
        "day_of_week",
        "month",
        "day",
        "Latitude",
        "Longitude",
    ]

    model_df = df.dropna(
        subset=feature_cols
        + ["Traffic_Volume", "Traffic_Level", "death_risk_index", "Weather Conditions"]
    ).copy()
    X = model_df[feature_cols]

    y_traffic = model_df["Traffic_Volume"]
    y_level = model_df["Traffic_Level"]
    y_death = model_df["death_risk_index"]
    y_weather = model_df["Weather Conditions"]

    X_train, X_test, y_t_train, y_t_test = train_test_split(
        X, y_traffic, test_size=0.2, random_state=42
    )
    _, _, y_l_train, y_l_test = train_test_split(
        X, y_level, test_size=0.2, random_state=42
    )
    _, _, y_d_train, y_d_test = train_test_split(
        X, y_death, test_size=0.2, random_state=42
    )
    _, _, y_w_train, y_w_test = train_test_split(
        X, y_weather, test_size=0.2, random_state=42
    )

    preprocessor = ColumnTransformer(
        transformers=[
            ("num", "passthrough", numeric_features),
            ("cat", OneHotEncoder(handle_unknown="ignore"), categorical_features),
        ]
    )

    traffic_model = Pipeline(
        [
            ("preprocess", preprocessor),
            ("model", RandomForestRegressor(n_estimators=250, random_state=42, n_jobs=-1)),
        ]
    )
    traffic_level_model = Pipeline(
        [
            ("preprocess", preprocessor),
            ("model", RandomForestClassifier(n_estimators=250, random_state=42, n_jobs=-1)),
        ]
    )
    death_model = Pipeline(
        [
            ("preprocess", preprocessor),
            ("model", RandomForestRegressor(n_estimators=250, random_state=42, n_jobs=-1)),
        ]
    )
    weather_model = Pipeline(
        [
            ("preprocess", preprocessor),
            ("model", RandomForestClassifier(n_estimators=250, random_state=42, n_jobs=-1)),
        ]
    )

    traffic_model.fit(X_train, y_t_train)
    traffic_level_model.fit(X_train, y_l_train)
    death_model.fit(X_train, y_d_train)
    weather_model.fit(X_train, y_w_train)

    metrics = {
        "traffic_mae": float(mean_absolute_error(y_t_test, traffic_model.predict(X_test))),
        "traffic_r2": float(r2_score(y_t_test, traffic_model.predict(X_test))),
        "traffic_level_accuracy": float(
            accuracy_score(y_l_test, traffic_level_model.predict(X_test))
        ),
        "death_mae": float(mean_absolute_error(y_d_test, death_model.predict(X_test))),
        "death_r2": float(r2_score(y_d_test, death_model.predict(X_test))),
        "weather_accuracy": float(accuracy_score(y_w_test, weather_model.predict(X_test))),
    }

    return {
        "feature_cols": feature_cols,
        "traffic_model": traffic_model,
        "traffic_level_model": traffic_level_model,
        "death_model": death_model,
        "weather_model": weather_model,
        "metrics": metrics,
    }, model_df


def batch_best_route_predictions(bundle: dict, model_df: pd.DataFrame, area_to_roads: dict) -> pd.DataFrame:
    scenario_cols = [
        "Source_Area",
        "Destination",
        "Hour",
        "is_peak_hour",
        "day_of_week",
        "month",
        "day",
    ]
    scenarios = model_df[scenario_cols].drop_duplicates().copy()

    road_geo = (
        model_df.groupby(["Source_Area", "Road/Intersection Name"])[["Latitude", "Longitude"]]
        .mean()
        .reset_index()
    )
    road_geo_by_area = {
        area: g.drop(columns=["Source_Area"]).reset_index(drop=True)
        for area, g in road_geo.groupby("Source_Area", sort=False)
    }

    candidate_parts = []
    for area, area_scenarios in scenarios.groupby("Source_Area", sort=False):
        roads = road_geo_by_area.get(area)
        if roads is None or roads.empty:
            continue
        area_scenarios = area_scenarios.reset_index(drop=True).copy()
        area_scenarios["__k"] = 1
        roads = roads.copy()
        roads["__k"] = 1
        candidate_parts.append(area_scenarios.merge(roads, on="__k").drop(columns="__k"))

    candidates = pd.concat(candidate_parts, ignore_index=True)
    feature_cols = bundle["feature_cols"]
    candidate_features = candidates[feature_cols]

    candidates["pred_traffic"] = bundle["traffic_model"].predict(candidate_features)
    candidates["pred_death"] = bundle["death_model"].predict(candidate_features)
    candidates["pred_level"] = bundle["traffic_level_model"].predict(candidate_features)
    candidates["pred_weather"] = bundle["weather_model"].predict(candidate_features)

    # Weighted risk score for route ranking.
    traffic_norm = candidates["pred_traffic"] / (candidates["pred_traffic"].max() + 1e-6)
    death_norm = candidates["pred_death"] / (candidates["pred_death"].max() + 1e-6)
    candidates["route_score"] = traffic_norm + death_norm

    idx = candidates.groupby(scenario_cols, sort=False)["route_score"].idxmin()
    best_by_scenario = candidates.loc[idx, scenario_cols + [
        "Road/Intersection Name",
        "pred_traffic",
        "pred_level",
        "pred_death",
        "pred_weather",
    ]].copy()
    best_by_scenario = best_by_scenario.rename(
        columns={
            "Road/Intersection Name": "Best_Route",
            "pred_traffic": "Predicted_Traffic_Volume",
            "pred_level": "Predicted_Traffic_Level",
            "pred_death": "Predicted_Death_Count",
            "pred_weather": "Predicted_Weather_Condition",
        }
    )
    return best_by_scenario


def main():
    df = pd.read_csv(INPUT_DATA_PATH)
    augmented_df = add_destination_and_time_columns(df)
    AUGMENTED_DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    augmented_df.to_csv(AUGMENTED_DATA_PATH, index=False)

    model_bundle, model_df = build_models(augmented_df)
    area_to_roads = (
        augmented_df.groupby("Source_Area")["Road/Intersection Name"]
        .apply(lambda s: sorted(s.unique().tolist()))
        .to_dict()
    )

    scenario_predictions = batch_best_route_predictions(model_bundle, model_df, area_to_roads)
    merge_cols = [
        "Source_Area",
        "Destination",
        "Hour",
        "is_peak_hour",
        "day_of_week",
        "month",
        "day",
    ]
    output_df = model_df.merge(scenario_predictions, on=merge_cols, how="left")
    output_df.to_csv(PREDICTION_OUTPUT_PATH, index=False)

    bundle_to_save = {
        **model_bundle,
        "area_to_roads": area_to_roads,
        "created_from": str(INPUT_DATA_PATH),
        "augmented_data_path": str(AUGMENTED_DATA_PATH),
        "prediction_output_path": str(PREDICTION_OUTPUT_PATH),
    }
    MODEL_BUNDLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle_to_save, MODEL_BUNDLE_PATH)

    print("Augmented dataset saved:", AUGMENTED_DATA_PATH)
    print("Prediction output saved:", PREDICTION_OUTPUT_PATH)
    print("Model bundle saved:", MODEL_BUNDLE_PATH)
    print("Metrics:", bundle_to_save["metrics"])


if __name__ == "__main__":
    main()

