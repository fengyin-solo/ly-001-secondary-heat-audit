#!/usr/bin/env python3
"""Build the deterministic secondary-network playback used by the task."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


SCHEMA = "secondary-heat-v1"
SEED_LABEL = "20261022"

BRANCH_IDS = [
    "BH-A17",
    "BH-C04",
    "BH-E11",
    "BH-H06",
    "BH-J03",
    "BH-K19",
    "BH-M08",
    "BH-P15",
    "BH-R02",
    "BH-S21",
    "BH-T07",
    "BH-V12",
    "BH-W05",
    "BH-X18",
    "BH-Y09",
    "BH-Z14",
    "BH-AA20",
    "BH-AB16",
]

METER_SERIALS = [
    "HXM-6Q2A",
    "HXM-9R4C",
    "HXM-1N7P",
    "HXM-3T8D",
    "HXM-5V2K",
    "HXM-8B1S",
    "HXM-4L6W",
    "HXM-2J9F",
    "HXM-7C3M",
    "HXM-0P5R",
    "HXM-6H8T",
    "HXM-9D1V",
    "HXM-4A7N",
    "HXM-2Z6Q",
    "HXM-8K3B",
    "HXM-5S9L",
    "HXM-1G4W",
    "HXM-7F2X",
]

VALVE_SIGNATURES = [
    [1.42, -0.38, 0.91, -1.07],
    [0.73, 1.16, -0.54, -0.89],
    [-1.21, 0.47, 1.08, 0.62],
    [1.05, 0.88, -1.31, 0.34],
    [-0.66, -1.14, 0.39, 1.27],
    [0.92, -0.81, 1.34, -0.45],
    [-1.07, 0.55, -0.72, 1.19],
    [1.18, 0.41, 0.67, -1.22],
    [0.51, -1.09, 1.23, 0.76],
    [-0.84, 1.29, 0.44, -0.58],
    [1.31, -0.52, -0.96, 0.48],
    [-0.49, 0.97, -1.18, -0.73],
    [0.64, 1.11, 0.82, -1.28],
    [-1.16, -0.43, 1.37, 0.91],
    [0.87, -1.24, -0.46, 1.06],
    [-0.71, 1.04, 0.59, 1.32],
    [1.09, 0.62, -1.13, -0.37],
    [-1.33, -0.57, 0.74, 1.15],
]

DESIGN_FLOWS = [
    31.8,
    18.7,
    42.6,
    25.4,
    14.9,
    37.2,
    21.3,
    48.1,
    28.6,
    16.5,
    34.4,
    23.1,
    19.8,
    45.3,
    27.2,
    39.6,
    12.8,
    33.7,
]

LAGS = [
    14.2,
    26.8,
    9.6,
    31.4,
    18.3,
    7.8,
    22.1,
    12.7,
    35.2,
    20.6,
    10.9,
    28.5,
    16.4,
    24.7,
    33.1,
    8.9,
    29.8,
    19.2,
]

RETURN_BASE = [
    42.8,
    46.4,
    39.7,
    48.2,
    44.1,
    37.9,
    50.3,
    41.6,
    52.1,
    43.5,
    45.2,
    47.6,
    40.4,
    49.1,
    38.8,
    46.9,
    51.3,
    42.0,
]

TRUE_UA = [
    920.0,
    1460.0,
    1110.0,
    1780.0,
    830.0,
    1290.0,
    1580.0,
    1040.0,
    1960.0,
    760.0,
    1370.0,
    1690.0,
    990.0,
    1840.0,
    870.0,
    1520.0,
    1190.0,
    1730.0,
]

HIDDEN_SIGNS = [
    -1,
    1,
    1,
    -1,
    1,
    -1,
    -1,
    1,
    1,
    -1,
    1,
    -1,
    1,
    -1,
    -1,
    1,
    -1,
    1,
]

METER_ORDER = [7, 2, 15, 0, 11, 4, 17, 8, 1, 13, 5, 16, 3, 10, 6, 14, 9, 12]


def _noise(series: int, sample: int, scale: float = 1.0) -> float:
    return scale * (
        0.48 * math.sin(0.71 * sample + 0.37 * series)
        + 0.31 * math.sin(1.93 * sample + 0.83 * series)
        + 0.21 * math.cos(0.29 * sample - 0.51 * series)
    )


def _round(value: float, places: int = 6) -> float:
    return float(f"{value:.{places}f}")


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def build_payload() -> tuple[dict, dict, dict]:
    sample_interval = 20
    samples = 32
    station_id = "CBN-SEC-02"
    station = {
        "schema_version": SCHEMA,
        "station_id": station_id,
        "batch_id": "NIGHT-20261021-B",
        "sample_interval_min": sample_interval,
        "supply_temp_c": [
            _round(76.5 + 1.8 * math.sin(0.21 * k) + 0.4 * math.cos(0.73 * k), 3)
            for k in range(samples)
        ],
        "outdoor_temp_c": [
            _round(-5.8 + 2.1 * math.sin(0.17 * k + 0.6) + 0.35 * math.cos(0.51 * k), 3)
            for k in range(samples)
        ],
        "source_heat_kwh": [],
    }

    branches = {
        "schema_version": SCHEMA,
        "branches": [
            {
                "branch_id": branch_id,
                "design_flow_m3h": DESIGN_FLOWS[index],
                "valve_signature_m3h": VALVE_SIGNATURES[index],
                "wave_lag_min": LAGS[index],
            }
            for index, branch_id in enumerate(BRANCH_IDS)
        ],
    }

    branch_samples: list[dict] = []
    meter_records: list[dict] = []
    for branch_index, branch_id in enumerate(BRANCH_IDS):
        flow_values: list[float] = []
        return_values: list[float] = []
        demand_values: list[float] = []
        for k in range(samples):
            flow = DESIGN_FLOWS[branch_index] * (
                1.0
                + 0.055 * math.sin(0.43 * k + 0.21 * branch_index)
                + 0.028 * math.cos(0.97 * k + 0.13 * branch_index)
            )
            returning = (
                RETURN_BASE[branch_index]
                + (2.4 + 0.13 * (branch_index % 6))
                * math.sin(
                    (0.09 + 0.083 * branch_index) * k
                    + 0.61 * branch_index
                )
                + (0.85 + 0.06 * (branch_index % 4))
                * math.cos(
                    (0.37 + 0.041 * branch_index) * k
                    + 1.17 * branch_index
                )
                + _noise(branch_index, k, 0.10)
            )
            delta_t = max(18.0, station["supply_temp_c"][k] - returning - 3.5)
            demand = flow / 3600.0 * 1000.0 * 4.186 * delta_t * sample_interval / 60.0
            demand *= 1.0 + _noise(branch_index + 30, k, 0.006)
            flow_values.append(_round(flow))
            return_values.append(_round(returning))
            demand_values.append(_round(max(0.0, demand)))
        branch_samples.append(
            {
                "flow": flow_values,
                "return": return_values,
                "demand": demand_values,
            }
        )

        meter_values = [
            HIDDEN_SIGNS[branch_index] * value
            + 0.014 * math.sin(0.9 * branch_index + position)
            + 0.006 * math.cos(0.4 * branch_index - position)
            for position, value in enumerate(VALVE_SIGNATURES[branch_index])
        ]
        meter_records.append(
            {
                "branch_index": branch_index,
                "payload": {
                    "meter_serial": METER_SERIALS[branch_index],
                    "valve_signature_m3h": [_round(value) for value in meter_values],
                    "wave_lag_min": _round(LAGS[branch_index] + _noise(branch_index, 19, 0.08)),
                    "flow_m3h": flow_values,
                    "return_temp_c": return_values,
                    "demand_kwh": demand_values,
                },
            }
        )

    meters = {
        "schema_version": SCHEMA,
        "meters": [meter_records[index]["payload"] for index in METER_ORDER],
    }

    source_values: list[float] = []
    for k in range(samples):
        loss = 0.0
        demand_total = 0.0
        for branch_index in range(len(BRANCH_IDS)):
            previous = branch_samples[branch_index]["return"][max(0, k - 1)]
            current = branch_samples[branch_index]["return"][k]
            lag_temp = 0.55 * current + 0.45 * previous
            degree_h = max(0.5, lag_temp - station["outdoor_temp_c"][k]) * sample_interval / 60.0
            loss += TRUE_UA[branch_index] * degree_h / 1000.0
            demand_total += branch_samples[branch_index]["demand"][k]
        source_values.append(_round(demand_total + loss + _noise(91, k, 0.03), 4))
    station["source_heat_kwh"] = source_values
    return station, branches, meters


def write_payload(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    station, branches, meters = build_payload()
    for name, payload in (
        ("station.json", station),
        ("branches.json", branches),
        ("meters.json", meters),
    ):
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        (out_dir / name).write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    write_payload(args.out)


if __name__ == "__main__":
    main()
