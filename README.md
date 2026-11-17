# CSV Cleaner — Python Data Cleaning Tool

A small command-line tool that cleans CSV files and creates a data-quality report.

## Features

- Removes completely blank rows
- Trims whitespace from column names and cell values
- Removes duplicate rows after trimming
- Keeps rows with missing values and flags them for review
- Exports `cleaned.csv`, `exceptions.csv`, and `summary.json`
- Never overwrites the original input file

## Requirements

- Python 3.9+
- No third-party packages required

## Quick start

```bash
python cleaner.py sample_data/customers.csv --output-dir output
```

## Output files

| File | Description |
|---|---|
| `cleaned.csv` | Cleaned data; rows with missing values are retained |
| `exceptions.csv` | Rows/issues that need human review |
| `summary.json` | Processing counts and notes |

## Run tests

```bash
python -m unittest discover -s tests -v
```

## Data handling decisions

This tool does not guess missing values or silently discard incomplete records. It keeps incomplete rows in the cleaned output and records the issue in `exceptions.csv`, so a client can decide how to handle them.

The original source file is never modified.

## Example use case

A client receives customer exports from multiple systems. The files contain extra spaces, duplicate records, blank lines, and missing fields. This script standardizes simple formatting issues and produces a review report before the data is used downstream.
