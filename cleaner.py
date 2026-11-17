#!/usr/bin/env python3
"""Clean a CSV file without modifying the original.

Features:
- Encoding support: auto-detects UTF-8 and UTF-8 BOM (common Excel format);
  raises a clear CsvEncodingError when the encoding cannot be recognized.
- Header repair: trims whitespace, names empty headers as column_N,
  renames duplicate headers as name_2, name_3, ... to avoid field mix-ups.
- Row handling: detects column-count mismatches (too few / too many columns),
  keeps them in exceptions.csv, and never crashes on them.
- Removes completely blank rows
- Deduplication: default full-row dedup, or specify fields e.g. --dedup-fields customer_id
- Rows with empty dedup-key values are kept and not treated as duplicates
- Keeps rows with missing values and records them in exceptions.csv
- Writes cleaned.csv, exceptions.csv and summary.json
"""
from __future__ import annotations
import argparse
import csv
import io
import json
from pathlib import Path
from typing import Any

BOM = b"\xef\xbb\xbf"


class CsvEncodingError(ValueError):
    """Raised when the input file encoding is not supported or not recognized."""


def _read_text(input_path: Path, user_encoding: str | None = None) -> tuple[str, str]:
    """Decode the CSV file. Returns (text, encoding_label).

    Supports UTF-8 and UTF-8 BOM automatically. When the content cannot be
    decoded, raises CsvEncodingError with a clear, actionable message.
    """
    raw = input_path.read_bytes()

    if user_encoding:
        try:
            return raw.decode(user_encoding), user_encoding
        except (UnicodeDecodeError, LookupError) as exc:
            raise CsvEncodingError(
                f"无法按指定编码 '{user_encoding}' 解码文件: {exc}"
            ) from exc

    if raw.startswith(BOM):
        try:
            return raw.decode("utf-8-sig"), "utf-8-with-bom"
        except UnicodeDecodeError as exc:
            raise CsvEncodingError(
                "文件带有 UTF-8 BOM 标记，但内容无法按 UTF-8 解码: "
                f"{exc}. 请重新另存为 UTF-8 编码。"
            ) from exc

    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass

    # Try to give a helpful hint when another common encoding is detected.
    hint = ""
    for enc in ("gb18030", "big5", "utf-16"):
        try:
            raw.decode(enc)
            hint = f"文件疑似 {enc.upper()} 编码。"
            break
        except UnicodeDecodeError:
            continue

    message = (
        "无法识别文件编码：本工具仅支持 UTF-8 或 UTF-8 BOM（Excel 常见格式）。"
    )
    if hint:
        message += hint + " 请用 Excel/文本编辑器另存为“CSV UTF-8”，或通过 --encoding 参数显式指定编码。"
    else:
        message += " 请将文件另存为 UTF-8 编码，或通过 --encoding 参数显式指定编码。"
    raise CsvEncodingError(message)


def _repair_headers(raw_headers: list[str | None]) -> tuple[list[str], list[dict[str, Any]]]:
    """Normalize headers: trim whitespace, fill empty names, de-duplicate.

    Returns (headers, header_fixes).
    """
    headers: list[str] = []
    fixes: list[dict[str, Any]] = []
    used: set[str] = set()
    counts: dict[str, int] = {}

    for idx, raw in enumerate(raw_headers):
        original = "" if raw is None else str(raw)
        base = original.strip()
        reason: str | None = None

        if base == "":
            base = f"column_{idx + 1}"
            reason = "empty_header_renamed"

        candidate = base
        if candidate in used:
            counts[base] = counts.get(base, 1) + 1
            candidate = f"{base}_{counts[base]}"
            while candidate in used:
                counts[base] += 1
                candidate = f"{base}_{counts[base]}"
            reason = "duplicate_header_renamed"
        else:
            counts.setdefault(base, 1)

        if reason is None and original != candidate:
            reason = "whitespace_trimmed"

        if reason is not None:
            fixes.append({
                "position": idx + 1,
                "original": original,
                "resolved": candidate,
                "reason": reason,
            })

        used.add(candidate)
        headers.append(candidate)

    return headers, fixes


def clean_csv(
    input_path: Path,
    output_dir: Path,
    dedup_fields: list[str] | None = None,
    encoding: str | None = None,
) -> dict[str, Any]:
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Original input file is only read, never modified.
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    if dedup_fields:
        dedup_fields = [field.strip() for field in dedup_fields]
        if not all(dedup_fields):
            raise ValueError("Deduplication fields cannot be empty.")

    text, encoding_label = _read_text(input_path, user_encoding=encoding)

    try:
        reader = csv.reader(io.StringIO(text, newline=""))
        try:
            raw_headers = next(reader)
        except StopIteration:
            raise ValueError("CSV file is empty or has no header row.")
    except csv.Error as exc:
        raise ValueError(f"CSV parse error while reading header: {exc}") from exc

    headers, header_fixes = _repair_headers(raw_headers)
    if not headers:
        raise ValueError("CSV file is empty or has no header row.")

    # Validate dedup fields against the repaired headers.
    if dedup_fields:
        for f in dedup_fields:
            if f not in headers:
                raise ValueError(f"Deduplication field '{f}' not found in CSV headers.")

    cleaned_rows: list[dict[str, str]] = []
    exception_rows: list[dict[str, str]] = []
    seen: set[tuple[str, ...]] = set()

    blank_rows = 0
    duplicate_rows = 0
    missing_value_rows = 0
    column_mismatch_rows = 0
    total_data_rows = 0

    for line_number, values in enumerate(reader, start=2):
        total_data_rows += 1
        values = list(values)

        # Completely blank row is dropped.
        if all((v or "").strip() == "" for v in values):
            blank_rows += 1
            continue

        expected = len(headers)
        actual = len(values)
        column_mismatch = actual != expected
        extra_values = values[expected:] if actual > expected else []

        row: dict[str, str] = {}
        for i, header in enumerate(headers):
            row[header] = values[i].strip() if i < actual else ""

        if column_mismatch:
            column_mismatch_rows += 1
            if actual < expected:
                detail = f"expected {expected} columns, got {actual} (missing columns filled empty)"
            else:
                detail = f"expected {expected} columns, got {actual} (extra values: {extra_values})"
            exception_rows.append({
                "source_line": str(line_number),
                "issue": "column_count_mismatch",
                "details": detail,
                **row,
            })

        # Build dedup key.
        skip_dedup_check = column_mismatch

        if not skip_dedup_check:
            if dedup_fields:
                dedup_key = tuple(row[f] for f in dedup_fields)

                # Empty dedup-key values are not deduplicated.
                if any(v == "" for v in dedup_key):
                    skip_dedup_check = True
            else:
                dedup_key = tuple(row[h] for h in headers)



        if not skip_dedup_check:
            if dedup_key in seen:
                duplicate_rows += 1
                continue
            seen.add(dedup_key)

        missing_columns = [header for header, value in row.items() if value == ""]
        if missing_columns:
            missing_value_rows += 1
            exception_rows.append({
                "source_line": str(line_number),
                "issue": "missing_values",
                "details": "Missing values in: " + ", ".join(missing_columns),
                **row,
            })

        cleaned_rows.append(row)

    cleaned_path = output_dir / "cleaned.csv"
    with cleaned_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        writer.writerows(cleaned_rows)

    exception_fields = ["source_line", "issue", "details", *headers]
    exceptions_path = output_dir / "exceptions.csv"
    with exceptions_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=exception_fields)
        writer.writeheader()
        writer.writerows(exception_rows)

    dedup_mode = "full_row" if not dedup_fields else f"fields:{','.join(dedup_fields)}"
    summary = {
        "input_file": input_path.name,
        "encoding": encoding_label,
        "headers": headers,
        "header_fixes": header_fixes,
        "total_data_rows": total_data_rows,
        "blank_rows_removed": blank_rows,
        "duplicate_rows_removed": duplicate_rows,
        "dedup_mode": dedup_mode,
        "dedup_fields": dedup_fields,
        "rows_with_column_mismatch": column_mismatch_rows,
        "rows_with_missing_values": missing_value_rows,
        "cleaned_rows": len(cleaned_rows),
        "exception_records": len(exception_rows),
        "outputs": ["cleaned.csv", "exceptions.csv", "summary.json"],
        "notes": [
            "The original input file was not modified.",
            "UTF-8 and UTF-8 BOM are auto-detected. Other encodings can be specified explicitly; unsupported or incorrect encodings raise a clear error.",
            "Headers are trimmed; empty/duplicate headers are renamed to avoid field mix-ups.",
            "Column-count mismatches are recorded in exceptions.csv and never crash the run.",
            "Rows with missing values are retained in cleaned.csv and flagged in exceptions.csv.",
            "Duplicate detection is performed after trimming whitespace.",
            "Rows with empty dedup-key fields are kept and NOT treated as duplicates.",
        ],
    }
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean a CSV file and generate a quality report.")
    parser.add_argument("input", help="Path to the source CSV file")
    parser.add_argument("-o", "--output-dir", default="output", help="Output directory (default: output)")
    parser.add_argument(
        "--dedup-fields",
        help="Fields for deduplication, comma-separated. Example: customer_id or customer_id,org_code. Default: full-row dedup",
        default=None,
    )
    parser.add_argument(
        "--encoding",
        help="Force an input encoding (e.g. utf-8, utf-8-sig, gbk). Default: auto-detect UTF-8 / UTF-8 BOM.",
        default=None,
    )
    args = parser.parse_args()

    dedup_fields_list: list[str] | None = None
    if args.dedup_fields:
        dedup_fields_list = [x.strip() for x in args.dedup_fields.split(",") if x.strip()]

    try:
        summary = clean_csv(
            Path(args.input),
            Path(args.output_dir),
            dedup_fields=dedup_fields_list,
            encoding=args.encoding,
        )
    except (OSError, ValueError, csv.Error) as exc:
        parser.exit(1, f"Error: {exc}\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
