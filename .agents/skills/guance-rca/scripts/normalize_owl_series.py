#!/usr/bin/env python3
"""Normalize owl.data.query-style payloads into a common time-series JSON shape."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


TIME_KEYS = ("ts", "timestamp", "time", "_time", "date", "datetime", "start_time")
VALUE_KEYS = ("value", "v", "y", "avg", "max", "p75", "p90", "p95", "p99", "duration")
SKIP_NUMERIC_RE = re.compile(r"(?:^|_)(?:id|trace|span|parent|pid|port|code|status_code)$", re.I)
TAGGED_FIELD_RE = re.compile(r"^(?P<name>.+?)(?P<tags>\{.*\})$")


def die(message: str, code: int = 2) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


def read_text(path: Optional[str]) -> str:
    if path and path != "-":
        return Path(path).read_text(encoding="utf-8")
    return sys.stdin.read()


def load_payload(path: Optional[str]) -> Tuple[Any, str]:
    text = read_text(path).strip()
    source = path or "stdin"
    if not text:
        die("empty input")

    looks_like_json = text.startswith("{") or text.startswith("[")
    candidate_path = Path(text) if not looks_like_json else None
    if candidate_path is not None:
        try:
            is_file = "\n" not in text and candidate_path.exists() and candidate_path.is_file()
        except OSError:
            is_file = False
        if is_file:
            source = str(candidate_path)
            text = candidate_path.read_text(encoding="utf-8").strip()

    if source == (path or "stdin"):
        match = re.search(r"(/[^\s]+\.json)\b", text)
        if match:
            file_path = Path(match.group(1))
            if file_path.exists() and file_path.is_file():
                source = str(file_path)
                text = file_path.read_text(encoding="utf-8").strip()

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        die(f"input is not JSON and no readable JSON file path was found: {exc}")
    data_path = owl_data_file_path(payload)
    if data_path:
        source = data_path
        try:
            return json.loads(Path(data_path).read_text(encoding="utf-8").strip()), source
        except json.JSONDecodeError as exc:
            die(f"owl data file is not JSON: {data_path}: {exc}")
    return payload, source


def owl_data_file_path(payload: Any) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    file_info = payload.get("file")
    if not isinstance(file_info, dict):
        return None
    path = file_info.get("absolutePath") or file_info.get("path")
    if not isinstance(path, str) or not path:
        return None
    candidate = Path(path)
    if candidate.exists() and candidate.is_file():
        return str(candidate)
    return None


def dump_json(obj: Any) -> None:
    json.dump(obj, sys.stdout, ensure_ascii=False, indent=2, sort_keys=False)
    sys.stdout.write("\n")


def as_number(value: Any) -> Optional[float]:
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


def parse_ts(value: Any) -> Optional[int]:
    numeric = as_number(value)
    if numeric is not None:
        if numeric < 10_000_000_000:
            numeric *= 1000
        return int(numeric)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def clean_tag_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    if not text or len(text) > 256:
        return None
    return text


def normalize_point(raw: Any) -> Optional[Dict[str, float]]:
    if isinstance(raw, dict):
        time_value = first_present(raw, TIME_KEYS)
        value = first_present(raw, VALUE_KEYS)
    elif isinstance(raw, (list, tuple)) and len(raw) >= 2:
        first = as_number(raw[0])
        second = as_number(raw[1])
        if first is None or second is None:
            return None
        if first > 10_000_000_000:
            time_value, value = first, second
        elif second > 10_000_000_000:
            time_value, value = second, first
        else:
            time_value, value = first, second
    else:
        return None
    ts = parse_ts(time_value)
    num = as_number(value)
    if ts is None or num is None:
        return None
    return {"ts": ts, "value": num}


def first_present(row: Dict[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in row:
            return row[key]
    lowered = {str(key).lower(): key for key in row}
    for key in keys:
        original = lowered.get(key.lower())
        if original is not None:
            return row[original]
    return None


def column_name(column: Any) -> str:
    if isinstance(column, dict):
        for key in ("name", "key", "field", "title"):
            if key in column:
                return str(column[key])
    return str(column)


def rows_from_columns(obj: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    columns_raw = obj.get("columns")
    rows_raw = obj.get("rows", obj.get("values", obj.get("data")))
    if not isinstance(columns_raw, list) or not isinstance(rows_raw, list):
        return None
    columns = [column_name(column) for column in columns_raw]
    rows: List[Dict[str, Any]] = []
    for row in rows_raw:
        if isinstance(row, dict):
            rows.append(row)
        elif isinstance(row, list):
            rows.append({columns[index]: value for index, value in enumerate(row) if index < len(columns)})
    return rows


def normalize_native_series(obj: Dict[str, Any]) -> List[Dict[str, Any]]:
    points_raw = obj.get("points", obj.get("values"))
    if not isinstance(points_raw, list):
        return []
    points = [point for point in (normalize_point(item) for item in points_raw) if point]
    if not points:
        return []
    tags = obj.get("tags") if isinstance(obj.get("tags"), dict) else {}
    name = obj.get("name", obj.get("metric", obj.get("field", "value")))
    return [
        {
            "name": str(name),
            "tags": {str(key): str(value) for key, value in tags.items()},
            "points": sorted(points, key=lambda item: item["ts"]),
        }
    ]


def find_time_key(row: Dict[str, Any], explicit: Optional[str]) -> Optional[str]:
    if explicit:
        return explicit if explicit in row else None
    for key in TIME_KEYS:
        for row_key in row:
            if str(row_key).lower() == key.lower():
                return str(row_key)
    return None


def choose_value_fields(row: Dict[str, Any], args: argparse.Namespace, time_key: str) -> List[str]:
    if args.value_field:
        return [field for field in args.value_field if field in row]
    fields: List[str] = []
    for key, value in row.items():
        key_text = str(key)
        if key_text == time_key:
            continue
        if key_text in args.tag_field:
            continue
        if SKIP_NUMERIC_RE.search(key_text):
            continue
        if as_number(value) is not None:
            fields.append(key_text)
    return fields


def choose_tags(row: Dict[str, Any], value_field: str, time_key: str, args: argparse.Namespace) -> Dict[str, str]:
    tags: Dict[str, str] = {}
    tag_fields = args.tag_field
    if tag_fields:
        candidates = tag_fields
    else:
        candidates = [
            str(key)
            for key, value in row.items()
            if str(key) not in {value_field, time_key} and as_number(value) is None
        ]
    for key in candidates:
        if key in row:
            text = clean_tag_value(row[key])
            if text is not None:
                tags[key] = text
    return tags


def parse_tagged_value_field(value_field: str) -> Tuple[str, Dict[str, str]]:
    match = TAGGED_FIELD_RE.match(value_field)
    if not match:
        return value_field, {}
    raw_tags = match.group("tags")
    try:
        parsed = json.loads(raw_tags)
    except json.JSONDecodeError:
        return value_field, {}
    if not isinstance(parsed, dict):
        return value_field, {}
    tags = {
        str(key): str(value)
        for key, value in parsed.items()
        if clean_tag_value(value) is not None
    }
    return match.group("name"), tags


def normalize_rows(rows: List[Dict[str, Any]], args: argparse.Namespace) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}

    for row in rows:
        if not isinstance(row, dict):
            continue
        time_key = find_time_key(row, args.time_field)
        if not time_key:
            continue
        ts = parse_ts(row.get(time_key))
        if ts is None:
            continue
        value_fields = choose_value_fields(row, args, time_key)
        for value_field in value_fields:
            value = as_number(row.get(value_field))
            if value is None:
                continue
            parsed_name, embedded_tags = parse_tagged_value_field(value_field)
            name = args.series_name or parsed_name
            tags = choose_tags(row, value_field, time_key, args)
            tags.update(embedded_tags)
            key = json.dumps({"name": name, "tags": sorted(tags.items())}, ensure_ascii=False, sort_keys=True)
            if key not in grouped:
                grouped[key] = {"name": name, "tags": tags, "points": []}
            grouped[key]["points"].append({"ts": ts, "value": value})

    for series in grouped.values():
        by_ts = {point["ts"]: point for point in series["points"]}
        series["points"] = [by_ts[ts] for ts in sorted(by_ts)]
    return list(grouped.values())


def normalize_any(obj: Any, args: argparse.Namespace) -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("success") is False:
                die(f"owl payload reports failure: {value.get('error') or value.get('message') or value}")
            native = normalize_native_series(value)
            if native:
                found.extend(native)
                return
            rows = rows_from_columns(value)
            if rows:
                found.extend(normalize_rows(rows, args))
                return
            for key in ("series", "data", "result", "results", "records", "items", "values", "rows", "content", "payload"):
                child = value.get(key)
                if child is not None:
                    visit(child)
        elif isinstance(value, list):
            if value and all(isinstance(item, dict) for item in value):
                row_series = normalize_rows(value, args)
                if row_series:
                    found.extend(row_series)
                    return
            for item in value:
                visit(item)

    visit(obj)
    return found


def compact_sample(value: Any) -> Any:
    if isinstance(value, (str, int, float)) or value is None or isinstance(value, bool):
        text = value if not isinstance(value, str) else value[:120]
        return text
    return None


def inspect_shape(obj: Any, max_depth: int = 5, max_items: int = 8) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    def visit(value: Any, path: str, depth: int) -> None:
        if depth > max_depth:
            rows.append({"path": path, "type": "max_depth", "note": "depth limit reached"})
            return
        if isinstance(value, dict):
            keys = [str(key) for key in value.keys()]
            entry: Dict[str, Any] = {
                "path": path,
                "type": "object",
                "key_count": len(keys),
                "keys": keys[:max_items],
            }
            if "columns" in value and any(key in value for key in ("rows", "values", "data")):
                entry["normalization_hint"] = "table_candidate"
            if any(key in value for key in ("points", "values")) and any(key in value for key in ("name", "metric", "field", "tags")):
                entry["normalization_hint"] = "series_candidate"
            rows.append(entry)
            for key in list(value.keys())[:max_items]:
                child_path = f"{path}.{key}" if path else str(key)
                visit(value[key], child_path, depth + 1)
        elif isinstance(value, list):
            entry = {"path": path, "type": "array", "length": len(value)}
            if value:
                entry["first_item_type"] = type(value[0]).__name__
            rows.append(entry)
            for index, item in enumerate(value[:max_items]):
                visit(item, f"{path}[{index}]", depth + 1)
        else:
            rows.append({"path": path, "type": type(value).__name__, "sample": compact_sample(value)})

    visit(obj, "$", 0)
    return rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", "-i", default="-", help="JSON input, owl output text, or a file path. Reads stdin by default.")
    parser.add_argument("--time-field", help="Explicit time field for row-shaped results.")
    parser.add_argument("--value-field", action="append", default=[], help="Numeric value field to keep. Repeatable.")
    parser.add_argument("--tag-field", action="append", default=[], help="Tag field to keep. Repeatable.")
    parser.add_argument("--series-name", help="Override output series name for row-shaped results.")
    parser.add_argument("--inspect-shape", action="store_true", help="Print a structural summary instead of normalizing.")
    parser.add_argument("--inspect-max-depth", type=int, default=5)
    parser.add_argument("--inspect-max-items", type=int, default=8)
    parser.add_argument("--strict", action="store_true", help="Exit nonzero when no series can be produced.")
    return parser


def main(argv: Optional[List[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    payload, source = load_payload(args.input)
    if args.inspect_shape:
        dump_json({"meta": {"source": source}, "shape": inspect_shape(payload, args.inspect_max_depth, args.inspect_max_items)})
        return
    series = normalize_any(payload, args)
    if args.strict and not series:
        die("no time series could be normalized")
    dump_json({"series": series, "meta": {"source": source, "series_count": len(series)}})


if __name__ == "__main__":
    main()
