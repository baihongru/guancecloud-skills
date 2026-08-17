#!/usr/bin/env python3
"""Chunk, stitch, detect anomaly windows, and compare normalized time series."""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


Point = Dict[str, float]
Series = Dict[str, Any]


DEFAULT_LATENCY_RE = r"avg|max|p75|p90|p95|p99|duration|latency|elapsed|response|cost"


def die(message: str, code: int = 2) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


def load_json(path: Optional[str]) -> Any:
    if path and path != "-":
        text = Path(path).read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        die(f"invalid JSON input: {exc}")


def dump_json(obj: Any) -> None:
    json.dump(obj, sys.stdout, ensure_ascii=False, indent=2, sort_keys=False)
    sys.stdout.write("\n")


def number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        value = float(value)
    elif isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(value):
        return None
    return value


def point_from_any(raw: Any) -> Optional[Point]:
    if isinstance(raw, dict):
        ts = raw.get("ts", raw.get("timestamp", raw.get("time")))
        value = raw.get("value", raw.get("v", raw.get("y")))
    elif isinstance(raw, (list, tuple)) and len(raw) >= 2:
        first = number(raw[0])
        second = number(raw[1])
        if first is None or second is None:
            return None
        if first > 10_000_000_000:
            ts, value = first, second
        elif second > 10_000_000_000:
            ts, value = second, first
        else:
            ts, value = first, second
    else:
        return None

    ts_num = number(ts)
    value_num = number(value)
    if ts_num is None or value_num is None:
        return None
    if ts_num < 10_000_000_000:
        ts_num *= 1000
    return {"ts": int(ts_num), "value": float(value_num)}


def normalize_series(raw: Dict[str, Any]) -> Optional[Series]:
    points_raw = raw.get("points", raw.get("values", raw.get("data")))
    if not isinstance(points_raw, list):
        return None
    points = [point for point in (point_from_any(item) for item in points_raw) if point]
    if not points:
        return None
    tags = raw.get("tags") if isinstance(raw.get("tags"), dict) else {}
    name = raw.get("name", raw.get("metric", raw.get("field", "value")))
    return {
        "name": str(name),
        "tags": {str(k): str(v) for k, v in tags.items()},
        "points": sorted(points, key=lambda item: item["ts"]),
    }


def collect_series(obj: Any) -> List[Series]:
    found: List[Series] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            series = normalize_series(value)
            if series:
                found.append(series)
                return
            if isinstance(value.get("series"), list):
                for item in value["series"]:
                    visit(item)
            if isinstance(value.get("chunks"), list):
                for item in value["chunks"]:
                    visit(item)
            for key in ("data", "result", "results", "payload", "content"):
                if key in value:
                    visit(value[key])
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(obj)
    return found


def series_key(series: Series) -> str:
    tags = series.get("tags") if isinstance(series.get("tags"), dict) else {}
    return json.dumps(
        {"name": str(series.get("name", "value")), "tags": sorted(tags.items())},
        ensure_ascii=False,
        sort_keys=True,
    )


def median(values: List[float]) -> float:
    return float(statistics.median(values))


def mad(values: List[float], center: float) -> float:
    deviations = [abs(value - center) for value in values]
    return float(statistics.median(deviations)) if deviations else 0.0


def baseline_values(values: List[float], index: int, args: argparse.Namespace) -> List[float]:
    window_points = max(1, int(args.window_points))
    mode = getattr(args, "baseline_mode", "centered")
    if mode == "past":
        return values[max(0, index - window_points) : index]

    guard_points = max(0, int(getattr(args, "guard_points", 0)))
    left = values[max(0, index - window_points) : max(0, index - guard_points)]
    right_start = min(len(values), index + guard_points + 1)
    right = values[right_start : min(len(values), right_start + window_points)]
    history = left + right
    if len(history) >= args.min_baseline_points:
        return history

    # Near the edges, fall back to all nearby points except the point itself.
    left = values[max(0, index - window_points) : index]
    right = values[index + 1 : min(len(values), index + window_points + 1)]
    return left + right


def median_interval(points: List[Point]) -> int:
    if len(points) < 2:
        return 0
    intervals = [
        int(points[index]["ts"] - points[index - 1]["ts"])
        for index in range(1, len(points))
        if points[index]["ts"] > points[index - 1]["ts"]
    ]
    if not intervals:
        return 0
    return int(statistics.median(intervals))


def cmd_chunk(args: argparse.Namespace) -> None:
    start = int(args.start_ms)
    end = int(args.end_ms)
    interval = int(args.interval_ms)
    if start >= end:
        die("--start-ms must be less than --end-ms")
    if interval <= 0:
        die("--interval-ms must be positive")

    target_points = max(2, int(args.target_points))
    max_points = max(target_points, int(args.max_points))
    overlap_points = max(0, int(args.overlap_points))
    span = interval * min(target_points, max_points)
    max_span = interval * max_points
    overlap = min(interval * overlap_points, max(0, span - interval))

    chunks: List[Dict[str, int]] = []
    if end - start <= max_span:
        chunks.append({"start_ms": start, "end_ms": end})
    else:
        cursor = start
        while cursor < end:
            chunk_end = min(end, cursor + span)
            chunks.append({"start_ms": cursor, "end_ms": chunk_end})
            if chunk_end >= end:
                break
            cursor = chunk_end - overlap

    dump_json(
        {
            "start_ms": start,
            "end_ms": end,
            "interval_ms": interval,
            "target_points": target_points,
            "max_points": max_points,
            "overlap_points": overlap_points,
            "overlap_ms": overlap,
            "chunks": chunks,
        }
    )


def cmd_stitch(args: argparse.Namespace) -> None:
    inputs = [load_json(path) for path in args.inputs] if args.inputs else [load_json(None)]
    merged: Dict[str, Series] = {}
    duplicate_points = 0

    for obj in inputs:
        for series in collect_series(obj):
            key = series_key(series)
            if key not in merged:
                merged[key] = {
                    "name": series["name"],
                    "tags": series.get("tags", {}),
                    "points": [],
                }
            by_ts = {int(point["ts"]): point for point in merged[key]["points"]}
            for point in series["points"]:
                ts = int(point["ts"])
                if ts in by_ts:
                    duplicate_points += 1
                    if args.prefer == "first":
                        continue
                    if args.prefer == "mean":
                        old_value = by_ts[ts]["value"]
                        by_ts[ts] = {"ts": ts, "value": (old_value + point["value"]) / 2.0}
                    else:
                        by_ts[ts] = {"ts": ts, "value": point["value"]}
                else:
                    by_ts[ts] = {"ts": ts, "value": point["value"]}
            merged[key]["points"] = [by_ts[ts] for ts in sorted(by_ts)]

    dump_json(
        {
            "series": list(merged.values()),
            "meta": {
                "series_count": len(merged),
                "duplicate_points": duplicate_points,
                "dedupe_preference": args.prefer,
            },
        }
    )


def detect_metric_windows(series: Series, args: argparse.Namespace) -> List[Dict[str, Any]]:
    points = [
        {"ts": int(point["ts"]), "value": float(point["value"])}
        for point in series.get("points", [])
        if number(point.get("value")) is not None
    ]
    points.sort(key=lambda item: item["ts"])
    if len(points) < args.min_points:
        return []

    interval = median_interval(points)
    merge_gap_ms = max(interval, 1) * args.merge_gap_points
    anomalies: List[Dict[str, Any]] = []
    all_values = [point["value"] for point in points]
    trigger_threshold = min(float(args.threshold), float(args.soft_threshold))

    for index, point in enumerate(points):
        history = baseline_values(all_values, index, args)
        if len(history) < args.min_baseline_points:
            history = all_values[:index] if index >= args.min_baseline_points else []
        if len(history) < args.min_baseline_points:
            continue

        baseline = median(history)
        robust_scale = mad(history, baseline) * 1.4826
        min_scale = max(abs(baseline) * args.min_relative_scale, args.min_abs_scale)
        scale = max(robust_scale, min_scale, 1e-9)
        score = (point["value"] - baseline) / scale
        if args.direction == "negative":
            score = (baseline - point["value"]) / scale
        elif args.direction == "both":
            score = abs(point["value"] - baseline) / scale

        if score >= trigger_threshold:
            anomalies.append(
                {
                    "ts": point["ts"],
                    "value": point["value"],
                    "baseline": baseline,
                    "score": round(score, 4),
                }
            )

    windows: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    for anomaly in anomalies:
        point_end = anomaly["ts"] + max(interval, 1)
        if current and anomaly["ts"] <= current["end_ms"] + merge_gap_ms:
            current["end_ms"] = max(current["end_ms"], point_end)
            current["max_score"] = max(current["max_score"], anomaly["score"])
            current["point_count"] += 1
            current["value_min"] = min(current["value_min"], anomaly["value"])
            current["value_max"] = max(current["value_max"], anomaly["value"])
        else:
            if current:
                windows.append(current)
            current = {
                "start_ms": anomaly["ts"],
                "end_ms": point_end,
                "metric": series.get("name", "value"),
                "tags": series.get("tags", {}),
                "max_score": anomaly["score"],
                "point_count": 1,
                "value_min": anomaly["value"],
                "value_max": anomaly["value"],
                "baseline": anomaly["baseline"],
                "interval_ms": interval,
                "evidence": "hard" if anomaly["score"] >= args.threshold else "soft",
            }
    if current:
        windows.append(current)

    return [window for window in windows if window["point_count"] >= args.min_window_points]


def confidence(max_score: float, metric_count: int, point_count: int) -> str:
    if max_score >= 8 or (max_score >= 4.5 and metric_count >= 2) or point_count >= 4:
        return "high"
    if max_score >= 3.5 or metric_count >= 2:
        return "medium"
    return "low"


def merge_candidate_windows(metric_windows: List[Dict[str, Any]], args: argparse.Namespace) -> List[Dict[str, Any]]:
    if not metric_windows:
        return []
    metric_windows.sort(key=lambda item: (item["start_ms"], item["end_ms"]))
    global_interval = int(
        statistics.median([window.get("interval_ms", 0) for window in metric_windows if window.get("interval_ms", 0)])
        if any(window.get("interval_ms", 0) for window in metric_windows)
        else 1
    )
    gap_ms = max(global_interval, 1) * args.merge_gap_points
    candidates: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    for window in metric_windows:
        if current and window["start_ms"] <= current["end_ms"] + gap_ms:
            current["end_ms"] = max(current["end_ms"], window["end_ms"])
            current["max_score"] = max(current["max_score"], window["max_score"])
            current["point_count"] += window["point_count"]
            current["metric_windows"].append(window)
        else:
            if current:
                candidates.append(current)
            current = {
                "start_ms": window["start_ms"],
                "end_ms": window["end_ms"],
                "max_score": window["max_score"],
                "point_count": window["point_count"],
                "metric_windows": [window],
            }
    if current:
        candidates.append(current)

    qualified = []
    for candidate in candidates:
        metrics = sorted({str(window["metric"]) for window in candidate["metric_windows"]})
        dominant = max(candidate["metric_windows"], key=lambda item: item["max_score"])
        candidate["duration_ms"] = candidate["end_ms"] - candidate["start_ms"]
        candidate["metrics"] = metrics
        candidate["dominant_metric"] = dominant["metric"]
        candidate["evidence_score"] = round(
            candidate["max_score"]
            + max(0, len(metrics) - 1) * args.cross_metric_bonus
            + max(0, candidate["point_count"] - 1) * args.point_count_bonus,
            4,
        )
        candidate["evidence"] = "hard" if candidate["max_score"] >= args.threshold else "soft"
        candidate["confidence"] = confidence(candidate["max_score"], len(metrics), candidate["point_count"])
        candidate["max_score"] = round(candidate["max_score"], 4)
        if candidate["evidence"] == "hard" or (
            candidate["max_score"] >= args.soft_threshold and len(metrics) >= args.min_window_metrics
        ):
            qualified.append(candidate)

    for index, candidate in enumerate(
        sorted(qualified, key=lambda item: item["evidence_score"], reverse=True),
        1,
    ):
        candidate["rank"] = index

    return sorted(qualified, key=lambda item: item["start_ms"])


def valid_points(series: Series) -> List[Point]:
    points = [
        {"ts": int(point["ts"]), "value": float(point["value"])}
        for point in series.get("points", [])
        if isinstance(point, dict) and number(point.get("value")) is not None and number(point.get("ts")) is not None
    ]
    return sorted(points, key=lambda item: item["ts"])


def select_series(series_list: List[Series], metric_regex: Optional[str]) -> Tuple[List[Series], Optional[str]]:
    metric_re = re.compile(metric_regex, re.IGNORECASE) if metric_regex else None
    selected = []
    for series in series_list:
        name = str(series.get("name", ""))
        if not metric_re or metric_re.search(name):
            selected.append(series)
    if selected:
        return selected, None
    return series_list, "metric regex matched no series; detection used all series"


def apply_detection_profile(args: argparse.Namespace, series_list: List[Series]) -> None:
    if getattr(args, "profile", "auto") != "auto":
        if args.window_points is None:
            args.window_points = 12
        if args.min_baseline_points is None:
            args.min_baseline_points = 5
        return

    point_counts = [len(valid_points(series)) for series in series_list]
    max_points = max(point_counts) if point_counts else 0
    if max_points and max_points <= 30:
        default_window = 5
        default_baseline = 3
    elif max_points and max_points <= 90:
        default_window = 8
        default_baseline = 4
    else:
        default_window = 12
        default_baseline = 5
    if args.window_points is None:
        args.window_points = default_window
    if args.min_baseline_points is None:
        args.min_baseline_points = default_baseline


def analyze_series_payload(obj: Any, args: argparse.Namespace) -> Dict[str, Any]:
    series_list = collect_series(obj)
    if not series_list:
        die("no normalized series found")
    apply_detection_profile(args, series_list)

    selected, warning = select_series(series_list, args.metric_regex)
    metric_windows: List[Dict[str, Any]] = []
    point_counts: List[int] = []
    intervals: List[int] = []
    for series in selected:
        points = valid_points(series)
        point_counts.append(len(points))
        interval = median_interval(points)
        if interval:
            intervals.append(interval)
        metric_windows.extend(detect_metric_windows(series, args))

    candidates = merge_candidate_windows(metric_windows, args)
    selected_point_count = sum(point_counts)
    anomaly_point_count = sum(int(window.get("point_count", 0)) for window in metric_windows)
    anomaly_ratio = anomaly_point_count / selected_point_count if selected_point_count else 0.0
    analysis_status = "ok"
    status_reason = "candidate windows are suitable for automatic drilldown"

    if not point_counts or max(point_counts) < args.min_points:
        analysis_status = "too_sparse"
        status_reason = "selected series have fewer points than min_points"
    elif not candidates:
        analysis_status = "no_anomaly"
        status_reason = "no candidate anomaly window crossed the threshold"
    elif len(candidates) > args.max_candidate_windows:
        analysis_status = "too_noisy"
        status_reason = "candidate window count exceeds max_candidate_windows"
    elif anomaly_ratio >= args.max_anomaly_ratio:
        analysis_status = "too_noisy"
        status_reason = "anomaly point ratio exceeds max_anomaly_ratio"

    return {
        "candidate_windows": candidates,
        "metric_windows": metric_windows,
        "meta": {
            "analysis_status": analysis_status,
            "status_reason": status_reason,
            "input_series_count": len(series_list),
            "detected_series_count": len(selected),
            "selected_point_count": selected_point_count,
            "min_selected_points": min(point_counts) if point_counts else 0,
            "max_selected_points": max(point_counts) if point_counts else 0,
            "median_interval_ms": int(statistics.median(intervals)) if intervals else 0,
            "anomaly_point_count": anomaly_point_count,
            "anomaly_point_ratio": round(anomaly_ratio, 4),
            "candidate_window_count": len(candidates),
            "threshold": args.threshold,
            "soft_threshold": args.soft_threshold,
            "window_points": args.window_points,
            "min_points": args.min_points,
            "min_baseline_points": args.min_baseline_points,
            "baseline_mode": args.baseline_mode,
            "guard_points": args.guard_points,
            "min_window_metrics": args.min_window_metrics,
            "profile": getattr(args, "profile", "auto"),
            "max_candidate_windows": args.max_candidate_windows,
            "max_anomaly_ratio": args.max_anomaly_ratio,
            "warning": warning,
        },
    }


def cmd_detect(args: argparse.Namespace) -> None:
    obj = load_json(args.input)
    dump_json(analyze_series_payload(obj, args))


def timezone_for_name(name: str) -> timezone:
    if name.upper() == "UTC":
        return timezone.utc
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        die(f"unknown timezone: {name}")


def format_ms(ts: int, tz_name: str = "UTC") -> str:
    tz = timezone_for_name(tz_name)
    dt = datetime.fromtimestamp(ts / 1000, tz=tz)
    if tz is timezone.utc:
        return dt.strftime("%Y-%m-%d %H:%M:%SZ")
    return dt.strftime("%Y-%m-%d %H:%M:%S %Z")


def point_in_window(ts: int, windows: List[Dict[str, Any]]) -> Optional[int]:
    for index, window in enumerate(windows, 1):
        if int(window["start_ms"]) <= ts < int(window["end_ms"]):
            return index
    return None


def choose_chart_series(series_list: List[Series], args: argparse.Namespace) -> Series:
    candidates = []
    name_re = re.compile(args.series_regex, re.IGNORECASE) if args.series_regex else None
    for series in series_list:
        name = str(series.get("name", ""))
        if name_re and not name_re.search(name):
            continue
        points = valid_points(series)
        if not points:
            continue
        priority = 10
        lower = name.lower()
        if "p99" in lower or ("percentile" in lower and "99" in lower):
            priority = 0
        elif "p95" in lower or ("percentile" in lower and "95" in lower):
            priority = 1
        elif "max" in lower:
            priority = 2
        elif "p90" in lower or ("percentile" in lower and "90" in lower):
            priority = 3
        elif "avg" in lower:
            priority = 4
        elif "p75" in lower or ("percentile" in lower and "75" in lower):
            priority = 5
        candidates.append((priority, -len(points), series))
    if not candidates:
        die("no chartable series found")
    candidates.sort(key=lambda item: (item[0], item[1], str(item[2].get("name", ""))))
    return candidates[0][2]


def parse_tag_filters(raw_items: Optional[List[str]]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for item in raw_items or []:
        if "=" not in item:
            die(f"tag filter must use key=value format: {item}")
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            die(f"tag filter has empty key: {item}")
        result[key] = value
    return result


def tags_match(series: Series, filters: Dict[str, str]) -> bool:
    tags = series.get("tags") if isinstance(series.get("tags"), dict) else {}
    return all(str(tags.get(key, "")) == value for key, value in filters.items())


def has_tag_key(series: Series, key: Optional[str]) -> bool:
    if not key:
        return True
    tags = series.get("tags") if isinstance(series.get("tags"), dict) else {}
    return key in tags


def series_label(series: Series) -> str:
    tags = series.get("tags") if isinstance(series.get("tags"), dict) else {}
    if not tags:
        return str(series.get("name", "value"))
    tag_text = ",".join(f"{key}={value}" for key, value in sorted(tags.items()))
    return f"{series.get('name', 'value')} {{{tag_text}}}"


def metric_priority(name: str) -> int:
    lower = name.lower()
    if "p99" in lower or ("percentile" in lower and "99" in lower):
        return 0
    if "p95" in lower or ("percentile" in lower and "95" in lower):
        return 1
    if "max" in lower:
        return 2
    if "p90" in lower or ("percentile" in lower and "90" in lower):
        return 3
    if "avg" in lower:
        return 4
    return 10


def points_in_range(points: List[Point], start_ms: Optional[int], end_ms: Optional[int]) -> List[Point]:
    result = []
    for point in points:
        ts = int(point["ts"])
        if start_ms is not None and ts < start_ms:
            continue
        if end_ms is not None and ts >= end_ms:
            continue
        result.append(point)
    return result


def align_series(target: Series, candidate: Series, start_ms: Optional[int], end_ms: Optional[int]) -> Tuple[List[float], List[float], List[int]]:
    target_points = points_in_range(valid_points(target), start_ms, end_ms)
    candidate_points = points_in_range(valid_points(candidate), start_ms, end_ms)
    target_by_ts = {int(point["ts"]): float(point["value"]) for point in target_points}
    candidate_by_ts = {int(point["ts"]): float(point["value"]) for point in candidate_points}
    timestamps = sorted(set(target_by_ts).intersection(candidate_by_ts))
    return [target_by_ts[ts] for ts in timestamps], [candidate_by_ts[ts] for ts in timestamps], timestamps


def standard_deviation(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return math.sqrt(max(variance, 0.0))


def robust_z(values: List[float]) -> List[float]:
    if not values:
        return []
    center = median(values)
    scale = mad(values, center) * 1.4826
    if scale <= 1e-9:
        scale = standard_deviation(values)
    if scale <= 1e-9:
        span = max(values) - min(values)
        scale = span if span > 1e-9 else 1.0
    return [(value - center) / scale for value in values]


def pearson(left: List[float], right: List[float]) -> float:
    if len(left) != len(right) or len(left) < 2:
        return 0.0
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    left_den = math.sqrt(sum((a - left_mean) ** 2 for a in left))
    right_den = math.sqrt(sum((b - right_mean) ** 2 for b in right))
    denominator = left_den * right_den
    if denominator <= 1e-12:
        return 0.0
    return max(-1.0, min(1.0, numerator / denominator))


def deltas(values: List[float]) -> List[float]:
    return [values[index] - values[index - 1] for index in range(1, len(values))]


def choose_similarity_target(series_list: List[Series], args: argparse.Namespace) -> Series:
    name_re = re.compile(args.target_regex, re.IGNORECASE) if args.target_regex else None
    tag_filters = parse_tag_filters(args.target_tag)
    candidates = []
    for series in series_list:
        name = str(series.get("name", ""))
        if name_re and not name_re.search(name):
            continue
        if tag_filters and not tags_match(series, tag_filters):
            continue
        points = points_in_range(valid_points(series), args.start_ms, args.end_ms)
        if len(points) < args.min_shared_points:
            continue
        tags = series.get("tags") if isinstance(series.get("tags"), dict) else {}
        candidates.append((metric_priority(name), len(tags), -len(points), series))
    if not candidates:
        die("no target series matched target filters")
    candidates.sort(key=lambda item: (item[0], item[1], item[2], series_label(item[3])))
    return candidates[0][3]


def cmd_similarity(args: argparse.Namespace) -> None:
    obj = load_json(args.input)
    series_list = collect_series(obj)
    if not series_list:
        die("no normalized series found")

    target = choose_similarity_target(series_list, args)
    candidate_re = re.compile(args.candidate_regex, re.IGNORECASE) if args.candidate_regex else None
    candidate_tag_filters = parse_tag_filters(args.candidate_tag)
    matches: List[Dict[str, Any]] = []
    target_values_all = points_in_range(valid_points(target), args.start_ms, args.end_ms)

    for candidate in series_list:
        if series_key(candidate) == series_key(target):
            continue
        name = str(candidate.get("name", ""))
        if candidate_re and not candidate_re.search(name):
            continue
        if candidate_tag_filters and not tags_match(candidate, candidate_tag_filters):
            continue
        if not has_tag_key(candidate, args.candidate_tag_key):
            continue

        target_values, candidate_values, timestamps = align_series(target, candidate, args.start_ms, args.end_ms)
        if len(timestamps) < args.min_shared_points:
            continue

        target_z = robust_z(target_values)
        candidate_z = robust_z(candidate_values)
        value_corr = pearson(target_z, candidate_z)
        delta_corr = pearson(deltas(target_z), deltas(candidate_z)) if len(timestamps) >= 3 else 0.0
        weight_sum = max(args.value_weight + args.delta_weight, 1e-9)
        score = (
            args.value_weight * max(value_corr, 0.0)
            + args.delta_weight * max(delta_corr, 0.0)
        ) / weight_sum

        matches.append(
            {
                "name": str(candidate.get("name", "value")),
                "tags": candidate.get("tags", {}),
                "label": series_label(candidate),
                "score": round(score, 4),
                "value_corr": round(value_corr, 4),
                "delta_corr": round(delta_corr, 4),
                "shared_points": len(timestamps),
                "start_ms": timestamps[0],
                "end_ms": timestamps[-1] + max(median_interval(valid_points(candidate)), 1),
            }
        )

    matches.sort(key=lambda item: (-item["score"], -item["shared_points"], item["label"]))
    for index, match in enumerate(matches[: args.top], 1):
        match["rank"] = index

    dump_json(
        {
            "target": {
                "name": str(target.get("name", "value")),
                "tags": target.get("tags", {}),
                "label": series_label(target),
                "points": len(target_values_all),
            },
            "matches": matches[: args.top],
            "meta": {
                "algorithm": "robust_z_pearson",
                "score_formula": "positive(value_corr) * value_weight + positive(delta_corr) * delta_weight",
                "value_weight": args.value_weight,
                "delta_weight": args.delta_weight,
                "min_shared_points": args.min_shared_points,
                "candidate_count": len(matches),
                "top": args.top,
                "start_ms": args.start_ms,
                "end_ms": args.end_ms,
            },
        }
    )


def bucket_points(points: List[Point], width: int) -> List[Dict[str, Any]]:
    if not points:
        return []
    if len(points) <= width:
        return [{"ts": point["ts"], "value": point["value"], "points": [point]} for point in points]
    start = points[0]["ts"]
    end = points[-1]["ts"]
    span = max(end - start, 1)
    buckets: List[List[Point]] = [[] for _ in range(width)]
    for point in points:
        index = int((point["ts"] - start) / span * (width - 1))
        buckets[max(0, min(width - 1, index))].append(point)
    result = []
    for index, bucket in enumerate(buckets):
        if bucket:
            value = sum(point["value"] for point in bucket) / len(bucket)
            ts = int(sum(point["ts"] for point in bucket) / len(bucket))
            result.append({"ts": ts, "value": value, "points": bucket})
        else:
            ts = int(start + span * index / max(width - 1, 1))
            result.append({"ts": ts, "value": None, "points": []})
    return result


def render_chart(series: Series, windows: List[Dict[str, Any]], meta: Dict[str, Any], width: int, tz_name: str) -> str:
    points = valid_points(series)
    buckets = bucket_points(points, width)
    values = [bucket["value"] for bucket in buckets if bucket["value"] is not None]
    if not values:
        die("selected series has no numeric points")
    low = min(values)
    high = max(values)
    scale = high - low if high > low else 1.0
    chars = " .:-=+*%@"
    plot = []
    marker = [" " for _ in buckets]
    previous_anomaly_index: Optional[int] = None
    for index, bucket in enumerate(buckets):
        if bucket["value"] is None:
            plot.append(" ")
            previous_anomaly_index = None
            continue
        anomaly_index = point_in_window(int(bucket["ts"]), windows)
        if anomaly_index:
            plot.append("#")
            if anomaly_index != previous_anomaly_index:
                label = f"A{anomaly_index}"
                for offset, char in enumerate(label):
                    if index + offset < len(marker):
                        marker[index + offset] = char
            previous_anomaly_index = anomaly_index
        else:
            level = int((bucket["value"] - low) / scale * (len(chars) - 1))
            plot.append(chars[max(0, min(len(chars) - 1, level))])
            previous_anomaly_index = None

    tags = series.get("tags") if isinstance(series.get("tags"), dict) else {}
    tag_text = " ".join(f"{key}={value}" for key, value in sorted(tags.items()))
    lines = [
        f"analysis_status: {meta.get('analysis_status', 'unknown')}",
        f"status_reason: {meta.get('status_reason', '')}",
        f"series: {series.get('name', 'value')} {tag_text}".rstrip(),
        f"time: {format_ms(points[0]['ts'], tz_name)} -> {format_ms(points[-1]['ts'], tz_name)}",
        f"value: min={low:.3f} max={high:.3f} points={len(points)}",
        f"plot : {''.join(plot)}",
        f"mark : {''.join(marker).rstrip()}",
    ]
    if windows:
        lines.append("windows:")
        for index, window in enumerate(windows, 1):
            lines.append(
                f"[A{index}] {format_ms(int(window['start_ms']), tz_name)} -> {format_ms(int(window['end_ms']), tz_name)} "
                f"score={window.get('max_score')} confidence={window.get('confidence')}"
            )
    else:
        lines.append("windows: none")
    return "\n".join(lines)


def cmd_chart(args: argparse.Namespace) -> None:
    obj = load_json(args.input)
    series_list = collect_series(obj)
    if not series_list:
        die("no normalized series found")
    if args.windows:
        windows_obj = load_json(args.windows)
        windows = windows_obj.get("candidate_windows", []) if isinstance(windows_obj, dict) else []
        meta = windows_obj.get("meta", {}) if isinstance(windows_obj, dict) else {}
    else:
        analysis = analyze_series_payload(obj, args)
        windows = analysis["candidate_windows"]
        meta = analysis["meta"]
    chart_series = choose_chart_series(series_list, args)
    print(render_chart(chart_series, windows, meta, args.width, args.timezone))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    chunk = sub.add_parser("chunk", help="Plan safe query chunks for a time range.")
    chunk.add_argument("--start-ms", required=True, type=int)
    chunk.add_argument("--end-ms", required=True, type=int)
    chunk.add_argument("--interval-ms", required=True, type=int)
    chunk.add_argument("--target-points", type=int, default=120)
    chunk.add_argument("--max-points", type=int, default=180)
    chunk.add_argument("--overlap-points", type=int, default=2)
    chunk.set_defaults(func=cmd_chunk)

    stitch = sub.add_parser("stitch", help="Merge normalized series from chunks.")
    stitch.add_argument("inputs", nargs="*", help="Input JSON files. Reads stdin when omitted.")
    stitch.add_argument("--prefer", choices=["last", "first", "mean"], default="last")
    stitch.set_defaults(func=cmd_stitch)

    detect = sub.add_parser("detect", help="Detect candidate anomaly windows.")
    detect.add_argument("--input", "-i", default="-", help="Normalized JSON input file. Reads stdin by default.")
    detect.add_argument("--metric-regex", default=DEFAULT_LATENCY_RE)
    detect.add_argument("--profile", choices=["auto", "standard"], default="auto")
    detect.add_argument("--baseline-mode", choices=["centered", "past"], default="centered")
    detect.add_argument("--threshold", type=float, default=3.5)
    detect.add_argument("--soft-threshold", type=float, default=2.0)
    detect.add_argument("--window-points", type=int)
    detect.add_argument("--min-baseline-points", type=int)
    detect.add_argument("--min-points", type=int, default=8)
    detect.add_argument("--min-window-points", type=int, default=1)
    detect.add_argument("--merge-gap-points", type=int, default=1)
    detect.add_argument("--guard-points", type=int, default=0)
    detect.add_argument("--min-window-metrics", type=int, default=2)
    detect.add_argument("--cross-metric-bonus", type=float, default=2.0)
    detect.add_argument("--point-count-bonus", type=float, default=0.1)
    detect.add_argument("--min-relative-scale", type=float, default=0.05)
    detect.add_argument("--min-abs-scale", type=float, default=1.0)
    detect.add_argument("--max-candidate-windows", type=int, default=6)
    detect.add_argument("--max-anomaly-ratio", type=float, default=0.25)
    detect.add_argument("--direction", choices=["positive", "negative", "both"], default="positive")
    detect.set_defaults(func=cmd_detect)

    chart = sub.add_parser("chart", help="Render an ASCII trend chart with anomaly markers.")
    chart.add_argument("--input", "-i", default="-", help="Normalized JSON input file. Reads stdin by default.")
    chart.add_argument("--windows", help="Optional detect output JSON. When omitted, chart runs detection first.")
    chart.add_argument("--metric-regex", default=DEFAULT_LATENCY_RE)
    chart.add_argument("--series-regex", default=r"p99|p95|max|avg|duration|latency|response")
    chart.add_argument("--profile", choices=["auto", "standard"], default="auto")
    chart.add_argument("--baseline-mode", choices=["centered", "past"], default="centered")
    chart.add_argument("--threshold", type=float, default=3.5)
    chart.add_argument("--soft-threshold", type=float, default=2.0)
    chart.add_argument("--window-points", type=int)
    chart.add_argument("--min-baseline-points", type=int)
    chart.add_argument("--min-points", type=int, default=8)
    chart.add_argument("--min-window-points", type=int, default=1)
    chart.add_argument("--merge-gap-points", type=int, default=1)
    chart.add_argument("--guard-points", type=int, default=0)
    chart.add_argument("--min-window-metrics", type=int, default=2)
    chart.add_argument("--cross-metric-bonus", type=float, default=2.0)
    chart.add_argument("--point-count-bonus", type=float, default=0.1)
    chart.add_argument("--min-relative-scale", type=float, default=0.05)
    chart.add_argument("--min-abs-scale", type=float, default=1.0)
    chart.add_argument("--max-candidate-windows", type=int, default=6)
    chart.add_argument("--max-anomaly-ratio", type=float, default=0.25)
    chart.add_argument("--direction", choices=["positive", "negative", "both"], default="positive")
    chart.add_argument("--width", type=int, default=72)
    chart.add_argument("--timezone", default="UTC", help="Timezone for chart timestamps, e.g. UTC or Asia/Shanghai.")
    chart.set_defaults(func=cmd_chart)

    similarity = sub.add_parser("similarity", help="Rank series by shape similarity to a target series.")
    similarity.add_argument("--input", "-i", default="-", help="Normalized JSON input file. Reads stdin by default.")
    similarity.add_argument("--target-regex", default=r"p99|percentile.*99|max|avg|duration|latency|response")
    similarity.add_argument("--target-tag", action="append", default=[], help="Target tag filter as key=value. Repeatable.")
    similarity.add_argument("--candidate-regex", default=r"p99|percentile.*99|max|avg|duration|latency|response")
    similarity.add_argument("--candidate-tag", action="append", default=[], help="Candidate tag filter as key=value. Repeatable.")
    similarity.add_argument("--candidate-tag-key", help="Only keep candidate series containing this tag key, e.g. service or resource.")
    similarity.add_argument("--start-ms", type=int, help="Optional inclusive comparison start timestamp.")
    similarity.add_argument("--end-ms", type=int, help="Optional exclusive comparison end timestamp.")
    similarity.add_argument("--min-shared-points", type=int, default=5)
    similarity.add_argument("--top", type=int, default=10)
    similarity.add_argument("--value-weight", type=float, default=0.65)
    similarity.add_argument("--delta-weight", type=float, default=0.35)
    similarity.set_defaults(func=cmd_similarity)

    return parser


def main(argv: Optional[List[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
