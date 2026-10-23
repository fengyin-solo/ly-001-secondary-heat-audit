#!/usr/bin/env python3
"""Recover secondary-network meters and publish a reconciled heat batch."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "secondary-heat-v1"
RESULT_VERSION = "secondary-heat-result-v1"
INVERSION_METHOD = "lagged-degree-hours-nnls-v1"
ROUND_PLACES = Decimal("0.000001")
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
STATION_KEYS = {
    "schema_version",
    "station_id",
    "batch_id",
    "sample_interval_min",
    "supply_temp_c",
    "outdoor_temp_c",
    "source_heat_kwh",
}
BRANCH_FILE_KEYS = {"schema_version", "branches"}
BRANCH_KEYS = {
    "branch_id",
    "design_flow_m3h",
    "valve_signature_m3h",
    "wave_lag_min",
}
METER_FILE_KEYS = {"schema_version", "meters"}
METER_KEYS = {
    "meter_serial",
    "valve_signature_m3h",
    "wave_lag_min",
    "flow_m3h",
    "return_temp_c",
    "demand_kwh",
}
LEDGER_KEYS = {
    "batch_key",
    "station_id",
    "result_sha256",
    "source_kwh",
    "demand_kwh",
    "loss_kwh",
}
INPUT_NAMES = ("branches.json", "meters.json", "station.json")


class AuditError(Exception):
    """A batch cannot be admitted under the public contract."""


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AuditError(f"JSON 出现重复键 {key}")
        result[key] = value
    return result


def _bad_constant(token: str) -> None:
    raise AuditError(f"JSON 出现非有限数值 {token}")


def _decode_json(raw: bytes, label: str) -> Any:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AuditError(f"{label} 不是 UTF-8") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_unique_pairs,
            parse_constant=_bad_constant,
        )
    except AuditError:
        raise
    except json.JSONDecodeError as exc:
        raise AuditError(f"{label} 不是合法 JSON: {exc.msg}") from exc


def _read_regular_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise AuditError(f"{label} 必须是普通文件")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise AuditError(f"无法读取 {label}: {exc.strerror}") from exc
    value = _decode_json(raw, label)
    if not isinstance(value, dict):
        raise AuditError(f"{label} 顶层必须是对象")
    return value, raw


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AuditError(f"{label} 必须是 JSON 数字")
    converted = float(value)
    if not math.isfinite(converted):
        raise AuditError(f"{label} 必须是有限数字")
    return converted


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AuditError(f"{label} 必须是 JSON 整数")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise AuditError(f"{label} 必须是非空字符串")
    if value != value.strip() or any(character in value for character in "\r\n\t"):
        raise AuditError(f"{label} 不能带首尾或控制空白")
    return value


def _array(value: Any, label: str, size: int | None = None) -> list[Any]:
    if not isinstance(value, list):
        raise AuditError(f"{label} 必须是数组")
    if size is not None and len(value) != size:
        raise AuditError(f"{label} 必须恰好有 {size} 个元素")
    return value


def _number_array(value: Any, label: str, size: int | None) -> list[float]:
    values = _array(value, label, size)
    return [_number(item, f"{label}[{index}]") for index, item in enumerate(values)]


def _exact_keys(payload: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(payload)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        detail = []
        if missing:
            detail.append("缺少 " + ",".join(missing))
        if extra:
            detail.append("多出 " + ",".join(extra))
        raise AuditError(f"{label} 键集合不正确: {'; '.join(detail)}")


def _validate_station(raw: dict[str, Any]) -> dict[str, Any]:
    _exact_keys(raw, STATION_KEYS, "station.json")
    if raw["schema_version"] != SCHEMA_VERSION:
        raise AuditError("station.json 的 schema_version 不受支持")
    station_id = _text(raw["station_id"], "station_id")
    batch_id = _text(raw["batch_id"], "batch_id")
    interval = _integer(raw["sample_interval_min"], "sample_interval_min")
    if interval < 5 or interval > 60:
        raise AuditError("sample_interval_min 必须介于 5 与 60")
    supply = _number_array(raw["supply_temp_c"], "supply_temp_c", None)
    outdoor = _number_array(raw["outdoor_temp_c"], "outdoor_temp_c", None)
    source = _number_array(raw["source_heat_kwh"], "source_heat_kwh", None)
    samples = len(supply)
    if samples < 12 or samples > 48:
        raise AuditError("采样段数必须介于 12 与 48")
    if len(outdoor) != samples or len(source) != samples:
        raise AuditError("站级历史数组长度必须一致")
    return {
        "station_id": station_id,
        "batch_id": batch_id,
        "sample_interval_min": interval,
        "supply_temp_c": supply,
        "outdoor_temp_c": outdoor,
        "source_heat_kwh": source,
    }


def _validate_branches(raw: dict[str, Any], samples: int) -> list[dict[str, Any]]:
    _exact_keys(raw, BRANCH_FILE_KEYS, "branches.json")
    if raw["schema_version"] != SCHEMA_VERSION:
        raise AuditError("branches.json 的 schema_version 不受支持")
    records = _array(raw["branches"], "branches")
    if len(records) < 2 or len(records) > 18:
        raise AuditError("支路数必须介于 2 与 18")
    branches: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(records):
        if not isinstance(item, dict):
            raise AuditError(f"branches[{index}] 必须是对象")
        _exact_keys(item, BRANCH_KEYS, f"branches[{index}]")
        branch_id = _text(item["branch_id"], f"branches[{index}].branch_id")
        if branch_id in seen:
            raise AuditError(f"branch_id 重复: {branch_id}")
        seen.add(branch_id)
        design_flow = _number(item["design_flow_m3h"], f"branches[{index}].design_flow_m3h")
        if design_flow <= 0:
            raise AuditError("design_flow_m3h 必须大于零")
        signature = _number_array(
            item["valve_signature_m3h"],
            f"branches[{index}].valve_signature_m3h",
            4,
        )
        lag = _number(item["wave_lag_min"], f"branches[{index}].wave_lag_min")
        if lag < 0:
            raise AuditError("wave_lag_min 不得小于零")
        branches.append(
            {
                "branch_id": branch_id,
                "design_flow_m3h": design_flow,
                "valve_signature_m3h": signature,
                "wave_lag_min": lag,
            }
        )
    if len(branches) > samples:
        raise AuditError("采样段数不足以闭合热损反演")
    return sorted(branches, key=lambda item: item["branch_id"])


def _validate_meters(
    raw: dict[str, Any],
    samples: int,
    expected_count: int,
) -> list[dict[str, Any]]:
    _exact_keys(raw, METER_FILE_KEYS, "meters.json")
    if raw["schema_version"] != SCHEMA_VERSION:
        raise AuditError("meters.json 的 schema_version 不受支持")
    records = _array(raw["meters"], "meters")
    if len(records) != expected_count:
        raise AuditError("表计记录数必须与支路数相同")
    meters: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(records):
        if not isinstance(item, dict):
            raise AuditError(f"meters[{index}] 必须是对象")
        _exact_keys(item, METER_KEYS, f"meters[{index}]")
        serial = _text(item["meter_serial"], f"meters[{index}].meter_serial")
        if serial in seen:
            raise AuditError(f"meter_serial 重复: {serial}")
        seen.add(serial)
        signature = _number_array(
            item["valve_signature_m3h"],
            f"meters[{index}].valve_signature_m3h",
            4,
        )
        lag = _number(item["wave_lag_min"], f"meters[{index}].wave_lag_min")
        if lag < 0:
            raise AuditError("表计 wave_lag_min 不得小于零")
        flow = _number_array(item["flow_m3h"], f"meters[{index}].flow_m3h", samples)
        returning = _number_array(
            item["return_temp_c"],
            f"meters[{index}].return_temp_c",
            samples,
        )
        demand = _number_array(
            item["demand_kwh"],
            f"meters[{index}].demand_kwh",
            samples,
        )
        if any(value <= 0 for value in flow):
            raise AuditError("flow_m3h 元素必须大于零")
        if any(value < 0 for value in demand):
            raise AuditError("demand_kwh 元素不得小于零")
        meters.append(
            {
                "meter_serial": serial,
                "valve_signature_m3h": signature,
                "wave_lag_min": lag,
                "flow_m3h": flow,
                "return_temp_c": returning,
                "demand_kwh": demand,
            }
        )
    return sorted(meters, key=lambda item: item["meter_serial"])


def _load_inputs(input_dir: Path) -> tuple[dict[str, Any], list[dict], list[dict], dict[str, bytes]]:
    if input_dir.is_symlink() or not input_dir.is_dir():
        raise AuditError("输入目录必须是真实目录")
    station_raw, station_bytes = _read_regular_json(input_dir / "station.json", "station.json")
    branches_raw, branch_bytes = _read_regular_json(input_dir / "branches.json", "branches.json")
    meters_raw, meter_bytes = _read_regular_json(input_dir / "meters.json", "meters.json")
    station = _validate_station(station_raw)
    branches = _validate_branches(branches_raw, len(station["source_heat_kwh"]))
    meters = _validate_meters(
        meters_raw,
        len(station["source_heat_kwh"]),
        len(branches),
    )
    raw_files = {
        "branches.json": branch_bytes,
        "meters.json": meter_bytes,
        "station.json": station_bytes,
    }
    return station, branches, meters, raw_files


def _median(values: Iterable[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        raise AuditError("历史流量为空")
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _pair_metrics(branch: dict[str, Any], meter: dict[str, Any]) -> tuple[str, float, float, float, float]:
    branch_signature = branch["valve_signature_m3h"]
    meter_signature = meter["valve_signature_m3h"]
    same = math.sqrt(
        math.fsum(
            (branch_value - meter_value) ** 2
            for branch_value, meter_value in zip(branch_signature, meter_signature)
        )
        / 4.0
    )
    inverted = math.sqrt(
        math.fsum(
            (branch_value + meter_value) ** 2
            for branch_value, meter_value in zip(branch_signature, meter_signature)
        )
        / 4.0
    )
    if inverted + 1e-12 < same:
        orientation = "inverted"
        valve_error = inverted
    else:
        orientation = "same"
        valve_error = same
    lag_error = abs(meter["wave_lag_min"] - branch["wave_lag_min"])
    flow_median = _median(meter["flow_m3h"])
    flow_error = abs(math.log(flow_median / branch["design_flow_m3h"]))
    cost = (
        4.0 * valve_error / 0.60
        + lag_error / 2.0
        + 2.0 * flow_error / 0.15
    )
    return orientation, valve_error, lag_error, flow_error, cost


def _hungarian(cost: list[list[float]]) -> tuple[list[int], float]:
    size = len(cost)
    if size == 0 or any(len(row) != size for row in cost):
        raise AuditError("配对成本矩阵不是方阵")
    u = [0.0] * (size + 1)
    v = [0.0] * (size + 1)
    assignment = [0] * (size + 1)
    way = [0] * (size + 1)
    for row in range(1, size + 1):
        assignment[0] = row
        column0 = 0
        minimum = [math.inf] * (size + 1)
        used = [False] * (size + 1)
        while True:
            used[column0] = True
            row0 = assignment[column0]
            delta = math.inf
            column1 = 0
            for column in range(1, size + 1):
                if used[column]:
                    continue
                current = cost[row0 - 1][column - 1] - u[row0] - v[column]
                if current < minimum[column]:
                    minimum[column] = current
                    way[column] = column0
                if minimum[column] < delta:
                    delta = minimum[column]
                    column1 = column
            if not math.isfinite(delta):
                raise AuditError("配对成本矩阵没有完整匹配")
            for column in range(size + 1):
                if used[column]:
                    u[assignment[column]] += delta
                    v[column] -= delta
                else:
                    minimum[column] -= delta
            column0 = column1
            if assignment[column0] == 0:
                break
        while True:
            column1 = way[column0]
            assignment[column0] = assignment[column1]
            column0 = column1
            if column0 == 0:
                break
    result = [-1] * size
    for column in range(1, size + 1):
        result[assignment[column] - 1] = column - 1
    total = math.fsum(cost[row][column] for row, column in enumerate(result))
    return result, total


def _assign_meters(
    branches: list[dict[str, Any]],
    meters: list[dict[str, Any]],
) -> tuple[list[int], list[tuple[str, float, float, float, float]], float, float]:
    size = len(branches)
    metrics: dict[tuple[int, int], tuple[str, float, float, float, float]] = {}
    cost = [[0.0] * size for _ in range(size)]
    for branch_index, branch in enumerate(branches):
        for meter_index, meter in enumerate(meters):
            pair = _pair_metrics(branch, meter)
            metrics[(branch_index, meter_index)] = pair
            cost[branch_index][meter_index] = pair[4]
    selected, best = _hungarian(cost)
    alternatives: list[float] = []
    forbidden = 1.0e12
    for row, column in enumerate(selected):
        trial = [line[:] for line in cost]
        trial[row][column] = forbidden
        _trial_assignment, trial_cost = _hungarian(trial)
        alternatives.append(trial_cost)
    second_best = min(alternatives)
    margin = second_best - best
    if not math.isfinite(margin) or margin <= 1e-9:
        raise AuditError("表计到支路的配对不唯一")
    ordered_metrics = [metrics[(row, column)] for row, column in enumerate(selected)]
    return selected, ordered_metrics, best, margin


def _solve_spd(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    size = len(rhs)
    work = [matrix[index][:] + [rhs[index]] for index in range(size)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(work[row][column]))
        if abs(work[pivot][column]) < 1e-14:
            raise AuditError("非负最小二乘的设计矩阵秩不足")
        if pivot != column:
            work[column], work[pivot] = work[pivot], work[column]
        pivot_value = work[column][column]
        for entry in range(column, size + 1):
            work[column][entry] /= pivot_value
        for row in range(size):
            if row == column:
                continue
            factor = work[row][column]
            if factor == 0.0:
                continue
            for entry in range(column, size + 1):
                work[row][entry] -= factor * work[column][entry]
    return [work[index][size] for index in range(size)]


def _nnls(design: list[list[float]], target: list[float]) -> list[float]:
    rows = len(design)
    columns = len(design[0])
    values = [0.0] * columns
    passive = [False] * columns
    tolerance = 1e-11
    for _outer in range(columns * 6 + 20):
        residual = [
            target[row] - math.fsum(design[row][column] * values[column] for column in range(columns))
            for row in range(rows)
        ]
        gradient = [
            math.fsum(design[row][column] * residual[row] for row in range(rows))
            for column in range(columns)
        ]
        candidates = [index for index in range(columns) if not passive[index]]
        if not candidates or max(gradient[index] for index in candidates) <= tolerance:
            return values
        entering = max(candidates, key=lambda index: (gradient[index], -index))
        passive[entering] = True
        for _inner in range(columns * 6 + 20):
            active = [index for index, enabled in enumerate(passive) if enabled]
            gram = [
                [
                    math.fsum(design[row][left] * design[row][right] for row in range(rows))
                    for right in active
                ]
                for left in active
            ]
            rhs = [
                math.fsum(design[row][left] * target[row] for row in range(rows))
                for left in active
            ]
            solved_active = _solve_spd(gram, rhs)
            trial = [0.0] * columns
            for index, value in zip(active, solved_active):
                trial[index] = value
            if all(trial[index] > tolerance for index in active):
                values = trial
                break
            ratios = [
                values[index] / (values[index] - trial[index])
                for index in active
                if trial[index] <= tolerance and values[index] - trial[index] > tolerance
            ]
            alpha = min(ratios) if ratios else 0.0
            values = [
                values[index] + alpha * (trial[index] - values[index])
                for index in range(columns)
            ]
            for index in active:
                if values[index] <= tolerance:
                    values[index] = 0.0
                    passive[index] = False
        else:
            raise AuditError("非负最小二乘未收敛")
    raise AuditError("非负最小二乘未收敛")


def _round_number(value: float) -> float:
    if not math.isfinite(value):
        raise AuditError("结果出现非有限数值")
    if abs(value) < 0.0000005:
        return 0.0
    rounded = Decimal(str(value)).quantize(ROUND_PLACES, rounding=ROUND_HALF_UP)
    if rounded == 0:
        return 0.0
    return float(rounded)


def _build_result(
    station: dict[str, Any],
    branches: list[dict[str, Any]],
    meters: list[dict[str, Any]],
    raw_files: dict[str, bytes],
) -> dict[str, Any]:
    selected, pair_metrics, objective, margin = _assign_meters(branches, meters)
    interval_hours = station["sample_interval_min"] / 60.0
    samples = len(station["source_heat_kwh"])

    design: list[list[float]] = []
    target: list[float] = []
    degree_by_branch: list[list[float]] = []
    for branch_index in range(len(branches)):
        meter = meters[selected[branch_index]]
        return_values = meter["return_temp_c"]
        branch_degrees = []
        for sample in range(samples):
            previous = return_values[max(0, sample - 1)]
            lag_temp = 0.55 * return_values[sample] + 0.45 * previous
            degree_hours = max(0.5, lag_temp - station["outdoor_temp_c"][sample]) * interval_hours
            branch_degrees.append(degree_hours)
        degree_by_branch.append(branch_degrees)

    for sample in range(samples):
        design.append(
            [degree_by_branch[branch][sample] / 1000.0 for branch in range(len(branches))]
        )
        demand = math.fsum(meters[selected[branch]][ "demand_kwh"][sample] for branch in range(len(branches)))
        target.append(station["source_heat_kwh"][sample] - demand)

    ua_values = _nnls(design, target)
    assignments = []
    branch_balance = []
    for branch_index, branch in enumerate(branches):
        meter = meters[selected[branch_index]]
        orientation, valve_error, lag_error, flow_error, pair_cost = pair_metrics[branch_index]
        assignments.append(
            {
                "branch_id": branch["branch_id"],
                "meter_serial": meter["meter_serial"],
                "orientation": orientation,
                "valve_rmse": _round_number(valve_error),
                "lag_error_min": _round_number(lag_error),
                "flow_log_error": _round_number(flow_error),
                "assignment_cost": _round_number(pair_cost),
            }
        )
        demand_total = math.fsum(meter["demand_kwh"])
        loss = ua_values[branch_index] * math.fsum(degree_by_branch[branch_index]) / 1000.0
        branch_balance.append(
            {
                "branch_id": branch["branch_id"],
                "meter_serial": meter["meter_serial"],
                "demand_kwh": _round_number(demand_total),
                "loss_kwh": _round_number(loss),
                "supply_kwh": _round_number(demand_total + loss),
                "flow_m3": _round_number(
                    math.fsum(meter["flow_m3h"]) * station["sample_interval_min"] / 60.0
                ),
                "ua_w_per_k": _round_number(ua_values[branch_index]),
            }
        )

    digest = hashlib.sha256()
    for name in INPUT_NAMES:
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(raw_files[name])
        digest.update(b"\0")

    source_total = math.fsum(station["source_heat_kwh"])
    demand_total = math.fsum(item["demand_kwh"] for item in branch_balance)
    loss_total = math.fsum(item["loss_kwh"] for item in branch_balance)
    degree_total = math.fsum(math.fsum(series) for series in degree_by_branch)
    weighted_ua = math.fsum(
        ua_values[index] * math.fsum(degree_by_branch[index])
        for index in range(len(branches))
    )
    aggregate_ua = weighted_ua / degree_total if degree_total else 0.0

    return {
        "contract_version": RESULT_VERSION,
        "batch_key": digest.hexdigest(),
        "station_id": station["station_id"],
        "assignment_summary": {
            "objective": _round_number(objective),
            "second_best_margin": _round_number(margin),
        },
        "assignments": assignments,
        "branch_balance": branch_balance,
        "system_balance": {
            "source_kwh": _round_number(source_total),
            "demand_kwh": _round_number(demand_total),
            "loss_kwh": _round_number(loss_total),
            "balance_error_kwh": _round_number(source_total - demand_total - loss_total),
        },
        "loss_inversion": {
            "samples": samples,
            "degree_hours_total": _round_number(degree_total),
            "aggregate_ua_w_per_k": _round_number(aggregate_ua),
            "method": INVERSION_METHOD,
        },
    }


def _compact_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _validate_ledger_line(line: bytes, number: int) -> dict[str, Any]:
    if not line.endswith(b"\n"):
        raise AuditError(f"台账第 {number} 行缺少换行")
    content = line[:-1]
    if not content.startswith(b"{") or not content.endswith(b"}"):
        raise AuditError(f"台账第 {number} 行不是紧凑 JSON 对象")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AuditError(f"台账第 {number} 行不是 UTF-8") from exc
    payload = _decode_json(content, f"台账第 {number} 行")
    if not isinstance(payload, dict):
        raise AuditError(f"台账第 {number} 行必须是对象")
    _exact_keys(payload, LEDGER_KEYS, f"台账第 {number} 行")
    for key in ("batch_key", "result_sha256"):
        value = payload[key]
        if not isinstance(value, str) or HEX_64.fullmatch(value) is None:
            raise AuditError(f"台账第 {number} 行的 {key} 必须是 64 位小写十六进制")
    _text(payload["station_id"], f"台账第 {number} 行 station_id")
    for key in ("source_kwh", "demand_kwh", "loss_kwh"):
        _number(payload[key], f"台账第 {number} 行 {key}")
    expected = _compact_bytes(payload)
    if line != expected:
        raise AuditError(f"台账第 {number} 行不是规范紧凑 JSON")
    return payload


def _read_ledger(path: Path) -> tuple[list[dict[str, Any]], bytes]:
    if path.exists() and path.is_dir():
        raise AuditError("台账目标不能是目录")
    try:
        raw = path.read_bytes() if path.exists() else b""
    except OSError as exc:
        raise AuditError(f"无法读取旧台账: {exc.strerror}") from exc
    if raw and not raw.endswith(b"\n"):
        raise AuditError("旧台账最后一行缺少换行")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for number, line in enumerate(raw.splitlines(keepends=True), start=1):
        record = _validate_ledger_line(line, number)
        if record["batch_key"] in seen:
            raise AuditError("旧台账含重复批次键")
        seen.add(record["batch_key"])
        records.append(record)
    return records, raw


@contextmanager
def _directory_lock(parent: Path) -> Iterable[None]:
    if not parent.is_dir():
        raise AuditError("台账父目录必须预先存在")
    descriptor = os.open(parent, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _write_temp(path: Path, content: bytes, prefix: str) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=prefix,
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        return temporary
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _restore_bytes(path: Path, old_bytes: bytes | None) -> None:
    if old_bytes is None:
        path.unlink(missing_ok=True)
        return
    temporary = _write_temp(path, old_bytes, f".{path.name}.restore.")
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _publish(
    result_path: Path,
    ledger_path: Path,
    result_bytes: bytes,
    record: dict[str, Any],
) -> None:
    if not result_path.parent.is_dir():
        raise AuditError("结果父目录必须预先存在")
    with _directory_lock(ledger_path.parent):
        records, current_ledger = _read_ledger(ledger_path)
        same = [item for item in records if item["batch_key"] == record["batch_key"]]
        result_digest = hashlib.sha256(result_bytes).hexdigest()
        if same:
            if same[0]["result_sha256"] != result_digest:
                raise AuditError("同一批次键对应了不同结果摘要")
            next_ledger = current_ledger
        else:
            line = _compact_bytes(record)
            next_ledger = current_ledger + line

        old_result = result_path.read_bytes() if result_path.exists() else None
        result_temp: Path | None = None
        ledger_temp: Path | None = None
        result_installed = False
        try:
            result_temp = _write_temp(result_path, result_bytes, f".{result_path.name}.")
            ledger_temp = _write_temp(ledger_path, next_ledger, f".{ledger_path.name}.")
            os.replace(result_temp, result_path)
            result_temp = None
            result_installed = True
            os.replace(ledger_temp, ledger_path)
            ledger_temp = None
        except Exception as exc:
            if result_installed:
                _restore_bytes(result_path, old_result)
            if isinstance(exc, AuditError):
                raise
            raise AuditError(f"结果或台账发布失败: {exc.__class__.__name__}") from exc
        finally:
            if result_temp is not None:
                result_temp.unlink(missing_ok=True)
            if ledger_temp is not None:
                ledger_temp.unlink(missing_ok=True)


def run(input_dir: Path, result_path: Path, ledger_path: Path) -> None:
    station, branches, meters, raw_files = _load_inputs(input_dir)
    result = _build_result(station, branches, meters, raw_files)
    result_bytes = _compact_bytes(result)
    record = {
        "batch_key": result["batch_key"],
        "station_id": result["station_id"],
        "result_sha256": hashlib.sha256(result_bytes).hexdigest(),
        "source_kwh": result["system_balance"]["source_kwh"],
        "demand_kwh": result["system_balance"]["demand_kwh"],
        "loss_kwh": result["system_balance"]["loss_kwh"],
    }
    _publish(result_path, ledger_path, result_bytes, record)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--ledger", required=True)
    args = parser.parse_args()
    try:
        if not args.input or not args.result or not args.ledger:
            raise AuditError("输入、结果和台账路径都不能为空")
        run(Path(args.input), Path(args.result), Path(args.ledger))
    except (AuditError, OSError, ValueError) as exc:
        reason = str(exc).replace("\r", " ").replace("\n", " ").strip() or "处理失败"
        sys.stderr.write(f"处理未完成: {reason}\n")
        return 2
    except Exception:
        sys.stderr.write("处理未完成: 程序未能完成本批处理\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
