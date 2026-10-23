#!/usr/bin/env python3
"""Existing secondary-network batch audit used by the station scheduler."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
from pathlib import Path


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def batch_key(input_dir: Path) -> str:
    digest = hashlib.sha256()
    for name in ("branches.json", "meters.json", "station.json"):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update((input_dir / name).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def simple_assign(branches: list[dict], meters: list[dict]) -> list[tuple[dict, dict]]:
    ordered_branches = sorted(branches, key=lambda item: item["branch_id"])
    ordered_meters = sorted(meters, key=lambda item: item["meter_serial"])
    return list(zip(ordered_branches, ordered_meters))


def direct_loss(station: dict, meters: list[dict]) -> float:
    source = math.fsum(station["source_heat_kwh"])
    demand = math.fsum(
        value for meter in meters for value in meter["demand_kwh"]
    )
    return max(0.0, source - demand)


def build_result(
    station: dict,
    branches: list[dict],
    meters: list[dict],
    key: str,
) -> dict:
    pairs = simple_assign(branches, meters)
    total_loss = direct_loss(station, meters)
    branch_share = total_loss / max(1, len(pairs))
    assignments = []
    branch_balance = []
    for branch, meter in pairs:
        flow_median = median(meter["flow_m3h"])
        assignments.append(
            {
                "branch_id": branch["branch_id"],
                "meter_serial": meter["meter_serial"],
                "orientation": "same",
                "valve_rmse": 0.0,
                "lag_error_min": abs(meter["wave_lag_min"] - branch["wave_lag_min"]),
                "flow_error_m3h": abs(flow_median - branch["design_flow_m3h"]),
            }
        )
        branch_balance.append(
            {
                "branch_id": branch["branch_id"],
                "meter_serial": meter["meter_serial"],
                "demand_kwh": math.fsum(meter["demand_kwh"]),
                "loss_kwh": branch_share,
                "supply_kwh": math.fsum(meter["demand_kwh"]) + branch_share,
                "flow_m3": math.fsum(meter["flow_m3h"])
                * station["sample_interval_min"]
                / 60.0,
                "ua_w_per_k": 0.0,
            }
        )
    demand_total = math.fsum(item["demand_kwh"] for item in branch_balance)
    return {
        "contract_version": "secondary-heat-result-v1",
        "batch_key": key,
        "station_id": station["station_id"],
        "assignments": assignments,
        "branch_balance": branch_balance,
        "system_balance": {
            "source_kwh": math.fsum(station["source_heat_kwh"]),
            "demand_kwh": demand_total,
            "loss_kwh": total_loss,
            "balance_error_kwh": 0.0,
        },
        "loss_inversion": {
            "samples": len(station["source_heat_kwh"]),
            "degree_hours_total": 0.0,
            "aggregate_ua_w_per_k": 0.0,
            "method": "direct-total-loss",
        },
    }


def publish(result_path: Path, ledger_path: Path, result: dict) -> None:
    result_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n"
    descriptor, temporary = tempfile.mkstemp(dir=result_path.parent, prefix=".heat-")
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(temporary, result_path)

    record = {
        "batch_key": result["batch_key"],
        "station_id": result["station_id"],
        "result_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
        "source_kwh": result["system_balance"]["source_kwh"],
        "demand_kwh": result["system_balance"]["demand_kwh"],
        "loss_kwh": result["system_balance"]["loss_kwh"],
    }
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(line)


def run(args: argparse.Namespace) -> None:
    input_dir = Path(args.input)
    station = load_json(input_dir / "station.json")
    branches = load_json(input_dir / "branches.json")["branches"]
    meters = load_json(input_dir / "meters.json")["meters"]
    key = batch_key(input_dir)
    result = build_result(station, branches, meters, key)
    publish(Path(args.result), Path(args.ledger), result)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--ledger", required=True)
    args = parser.parse_args()
    try:
        run(args)
    except Exception as exc:
        reason = str(exc).replace("\n", " ").strip() or "处理失败"
        print(f"处理未完成: {reason}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
