from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = PROJECT_ROOT / "data" / "bangalore_traffic_with_coordinates_FINAL.csv"
OUTPUT_DIR = PROJECT_ROOT / "data" / "eda_outputs"


def main() -> None:
    if not DATA_PATH.exists():
        raise FileNotFoundError(f"Dataset not found: {DATA_PATH}")

    df = pd.read_csv(DATA_PATH)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    plt.figure(figsize=(8, 5))
    sns.histplot(df["Traffic_Volume"], kde=True)
    plt.title("Traffic Volume Distribution")
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / "traffic_volume_distribution.png", dpi=150)
    plt.close()

    plt.figure(figsize=(8, 5))
    sns.scatterplot(data=df, x="Average Speed", y="Traffic_Volume", alpha=0.6)
    plt.title("Average Speed vs Traffic Volume")
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / "speed_vs_volume.png", dpi=150)
    plt.close()

    numeric_df = df.select_dtypes(include=["int64", "float64"])
    plt.figure(figsize=(10, 7))
    sns.heatmap(numeric_df.corr(numeric_only=True), cmap="coolwarm")
    plt.title("Numeric Correlation Heatmap")
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / "correlation_heatmap.png", dpi=150)
    plt.close()

    print(f"EDA charts saved to: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
