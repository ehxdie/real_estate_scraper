from __future__ import annotations
import os
import json
import pandas as pd
from loguru import logger


# Column order for clean output
COLUMNS = [
    "title",
    "price",
    "location",
    "address",
    "bedrooms",
    "bathrooms",
    "square_feet",
    "url",
    "description",
    "posted_date",
]


def _to_df(data: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(data)
    # Re-order columns; add any missing ones as NaN
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = None
    # Keep defined columns first, then any extra columns the parser added
    extra = [c for c in df.columns if c not in COLUMNS]
    return df[COLUMNS + extra]


def export_csv(data: list[dict], path: str = "data/listings.csv") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df = _to_df(data)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    logger.info(f"Exported {len(df)} rows → {path}")


def export_json(data: list[dict], path: str = "data/listings.json") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df = _to_df(data)
    df.to_json(path, orient="records", indent=2, force_ascii=False)
    logger.info(f"Exported {len(df)} records → {path}")


def print_summary(data: list[dict]) -> None:
    """Print a quick human-readable summary to stdout."""
    if not data:
        print("No listings collected.")
        return

    df = _to_df(data)
    print(f"\n{'='*60}")
    print(f"  Total listings : {len(df)}")
    print(f"  Price range    : ${df['price'].min():,.0f} – ${df['price'].max():,.0f}")
    print(f"  Avg price      : ${df['price'].mean():,.0f}")

    loc_counts = df["location"].value_counts()
    if not loc_counts.empty:
        print(f"  Top location   : {loc_counts.index[0]} ({loc_counts.iloc[0]}x)")

    print(f"{'='*60}\n")