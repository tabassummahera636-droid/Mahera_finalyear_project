from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUT_DATA_PATH = PROJECT_ROOT / "data" / "bangalore_traffic_with_coordinates_FINAL.csv"
OUTPUT_DATA_PATH = PROJECT_ROOT / "data" / "cleaned_traffic.csv"


def preprocess(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    num_cols = df.select_dtypes(include=["int64", "float64"]).columns
    for col in num_cols:
        df[col] = df[col].fillna(df[col].median())

    cat_cols = df.select_dtypes(include=["object"]).columns
    for col in cat_cols:
        mode_val = df[col].mode(dropna=True)
        if not mode_val.empty:
            df[col] = df[col].fillna(mode_val.iloc[0])

    df = df.drop_duplicates()
    return df


def main() -> None:
    if not INPUT_DATA_PATH.exists():
        raise FileNotFoundError(f"Input dataset not found: {INPUT_DATA_PATH}")

    df = pd.read_csv(INPUT_DATA_PATH)
    cleaned = preprocess(df)

    OUTPUT_DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    cleaned.to_csv(OUTPUT_DATA_PATH, index=False)
    print(f"Saved cleaned data: {OUTPUT_DATA_PATH}")


if __name__ == "__main__":
    main()
