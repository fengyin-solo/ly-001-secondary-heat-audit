"""Behavioral acceptance tests for the secondary-network heat audit."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


APP = os.environ.get("TASK_APP", "/app/secondary_heat_audit.py")
TASK_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("TASK_DATA", "/data"))
if not (DATA_DIR / "station.json").is_file():
    DATA_DIR = TASK_ROOT / "environment" / "data"

EXPECTED_MAPPING = {
    "BH-A17": ("HXM-6Q2A", "inverted"),
    "BH-AA20": ("HXM-1G4W", "inverted"),
    "BH-AB16": ("HXM-7F2X", "same"),
    "BH-C04": ("HXM-9R4C", "same"),
    "BH-E11": ("HXM-1N7P", "same"),
    "BH-H06": ("HXM-3T8D", "inverted"),
    "BH-J03": ("HXM-5V2K", "same"),
    "BH-K19": ("HXM-8B1S", "inverted"),
    "BH-M08": ("HXM-4L6W", "inverted"),
    "BH-P15": ("HXM-2J9F", "same"),
    "BH-R02": ("HXM-7C3M", "same"),
    "BH-S21": ("HXM-0P5R", "inverted"),
    "BH-T07": ("HXM-6H8T", "same"),
    "BH-V12": ("HXM-9D1V", "inverted"),
    "BH-W05": ("HXM-4A7N", "same"),
    "BH-X18": ("HXM-2Z6Q", "inverted"),
    "BH-Y09": ("HXM-8K3B", "inverted"),
    "BH-Z14": ("HXM-5S9L", "same"),
}


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_payloads(root: Path, payloads: dict[str, dict]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _write_json(root / "station.json", payloads["station"])
    _write_json(root / "branches.json", payloads["branches"])
    _write_json(root / "meters.json", payloads["meters"])
    return root


def _run_app(input_dir: Path, result_path: Path, ledger_path: Path, timeout: int = 45) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            APP,
            "--input",
            str(input_dir),
            "--result",
            str(result_path),
            "--ledger",
            str(ledger_path),
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _payloads(branch_count: int = 4, sample_count: int | None = None) -> dict[str, dict]:
    samples = sample_count if sample_count is not None else max(16, branch_count)
    interval = 15
    station = {
        "schema_version": "secondary-heat-v1",
        "station_id": "TEST-SEC-7",
        "batch_id": "TEST-BATCH-A",
        "sample_interval_min": interval,
        "supply_temp_c": [
            round(72.0 + 1.1 * math.sin(0.19 * k), 6) for k in range(samples)
        ],
        "outdoor_temp_c": [
            round(-4.0 + 1.4 * math.sin(0.13 * k + 0.5), 6) for k in range(samples)
        ],
        "source_heat_kwh": [],
    }
    branches: list[dict] = []
    branch_histories: list[dict] = []
    meters: list[dict] = []
    for index in range(branch_count):
        branch_id = f"BR-A{index + 1:02d}"
        design = 16.0 + 3.4 * index
        signature = [
            round(0.35 + 0.11 * index, 6),
            round(-0.22 + 0.07 * index, 6),
            round(0.61 - 0.03 * index, 6),
            round(-0.47 + 0.09 * index, 6),
        ]
        lag = 8.0 + 0.9 * index
        flow: list[float] = []
        returning: list[float] = []
        demand: list[float] = []
        for k in range(samples):
            current_flow = design * (
                1.0
                + 0.035 * math.sin(0.23 * k + 0.31 * index)
                + 0.018 * math.cos(0.71 * k + 0.11 * index)
            )
            current_return = (
                39.0
                + 0.75 * index
                + 2.2 * math.sin((0.08 + 0.049 * index) * k + 0.37 * index)
                + 0.7 * math.cos((0.29 + 0.031 * index) * k + 0.53 * index)
            )
            delta = max(12.0, station["supply_temp_c"][k] - current_return - 2.0)
            current_demand = current_flow / 3600.0 * 1000.0 * 4.186 * delta * interval / 60.0
            flow.append(round(current_flow, 6))
            returning.append(round(current_return, 6))
            demand.append(round(current_demand, 6))
        branches.append(
            {
                "branch_id": branch_id,
                "design_flow_m3h": design,
                "valve_signature_m3h": signature,
                "wave_lag_min": lag,
            }
        )
        branch_histories.append(
            {
                "flow": flow,
                "return": returning,
                "demand": demand,
                "ua": 760.0 + 95.0 * index,
            }
        )
        sign = -1.0 if index % 2 else 1.0
        meters.append(
            {
                "meter_serial": f"HX-{index + 1:02d}",
                "valve_signature_m3h": [round(sign * value, 6) for value in signature],
                "wave_lag_min": lag + 0.01,
                "flow_m3h": flow,
                "return_temp_c": returning,
                "demand_kwh": demand,
            }
        )
    source: list[float] = []
    for k in range(samples):
        total = 0.0
        for index in range(branch_count):
            previous = branch_histories[index]["return"][max(0, k - 1)]
            lag_temp = 0.55 * branch_histories[index]["return"][k] + 0.45 * previous
            degree = max(0.5, lag_temp - station["outdoor_temp_c"][k]) * interval / 60.0
            total += branch_histories[index]["demand"][k]
            total += branch_histories[index]["ua"] * degree / 1000.0
        source.append(round(total, 6))
    station["source_heat_kwh"] = source
    return {
        "station": station,
        "branches": {"schema_version": "secondary-heat-v1", "branches": branches},
        "meters": {
            "schema_version": "secondary-heat-v1",
            "meters": list(reversed(meters)),
        },
    }


def _case(tmp_path: Path, payloads: dict[str, dict], label: str = "case") -> tuple[Path, Path, Path]:
    root = tmp_path / label
    input_dir = _write_payloads(root / "input", payloads)
    output_dir = root / "out"
    output_dir.mkdir(parents=True)
    return input_dir, output_dir / "result.json", output_dir / "ledger.jsonl"


def _refused(tmp_path: Path, payloads: dict[str, dict], label: str = "case"):
    input_dir, result_path, ledger_path = _case(tmp_path, payloads, label)
    process = _run_app(input_dir, result_path, ledger_path)
    return process, result_path, ledger_path


def _read_result(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _refusal_state(process: subprocess.CompletedProcess, result_path: Path, ledger_path: Path) -> dict:
    return {
        "returncode": process.returncode,
        "stdout": process.stdout,
        "stderr": process.stderr,
        "result_exists": result_path.exists(),
        "ledger_exists": ledger_path.exists(),
    }


def _raw_batch_key(input_dir: Path) -> str:
    digest = hashlib.sha256()
    for name in ("branches.json", "meters.json", "station.json"):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update((input_dir / name).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _unrounded_numbers(value):
    found = []
    if isinstance(value, dict):
        for item in value.values():
            found.extend(_unrounded_numbers(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_unrounded_numbers(item))
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        scaled = float(value) * 1_000_000.0
        if abs(scaled - round(scaled)) > 1e-5:
            found.append(value)
    return found


def _expected_static_map() -> dict[str, tuple[str, str]]:
    return EXPECTED_MAPPING


@pytest.fixture(scope="module")
def static_run(tmp_path_factory):
    root = tmp_path_factory.mktemp("static_batch")
    output = root / "out"
    output.mkdir()
    result_path = output / "result.json"
    ledger_path = output / "ledger.jsonl"
    process = _run_app(DATA_DIR, result_path, ledger_path, timeout=45)
    return {
        "process": process,
        "returncode": process.returncode,
        "stdout": process.stdout,
        "stderr": process.stderr,
        "result_path": result_path,
        "ledger_path": ledger_path,
        "result": _read_result(result_path),
        "result_bytes": result_path.read_bytes(),
        "ledger_bytes": ledger_path.read_bytes(),
    }


def test_candidate_runs_valid_batch_and_publishes(static_run):
    """题面：「运行成功时退出码为 0，标准输出和标准错误都不写内容」"""
    assert static_run["returncode"] == 0
    assert static_run["result_path"].is_file()
    assert static_run["ledger_path"].is_file()
    assert static_run["result"]["assignments"][0]["meter_serial"] == "HXM-6Q2A"


def test_branch_meter_correspondence_is_recovered(static_run):
    """题面：「站内没有保存表计与支路的对照表」"""
    print("CASE MAP-01 MAP-05 MAP-08")
    actual = {
        item["branch_id"]: (item["meter_serial"], item["orientation"])
        for item in static_run["result"]["assignments"]
    }
    assert actual == _expected_static_map()


def test_inverted_valve_signatures_recover_orientation(tmp_path):
    """题面：「表计阀门签名可能同向，也可能整组反向」"""
    print("CASE MAP-02 MAP-06")
    payloads = _payloads()
    input_dir, result_path, ledger_path = _case(tmp_path, payloads)
    process = _run_app(input_dir, result_path, ledger_path)
    actual = {item["branch_id"]: item["orientation"] for item in _read_result(result_path)["assignments"]}
    assert process.returncode == 0
    assert actual["BR-A01"] == "same"
    assert actual["BR-A02"] == "inverted"


def test_unique_assignment_margin_is_positive(static_run):
    """题面：「合法数据保证最优配对的第二优差值严格大于 `1e-9`」"""
    summary = static_run["result"]["assignment_summary"]
    assert summary["second_best_margin"] > 1e-9
    assert summary["objective"] > 0


def test_aggregate_effective_ua_matches_transient_inverse(static_run):
    """题面：「聚合传热系数按支路度时加权计算」"""
    print("CASE LOSS-01 LOSS-04")
    print("HARDCODE-BASE INPUT_SCENARIO_BASE")
    inversion = static_run["result"]["loss_inversion"]
    station = _read_result(DATA_DIR / "station.json")
    meters = _read_result(DATA_DIR / "meters.json")["meters"]
    by_serial = {item["meter_serial"]: item for item in meters}
    weighted_ua = 0.0
    degree_total = 0.0
    for branch in static_run["result"]["branch_balance"]:
        returning = by_serial[branch["meter_serial"]]["return_temp_c"]
        degrees = []
        for sample in range(len(returning)):
            lag_temp = 0.55 * returning[sample] + 0.45 * returning[max(0, sample - 1)]
            degrees.append(
                max(0.5, lag_temp - station["outdoor_temp_c"][sample])
                * station["sample_interval_min"]
                / 60.0
            )
        branch_degrees = math.fsum(degrees)
        degree_total += branch_degrees
        weighted_ua += branch["ua_w_per_k"] * branch_degrees
    expected = weighted_ua / degree_total if degree_total else 0.0
    assert inversion["method"] == "lagged-degree-hours-nnls-v1"
    assert inversion["aggregate_ua_w_per_k"] == pytest.approx(expected, abs=0.00001)
    assert inversion["aggregate_ua_w_per_k"] == pytest.approx(1347.848723, abs=0.02)


def test_branch_ua_and_loss_use_lagged_degree_hours(static_run):
    """题面：「支路热损是 `ua_w_per_k` 与该支路全部 `degree_h` 乘积之和再除以 1000」"""
    station = _read_result(DATA_DIR / "station.json")
    meters = _read_result(DATA_DIR / "meters.json")["meters"]
    by_serial = {item["meter_serial"]: item for item in meters}
    branch = next(item for item in static_run["result"]["branch_balance"] if item["branch_id"] == "BH-Z14")
    returning = by_serial[branch["meter_serial"]]["return_temp_c"]
    degrees = []
    for sample in range(len(returning)):
        lag_temp = 0.55 * returning[sample] + 0.45 * returning[max(0, sample - 1)]
        degrees.append(max(0.5, lag_temp - station["outdoor_temp_c"][sample]) * station["sample_interval_min"] / 60.0)
    expected_loss = branch["ua_w_per_k"] * math.fsum(degrees) / 1000.0
    assert abs(branch["loss_kwh"] - expected_loss) <= 0.00001
    assert branch["ua_w_per_k"] > 0


def test_system_heat_balance_closes_with_signed_error(static_run):
    """题面：「`balance_error_kwh` 是站级输入热量减去需求总计与热损总计，保留正负号」"""
    balance = static_run["result"]["system_balance"]
    expected = balance["source_kwh"] - balance["demand_kwh"] - balance["loss_kwh"]
    assert abs(balance["balance_error_kwh"] - expected) <= 0.00001
    assert abs(balance["balance_error_kwh"]) <= 0.001


def test_repeating_same_batch_is_byte_idempotent(tmp_path):
    """题面：「若同一结果文件和台账文件再次处理完全相同的输入，结果字节与台账字节都必须保持不变」"""
    print("CASE LEDGER-01 LEDGER-07")
    input_dir, result_path, ledger_path = _case(tmp_path, _payloads())
    first = _run_app(input_dir, result_path, ledger_path)
    first_result = result_path.read_bytes()
    first_ledger = ledger_path.read_bytes()
    second = _run_app(input_dir, result_path, ledger_path)
    assert first.returncode == 0
    assert second.returncode == 0
    assert result_path.read_bytes() == first_result
    assert ledger_path.read_bytes() == first_ledger


def test_new_batch_appends_exactly_one_ledger_line(tmp_path):
    """题面：「新批次只追加一行，不得重写或搬动已有行」"""
    payloads = _payloads()
    input_dir, result_path, ledger_path = _case(tmp_path, payloads)
    _run_app(input_dir, result_path, ledger_path)
    payloads["station"]["batch_id"] = "TEST-BATCH-B"
    input_dir = _write_payloads(tmp_path / "case" / "input", payloads)
    process = _run_app(input_dir, result_path, ledger_path)
    assert process.returncode == 0
    assert len(ledger_path.read_bytes().splitlines()) == 2


def test_later_batch_keeps_earlier_ledger_bytes(tmp_path):
    """题面：「新批次只追加一行，不得重写或搬动已有行」"""
    payloads = _payloads()
    input_dir, result_path, ledger_path = _case(tmp_path, payloads)
    _run_app(input_dir, result_path, ledger_path)
    first_ledger = ledger_path.read_bytes()
    payloads["station"]["batch_id"] = "TEST-BATCH-C"
    input_dir = _write_payloads(tmp_path / "case" / "input", payloads)
    process = _run_app(input_dir, result_path, ledger_path)
    assert process.returncode == 0
    assert ledger_path.read_bytes().startswith(first_ledger)


def test_input_failure_preserves_existing_output_bytes(tmp_path):
    """题面：「任何失败都不能改动输入、不能留下临时文件、不能留下半行台账」"""
    print("CASE PUBLISH-01 PUBLISH-03")
    payloads = _payloads()
    input_dir, result_path, ledger_path = _case(tmp_path, payloads)
    _run_app(input_dir, result_path, ledger_path)
    old_result = result_path.read_bytes()
    old_ledger = ledger_path.read_bytes()
    (input_dir / "station.json").write_text("{", encoding="utf-8")
    process = _run_app(input_dir, result_path, ledger_path)
    assert process.returncode == 2
    assert result_path.read_bytes() == old_result
    assert ledger_path.read_bytes() == old_ledger
    assert list(result_path.parent.glob(".*.tmp")) == []


def test_ledger_target_failure_restores_old_result(tmp_path):
    """题面：「结果替换成功后，台账替换失败时，必须把结果恢复成运行前字节」"""
    print("CASE PUBLISH-02 PUBLISH-04 PUBLISH-05")
    payloads = _payloads()
    root = tmp_path / "case"
    input_dir = _write_payloads(root / "input", payloads)
    output = root / "out"
    output.mkdir()
    result_path = output / "result.json"
    result_path.write_bytes(b"old-result-bytes\n")
    ledger_path = output / "ledger"
    ledger_path.mkdir()
    process = _run_app(input_dir, result_path, ledger_path)
    assert process.returncode == 2
    assert result_path.read_bytes() == b"old-result-bytes\n"
    assert ledger_path.is_dir()
    assert list(output.glob(".*.tmp")) == []


def test_valid_batch_recovers_mapping_inverts_loss_and_appends_ledger(tmp_path):
    """题面：「让下一批数据能够直接算出表计归属、热平衡和本批热损，并接着原来的批次台账落账」"""
    print("CASE COMBINE-01 COMBINE-02 MAP-07")
    payloads = _payloads()
    input_dir, result_path, ledger_path = _case(tmp_path, payloads)
    first = _run_app(input_dir, result_path, ledger_path)
    first_result = _read_result(result_path)
    payloads["station"]["batch_id"] = "TEST-BATCH-D"
    input_dir = _write_payloads(tmp_path / "case" / "input", payloads)
    second = _run_app(input_dir, result_path, ledger_path)
    second_result = _read_result(result_path)
    assert first.returncode == 0
    assert second.returncode == 0
    assert first_result["assignments"][0]["meter_serial"] == "HX-01"
    assert second_result["assignments"][0]["meter_serial"] == "HX-01"
    assert second_result["loss_inversion"]["aggregate_ua_w_per_k"] > 0
    assert len(ledger_path.read_text(encoding="utf-8").splitlines()) == 2


def test_assignment_and_balance_rows_follow_business_order(static_run):
    """题面：「`assignments` 按支路升序，`branch_balance` 也按支路升序」"""
    print("CASE MAP-04")
    assignments = [item["branch_id"] for item in static_run["result"]["assignments"]]
    balance = [item["branch_id"] for item in static_run["result"]["branch_balance"]]
    assert assignments == sorted(assignments)
    assert balance == sorted(balance)


def test_successful_run_is_silent(static_run):
    """题面：「运行成功时退出码为 0，标准输出和标准错误都不写内容」"""
    print("CASE OUTPUT-01")
    returncode = static_run["returncode"]
    stdout = static_run["stdout"]
    stderr = static_run["stderr"]
    assert returncode == 0
    assert stdout == ""
    assert stderr == ""


def test_result_top_level_keys_and_order_are_exact(static_run):
    """题面：「结果只交出一个对象，一级成员固定为八项且按表内顺序出现」"""
    print("CASE RESULT-01 RESULT-08 RESULT-09 RESULT-10")
    expected = [
        "contract_version",
        "batch_key",
        "station_id",
        "assignment_summary",
        "assignments",
        "branch_balance",
        "system_balance",
        "loss_inversion",
    ]
    assert list(static_run["result"]) == expected
    assert static_run["result"]["contract_version"] == "secondary-heat-result-v1"


def test_assignment_objects_have_exact_keys_and_types(static_run):
    """题面：「`assignments[]` | `branch_id`, `meter_serial`, `orientation`, `valve_rmse`, `lag_error_min`, `flow_log_error`, `assignment_cost`」"""
    keys = {
        "branch_id",
        "meter_serial",
        "orientation",
        "valve_rmse",
        "lag_error_min",
        "flow_log_error",
        "assignment_cost",
    }
    first = static_run["result"]["assignments"][0]
    assert set(first) == keys
    assert isinstance(first["branch_id"], str)
    assert isinstance(first["meter_serial"], str)
    assert first["orientation"] in {"same", "inverted"}
    assert all(isinstance(first[key], (int, float)) and not isinstance(first[key], bool) for key in keys - {"branch_id", "meter_serial", "orientation"})


def test_branch_balance_objects_have_exact_keys_and_types(static_run):
    """题面：「`branch_balance[]` | `branch_id`, `meter_serial`, `demand_kwh`, `loss_kwh`, `supply_kwh`, `flow_m3`, `ua_w_per_k`」"""
    keys = {
        "branch_id",
        "meter_serial",
        "demand_kwh",
        "loss_kwh",
        "supply_kwh",
        "flow_m3",
        "ua_w_per_k",
    }
    first = static_run["result"]["branch_balance"][0]
    assert set(first) == keys
    assert isinstance(first["branch_id"], str)
    assert isinstance(first["meter_serial"], str)
    assert all(isinstance(first[key], (int, float)) and not isinstance(first[key], bool) for key in keys - {"branch_id", "meter_serial"})


def test_system_and_inversion_objects_have_exact_keys_and_types(static_run):
    """题面：「`system_balance` | `source_kwh`, `demand_kwh`, `loss_kwh`, `balance_error_kwh`」"""
    system = static_run["result"]["system_balance"]
    inversion = static_run["result"]["loss_inversion"]
    assert set(system) == {"source_kwh", "demand_kwh", "loss_kwh", "balance_error_kwh"}
    assert set(inversion) == {"samples", "degree_hours_total", "aggregate_ua_w_per_k", "method"}
    assert isinstance(inversion["samples"], int) and not isinstance(inversion["samples"], bool)
    assert isinstance(inversion["method"], str)


def test_result_uses_compact_utf8_json_with_one_newline(static_run):
    """题面：「结果采用 UTF-8、紧凑 JSON：对象键之间和数组元素之间不加空格」"""
    print("CASE RESULT-07")
    raw = static_run["result_bytes"]
    expected = (json.dumps(static_run["result"], ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    assert raw == expected
    assert raw.count(b"\n") == 1


def test_branch_flow_volume_uses_history_and_interval(tmp_path):
    """题面：「`flow_m3` 是表计历史流量乘以采样间隔分钟再除以 60 后求和」"""
    payloads = _payloads()
    input_dir, result_path, ledger_path = _case(tmp_path, payloads)
    process = _run_app(input_dir, result_path, ledger_path)
    result = _read_result(result_path)
    meter = next(item for item in payloads["meters"]["meters"] if item["meter_serial"] == "HX-01")
    expected = math.fsum(meter["flow_m3h"]) * payloads["station"]["sample_interval_min"] / 60.0
    actual = next(item["flow_m3"] for item in result["branch_balance"] if item["meter_serial"] == "HX-01")
    assert process.returncode == 0
    assert abs(actual - expected) <= 0.00001


def test_batch_key_consumes_raw_input_bytes(static_run, tmp_path):
    """题面：「批次键按文件名 `branches.json`、`meters.json`、`station.json` 的字节升序依次取原始文件，计算 SHA-256」"""
    print("CASE BATCH-01 BATCH-02")
    input_copy = tmp_path / "raw"
    _write_payloads(input_copy, {
        "station": _read_result(DATA_DIR / "station.json"),
        "branches": _read_result(DATA_DIR / "branches.json"),
        "meters": _read_result(DATA_DIR / "meters.json"),
    })
    station_copy = input_copy / "station.json"
    station_copy.write_bytes(b" " + station_copy.read_bytes())
    assert static_run["result"]["batch_key"] == _raw_batch_key(DATA_DIR)
    assert static_run["result"]["batch_key"] != _raw_batch_key(input_copy)


def test_ledger_line_has_exact_compact_schema(static_run):
    """题面：「台账摘要不能多加字段；六个成员及其含义见下表」"""
    print("CASE LEDGER-04 LEDGER-06 LEDGER-08")
    lines = static_run["ledger_bytes"].splitlines(keepends=True)
    record = json.loads(lines[0])
    assert len(lines) == 1
    assert set(record) == {"batch_key", "station_id", "result_sha256", "source_kwh", "demand_kwh", "loss_kwh"}
    assert (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8") == lines[0]


def test_corrupt_ledger_is_refused_without_side_effects(tmp_path):
    """题面：「台账中出现重复批次键且结果摘要相同，就保持整份台账字节不变」"""
    input_dir, result_path, ledger_path = _case(tmp_path, _payloads())
    old_result = b"old-result\n"
    old_ledger = b'{"batch_key":"broken"}\n'
    result_path.write_bytes(old_result)
    ledger_path.write_bytes(old_ledger)
    process = _run_app(input_dir, result_path, ledger_path)
    assert process.returncode == 2
    assert result_path.read_bytes() == old_result
    assert ledger_path.read_bytes() == old_ledger
    assert "Traceback" not in process.stderr


def test_duplicate_station_key_is_refused(tmp_path):
    """题面：「对象任意层级出现重复键都要拒绝」"""
    input_dir, result_path, ledger_path = _case(tmp_path, _payloads())
    original = (input_dir / "station.json").read_text(encoding="utf-8")
    duplicated = '{"schema_version":"secondary-heat-v1",' + original[1:]
    (input_dir / "station.json").write_text(duplicated, encoding="utf-8")
    process = _run_app(input_dir, result_path, ledger_path)
    assert process.returncode == 2
    assert "重复键" in process.stderr
    assert not result_path.exists()


def test_duplicate_nested_branch_key_is_refused(tmp_path):
    """题面：「对象任意层级出现重复键都要拒绝」"""
    input_dir, result_path, ledger_path = _case(tmp_path, _payloads())
    text = (input_dir / "branches.json").read_text(encoding="utf-8")
    text = text.replace('"branch_id": "BR-A01"', '"branch_id": "BR-A01", "branch_id": "BR-A01"', 1)
    (input_dir / "branches.json").write_text(text, encoding="utf-8")
    process = _run_app(input_dir, result_path, ledger_path)
    assert process.returncode == 2
    assert "重复键" in process.stderr
    assert not ledger_path.exists()


def test_duplicate_nested_meter_key_is_refused(tmp_path):
    """题面：「对象任意层级出现重复键都要拒绝」"""
    input_dir, result_path, ledger_path = _case(tmp_path, _payloads())
    text = (input_dir / "meters.json").read_text(encoding="utf-8")
    text = text.replace('"meter_serial": "HX-01"', '"meter_serial": "HX-01", "meter_serial": "HX-01"', 1)
    (input_dir / "meters.json").write_text(text, encoding="utf-8")
    process = _run_app(input_dir, result_path, ledger_path)
    assert process.returncode == 2
    assert "重复键" in process.stderr
    assert not result_path.exists()


def test_malformed_json_is_refused_without_traceback(tmp_path):
    """题面：「三份文件都必须使用 UTF-8 编码和标准 JSON」"""
    print("CASE JSON-04 OUTPUT-02")
    input_dir, result_path, ledger_path = _case(tmp_path, _payloads())
    old_result = b"prior-result\n"
    old_ledger = b'{"batch_key":"prior"}\n'
    result_path.write_bytes(old_result)
    ledger_path.write_bytes(old_ledger)
    (input_dir / "meters.json").write_text("{", encoding="utf-8")
    process = _run_app(input_dir, result_path, ledger_path)
    assert process.returncode == 2
    assert process.stderr.startswith("处理未完成: ")
    assert "Traceback" not in process.stderr
    assert result_path.read_bytes() == old_result
    assert ledger_path.read_bytes() == old_ledger
    assert list(result_path.parent.glob(".*.tmp")) == []


def test_station_missing_key_is_refused(tmp_path):
    """题面：「| `station.json` | `schema_version`, `station_id`, `batch_id`, `sample_interval_min`, `supply_temp_c`, `outdoor_temp_c`, `source_heat_kwh` |」"""
    print("CASE SCHEMA-01 SCHEMA-17")
    payloads = _payloads()
    payloads["station"].pop("batch_id")
    process_missing, result_missing, ledger_missing = _refused(tmp_path, payloads, "alpha")
    state_missing = _refusal_state(process_missing, result_missing, ledger_missing)
    schema_payloads = _payloads()
    schema_payloads["station"]["schema_version"] = 3
    process_schema, result_schema, ledger_schema = _refused(tmp_path, schema_payloads, "beta")
    state_schema = _refusal_state(process_schema, result_schema, ledger_schema)
    id_payloads = _payloads()
    id_payloads["station"]["station_id"] = "   "
    process_id, result_id, ledger_id = _refused(tmp_path, id_payloads, "gamma")
    state_id = _refusal_state(process_id, result_id, ledger_id)
    assert state_missing["returncode"] == 2
    assert state_missing["stdout"] == ""
    assert state_missing["stderr"].startswith("处理未完成: ")
    assert state_missing["result_exists"] is False
    assert state_schema["returncode"] == 2
    assert state_schema["stderr"].startswith("处理未完成: ")
    assert state_schema["result_exists"] is False
    assert state_id["returncode"] == 2
    assert state_id["stderr"].startswith("处理未完成: ")
    assert state_id["ledger_exists"] is False


def test_station_extra_key_is_refused(tmp_path):
    """题面：「| `station.json` | `schema_version`, `station_id`, `batch_id`, `sample_interval_min`, `supply_temp_c`, `outdoor_temp_c`, `source_heat_kwh` |」"""
    payloads = _payloads()
    payloads["station"]["shift_note"] = "manual"
    process, result_path, ledger_path = _refused(tmp_path, payloads)
    state = _refusal_state(process, result_path, ledger_path)
    assert state["returncode"] == 2
    assert state["stdout"] == ""
    assert state["stderr"].startswith("处理未完成: ")
    assert state["ledger_exists"] is False


def test_station_bool_integer_type_is_refused(tmp_path):
    """题面：「采样间隔取 5 到 60 的整数，布尔值排除」"""
    payloads = _payloads()
    payloads["station"]["sample_interval_min"] = True
    process, result_path, ledger_path = _refused(tmp_path, payloads)
    state = _refusal_state(process, result_path, ledger_path)
    assert state["returncode"] == 2
    assert state["stdout"] == ""
    assert state["stderr"].startswith("处理未完成: ")
    assert state["result_exists"] is False


def test_branch_missing_key_is_refused(tmp_path):
    """题面：「每个支路项只允许 `branch_id`、`design_flow_m3h`、`valve_signature_m3h`、`wave_lag_min`；多出字段即退回」"""
    payloads = _payloads()
    payloads["branches"]["branches"][0].pop("wave_lag_min")
    process, result_path, ledger_path = _refused(tmp_path, payloads)
    state = _refusal_state(process, result_path, ledger_path)
    assert state["returncode"] == 2
    assert state["stdout"] == ""
    assert state["stderr"].startswith("处理未完成: ")
    assert state["ledger_exists"] is False


def test_branch_extra_key_is_refused(tmp_path):
    """题面：「每个支路项只允许 `branch_id`、`design_flow_m3h`、`valve_signature_m3h`、`wave_lag_min`；多出字段即退回」"""
    print("CASE SCHEMA-05 SCHEMA-15 SCHEMA-19")
    payloads = _payloads()
    payloads["branches"]["branches"][0]["floor"] = 3
    process, result_path, ledger_path = _refused(tmp_path, payloads)
    state = _refusal_state(process, result_path, ledger_path)
    top_payloads = _payloads()
    top_payloads["branches"]["revision"] = 2
    top_process, top_result, top_ledger = _refused(tmp_path, top_payloads, "delta")
    top_state = _refusal_state(top_process, top_result, top_ledger)
    version_payloads = _payloads()
    version_payloads["branches"]["schema_version"] = "secondary-heat-v2"
    version_process, version_result, version_ledger = _refused(tmp_path, version_payloads, "epsilon")
    version_state = _refusal_state(version_process, version_result, version_ledger)
    assert state["returncode"] == 2
    assert state["stdout"] == ""
    assert state["stderr"].startswith("处理未完成: ")
    assert state["result_exists"] is False
    assert top_state["returncode"] == 2
    assert top_state["stderr"].startswith("处理未完成: ")
    assert top_state["ledger_exists"] is False
    assert version_state["returncode"] == 2
    assert version_state["stderr"].startswith("处理未完成: ")
    assert version_state["result_exists"] is False


def test_branch_wrong_type_is_refused(tmp_path):
    """题面：「`design_flow_m3h` 是大于零的有限数字」"""
    payloads = _payloads()
    payloads["branches"]["branches"][0]["design_flow_m3h"] = "16.0"
    process, result_path, ledger_path = _refused(tmp_path, payloads)
    state = _refusal_state(process, result_path, ledger_path)
    assert state["returncode"] == 2
    assert state["stdout"] == ""
    assert state["stderr"].startswith("处理未完成: ")
    assert state["ledger_exists"] is False
    zero_payloads = _payloads()
    zero_payloads["branches"]["branches"][0]["design_flow_m3h"] = 0.0
    zero_process, zero_result, zero_ledger = _refused(
        tmp_path,
        zero_payloads,
        "zero-design-flow",
    )
    zero_state = _refusal_state(zero_process, zero_result, zero_ledger)
    assert zero_state["returncode"] == 2
    assert zero_state["stdout"] == ""
    assert zero_state["stderr"] == "处理未完成: design_flow_m3h 必须大于零\n"
    assert zero_state["result_exists"] is False


def test_meter_missing_key_is_refused(tmp_path):
    """题面：「每只表只允许 `meter_serial`、`valve_signature_m3h`、`wave_lag_min`、`flow_m3h`、`return_temp_c`、`demand_kwh`；多出字段即退回」"""
    payloads = _payloads()
    payloads["meters"]["meters"][0].pop("demand_kwh")
    process, result_path, ledger_path = _refused(tmp_path, payloads)
    state = _refusal_state(process, result_path, ledger_path)
    assert state["returncode"] == 2
    assert state["stdout"] == ""
    assert state["stderr"].startswith("处理未完成: ")
    assert state["result_exists"] is False


def test_meter_extra_key_is_refused(tmp_path):
    """题面：「每只表只允许 `meter_serial`、`valve_signature_m3h`、`wave_lag_min`、`flow_m3h`、`return_temp_c`、`demand_kwh`；多出字段即退回」"""
    print("CASE SCHEMA-08 SCHEMA-16 SCHEMA-20")
    payloads = _payloads()
    payloads["meters"]["meters"][0]["site_tag"] = "lost"
    process, result_path, ledger_path = _refused(tmp_path, payloads)
    state = _refusal_state(process, result_path, ledger_path)
    top_payloads = _payloads()
    top_payloads["meters"]["revision"] = 2
    top_process, top_result, top_ledger = _refused(tmp_path, top_payloads, "zeta")
    top_state = _refusal_state(top_process, top_result, top_ledger)
    version_payloads = _payloads()
    version_payloads["meters"]["schema_version"] = "secondary-heat-v2"
    version_process, version_result, version_ledger = _refused(tmp_path, version_payloads, "eta")
    version_state = _refusal_state(version_process, version_result, version_ledger)
    assert state["returncode"] == 2
    assert state["stdout"] == ""
    assert state["stderr"].startswith("处理未完成: ")
    assert state["ledger_exists"] is False
    assert top_state["returncode"] == 2
    assert top_state["stderr"].startswith("处理未完成: ")
    assert top_state["result_exists"] is False
    assert version_state["returncode"] == 2
    assert version_state["stderr"].startswith("处理未完成: ")
    assert version_state["ledger_exists"] is False


def test_meter_wrong_array_element_type_is_refused(tmp_path):
    """题面：「元素只收有限 JSON 数字」"""
    payloads = _payloads()
    payloads["meters"]["meters"][0]["flow_m3h"][1] = "18.2"
    process, result_path, ledger_path = _refused(tmp_path, payloads)
    state = _refusal_state(process, result_path, ledger_path)
    assert state["returncode"] == 2
    assert state["stdout"] == ""
    assert state["stderr"].startswith("处理未完成: ")
    assert state["result_exists"] is False


def test_duplicate_branch_id_is_refused(tmp_path):
    """题面：「`branch_id` 是唯一、非空的字符串」"""
    payloads = _payloads()
    payloads["branches"]["branches"][1]["branch_id"] = payloads["branches"]["branches"][0]["branch_id"]
    process, result_path, ledger_path = _refused(tmp_path, payloads)
    state = _refusal_state(process, result_path, ledger_path)
    assert state["returncode"] == 2
    assert state["stdout"] == ""
    assert state["stderr"].startswith("处理未完成: ")
    assert state["ledger_exists"] is False


def test_meter_count_mismatch_is_refused(tmp_path):
    """题面：「表计项数目必须与支路项相同」"""
    print("CASE SCHEMA-11 SCHEMA-21")
    payloads = _payloads()
    payloads["meters"]["meters"].pop()
    process, result_path, ledger_path = _refused(tmp_path, payloads)
    state = _refusal_state(process, result_path, ledger_path)
    version_payloads = _payloads()
    version_payloads["meters"]["schema_version"] = "secondary-heat-v2"
    version_process, version_result, version_ledger = _refused(tmp_path, version_payloads, "theta")
    version_state = _refusal_state(version_process, version_result, version_ledger)
    assert state["returncode"] == 2
    assert state["stdout"] == ""
    assert state["stderr"].startswith("处理未完成: ")
    assert state["result_exists"] is False
    assert version_state["returncode"] == 2
    assert version_state["stderr"].startswith("处理未完成: ")
    assert version_state["ledger_exists"] is False


def test_station_array_length_mismatch_is_refused(tmp_path):
    """题面：「`supply_temp_c`、`outdoor_temp_c` 和 `source_heat_kwh` 三列时序同长，每列 12 到 48 段，元素只收有限 JSON 数字」"""
    print("CASE SCHEMA-12 SCHEMA-18")
    payloads = _payloads()
    payloads["station"]["source_heat_kwh"].pop()
    process, result_path, ledger_path = _refused(tmp_path, payloads)
    state = _refusal_state(process, result_path, ledger_path)
    assert state["returncode"] == 2
    assert state["stdout"] == ""
    assert state["stderr"].startswith("处理未完成: ")
    assert state["result_exists"] is False


def test_meter_nonpositive_flow_is_refused(tmp_path):
    """题面：「流量元素必须大于零，回水温度必须是有限数字，热量元素不得小于零」"""
    print("CASE SCHEMA-13")
    payloads = _payloads()
    payloads["meters"]["meters"][0]["flow_m3h"][0] = 0
    process, result_path, ledger_path = _refused(tmp_path, payloads)
    state = _refusal_state(process, result_path, ledger_path)
    assert state["returncode"] == 2
    assert state["stdout"] == ""
    assert state["stderr"].startswith("处理未完成: ")
    assert state["ledger_exists"] is False


def test_empty_command_path_is_refused(tmp_path):
    """题面：「命令给出的输入路径、结果路径和台账路径不能为空字符串」"""
    print("CASE FS-02")
    output = tmp_path / "case"
    output.mkdir()
    result_path = output / "result.json"
    ledger_path = output / "ledger.jsonl"
    process = subprocess.run(
        [
            sys.executable,
            APP,
            "--input",
            "",
            "--result",
            str(result_path),
            "--ledger",
            str(ledger_path),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert process.returncode == 2
    assert process.stdout == ""
    assert process.stderr.startswith("处理未完成: ")
    assert not result_path.exists()


def test_output_numbers_use_six_decimal_rounding(static_run):
    """题面：「所有输出数字先按十进制四舍五入到小数点后六位，恰好半值时远离零取整；零不写成 `-0.000000`」"""
    print("CASE RESULT-07 RESULT-13")
    unrounded = _unrounded_numbers(static_run["result"])
    assert unrounded == []
    assert b"-0.0" not in static_run["result_bytes"]


def test_result_pairing_and_sample_count_come_from_input(tmp_path):
    """题面：「结果中的时间样本数、支路数、排序和配对都来自本次输入」"""
    print("CASE RESULT-11 RESULT-12")
    payloads = _payloads(branch_count=5, sample_count=18)
    input_dir, result_path, ledger_path = _case(tmp_path, payloads)
    process = _run_app(input_dir, result_path, ledger_path)
    result = _read_result(result_path)
    assert process.returncode == 0
    assert result["loss_inversion"]["samples"] == 18
    assert [item["branch_id"] for item in result["assignments"]] == ["BR-A01", "BR-A02", "BR-A03", "BR-A04", "BR-A05"]
    assert [item["meter_serial"] for item in result["assignments"]] == ["HX-01", "HX-02", "HX-03", "HX-04", "HX-05"]


def test_eighteen_branches_are_accepted(tmp_path):
    """题面：「支路账列出 2 到 18 项」"""
    print("HARDCODE-VARIANT INPUT_SCENARIO_BRANCH18")
    payloads = _payloads(branch_count=18, sample_count=20)
    input_dir, result_path, ledger_path = _case(tmp_path, payloads)
    process = _run_app(input_dir, result_path, ledger_path)
    assert process.returncode == 0
    report = _read_result(result_path)
    assert len(report["assignments"]) == 18
    assert report["loss_inversion"]["aggregate_ua_w_per_k"] > 0


def test_eighteen_branch_batch_finishes_within_public_limit(tmp_path):
    """题面：「一个十八支路批次从进程启动到两个文件发布完成，在这台站机上应在 8 秒内结束」"""
    print("CASE PERF-01")
    payloads = _payloads(branch_count=18, sample_count=20)
    input_dir, result_path, ledger_path = _case(tmp_path, payloads)
    started = time.monotonic()
    process = _run_app(input_dir, result_path, ledger_path, timeout=10)
    elapsed = time.monotonic() - started
    assert process.returncode == 0
    assert elapsed < 8


def test_nineteen_branches_are_refused(tmp_path):
    """题面：「支路账列出 2 到 18 项」"""
    payloads = _payloads(branch_count=19, sample_count=20)
    process, result_path, ledger_path = _refused(tmp_path, payloads)
    state = _refusal_state(process, result_path, ledger_path)
    assert state["returncode"] == 2
    assert state["stdout"] == ""
    assert state["stderr"].startswith("处理未完成: ")
    assert state["ledger_exists"] is False


def test_symlinked_input_directory_is_refused(tmp_path):
    """题面：「输入目录必须是真实目录」"""
    root = tmp_path / "case"
    real_input = _write_payloads(root / "input", _payloads())
    linked_input = root / "linked_input"
    linked_input.symlink_to(real_input, target_is_directory=True)
    output = root / "out"
    output.mkdir()
    result_path = output / "result.json"
    ledger_path = output / "ledger.jsonl"
    process = _run_app(linked_input, result_path, ledger_path)
    assert process.returncode == 2
    assert "真实目录" in process.stderr
    assert not result_path.exists()
