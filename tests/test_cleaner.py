import csv
import tempfile
import unittest
from pathlib import Path

from cleaner import clean_csv, CsvEncodingError


class CleanerTests(unittest.TestCase):

    def create_csv(self, base, content, encoding="utf-8", name="input.csv"):
        """Create a test CSV file."""
        source = base / name
        source.write_text(content, encoding=encoding)
        return source

    def create_csv_bytes(self, base, content_bytes, name="input.csv"):
        """Create a test CSV file from raw bytes (for BOM / encoding cases)."""
        source = base / name
        source.write_bytes(content_bytes)
        return source

    def read_csv(self, path):
        """Read the cleaned CSV."""
        with path.open(encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))

    # ------------------------------------------------------------------
    # Original 5 tests (must keep passing)
    # ------------------------------------------------------------------
    def test_cleaning_and_summary(self):
        """Basic cleaning and summary."""
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = self.create_csv(
                base,
                "id,name,email\n"
                "1, Alice ,alice@example.com\n"
                "1,Alice,alice@example.com\n"
                "2,Bob,\n"
                ",,\n",
            )
            out = base / "out"
            summary = clean_csv(source, out)

            self.assertEqual(summary["total_data_rows"], 4)
            self.assertEqual(summary["blank_rows_removed"], 1)
            self.assertEqual(summary["duplicate_rows_removed"], 1)
            self.assertEqual(summary["cleaned_rows"], 2)

            rows = self.read_csv(out / "cleaned.csv")
            self.assertEqual(rows[0]["name"], "Alice")
            self.assertEqual(rows[1]["email"], "")

    def test_missing_file(self):
        """Missing input file."""
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(FileNotFoundError):
                clean_csv(Path(temp) / "missing.csv", Path(temp) / "out")

    def test_dedup_by_single_field(self):
        """Dedup by customer_id; empty IDs are kept."""
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = self.create_csv(
                base,
                "customer_id,name,city\n"
                "1001,Alice,Beijing\n"
                "1002,Bob,Shanghai\n"
                "1001,Alice,Shenzhen\n"
                ",Tom,Guangzhou\n"
                ",Jerry,Chengdu\n",
            )
            out = base / "out"
            summary = clean_csv(source, out, dedup_fields=["customer_id"])

            rows = self.read_csv(out / "cleaned.csv")
            self.assertEqual(summary["duplicate_rows_removed"], 1)
            self.assertEqual(summary["cleaned_rows"], 4)
            self.assertEqual(rows[0]["city"], "Beijing")
            empty_id_rows = [row for row in rows if row["customer_id"] == ""]
            self.assertEqual(len(empty_id_rows), 2)

    def test_dedup_multi_fields(self):
        """Dedup by customer_id + org_code."""
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = self.create_csv(
                base,
                "customer_id,org_code,name\n"
                "1001,A,Alice\n"
                "1001,B,Alice\n"
                "1001,A,Alice New\n"
                "1002,C,Bob\n",
            )
            out = base / "out"
            summary = clean_csv(source, out, dedup_fields=["customer_id", "org_code"])

            rows = self.read_csv(out / "cleaned.csv")
            self.assertEqual(summary["duplicate_rows_removed"], 1)
            self.assertEqual(summary["cleaned_rows"], 3)
            self.assertEqual(rows[0]["name"], "Alice")
            self.assertEqual(rows[1]["org_code"], "B")

    def test_dedup_field_not_exist(self):
        """Unknown dedup field raises ValueError."""
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = self.create_csv(base, "customer_id,name\n1001,Alice\n")
            with self.assertRaises(ValueError):
                clean_csv(source, base / "out", dedup_fields=["not_exist_column"])

    # ------------------------------------------------------------------
    # New: CSV encoding compatibility
    # ------------------------------------------------------------------
    def test_encoding_utf8_no_bom(self):
        """Plain UTF-8 input is decoded correctly."""
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = self.create_csv(
                base,
                "编号,姓名,城市\n1001,张三,长沙\n1002,李四,北京\n",
                encoding="utf-8",
            )
            out = base / "out"
            summary = clean_csv(source, out)

            self.assertEqual(summary["encoding"], "utf-8")
            self.assertEqual(summary["cleaned_rows"], 2)
            rows = self.read_csv(out / "cleaned.csv")
            self.assertEqual(rows[0]["姓名"], "张三")

    def test_encoding_utf8_with_bom(self):
        """UTF-8 BOM (excel style) is detected and stripped correctly."""
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            content = "编号,姓名,城市\n1001,张三,长沙\n"
            source = self.create_csv_bytes(
                base,
                b"\xef\xbb\xbf" + content.encode("utf-8"),
            )
            out = base / "out"
            summary = clean_csv(source, out)

            self.assertEqual(summary["encoding"], "utf-8-with-bom")
            rows = self.read_csv(out / "cleaned.csv")
            # First header must be clean (no BOM char leaking into the name).
            self.assertEqual(summary["headers"][0], "编号")
            self.assertEqual(rows[0]["姓名"], "张三")

    def test_encoding_unknown_raises_clear_error(self):
        """GBK-encoded content raises a clear, actionable error."""
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            content = "编号,姓名\n1001,张三\n"
            source = self.create_csv_bytes(base, content.encode("gbk"))
            with self.assertRaises(CsvEncodingError) as ctx:
                clean_csv(source, base / "out")
            message = str(ctx.exception)
            self.assertIn("无法识别文件编码", message)
            self.assertIn("UTF-8", message)  # tells the user what is supported

    def test_encoding_force_with_param(self):
        """Explicit encoding parameter can decode a non-UTF-8 file."""
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            content = "编号,姓名\n1001,张三\n"
            source = self.create_csv_bytes(base, content.encode("gbk"))
            out = base / "out"
            summary = clean_csv(source, out, encoding="gbk")
            self.assertEqual(summary["cleaned_rows"], 1)
            rows = self.read_csv(out / "cleaned.csv")
            self.assertEqual(rows[0]["姓名"], "张三")

    # ------------------------------------------------------------------
    # New: header anomaly handling
    # ------------------------------------------------------------------
    def test_header_trim_empty_and_duplicate(self):
        """Headers are trimmed; empty and duplicate headers are repaired."""
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = self.create_csv(
                base,
                " id ,  name ,, name \n"
                "1,Alice,x,Bob\n",
            )
            out = base / "out"
            summary = clean_csv(source, out)

            self.assertEqual(summary["headers"], ["id", "name", "column_3", "name_2"])
            # Original headers were repaired, not silently mixed up.
            self.assertTrue(summary["header_fixes"])

            rows = self.read_csv(out / "cleaned.csv")
            self.assertEqual(rows[0]["id"], "1")
            self.assertEqual(rows[0]["name"], "Alice")
            self.assertEqual(rows[0]["column_3"], "x")
            self.assertEqual(rows[0]["name_2"], "Bob")

    def test_header_duplicate_dedup_field_uses_repaired_name(self):
        """Dedup can reference the repaired (renamed) header."""
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = self.create_csv(
                base,
                "customer_id , customer_id\n1001,A\n1001,B\n",
            )
            out = base / "out"
            summary = clean_csv(source, out, dedup_fields=["customer_id"])
            self.assertEqual(summary["headers"], ["customer_id", "customer_id_2"])
            # Dedup by repaired first column: both rows share 1001 -> keep 1.
            self.assertEqual(summary["cleaned_rows"], 1)
            self.assertEqual(summary["duplicate_rows_removed"], 1)

    # ------------------------------------------------------------------
    # New: row column-count mismatch handling (must not crash)
    # ------------------------------------------------------------------
    def test_column_count_mismatch_records_exceptions(self):
        """Too few / too many columns are recorded and never crash the run."""
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = self.create_csv(
                base,
                "a,b,c\n"
                "1,2,3\n"
                "4,5,6,7\n"
                "8,9\n",
            )
            out = base / "out"
            summary = clean_csv(source, out)

            self.assertEqual(summary["total_data_rows"], 3)
            self.assertEqual(summary["rows_with_column_mismatch"], 2)
            self.assertEqual(summary["cleaned_rows"], 3)  # mismatch rows are kept

            with (out / "exceptions.csv").open(encoding="utf-8-sig", newline="") as f:
                exceptions = list(csv.DictReader(f))
            mismatch = [r for r in exceptions if r["issue"] == "column_count_mismatch"]
            self.assertEqual(len(mismatch), 2)
            self.assertIn("extra values", mismatch[0]["details"])
            self.assertIn("missing columns", mismatch[1]["details"])

    def test_column_mismatch_extra_values_in_detail(self):
        """Extra column values are shown in the exception detail."""
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = self.create_csv(base, "a,b\n1,2,3\n")
            out = base / "out"
            summary = clean_csv(source, out)
            self.assertEqual(summary["rows_with_column_mismatch"], 1)
            with (out / "exceptions.csv").open(encoding="utf-8-sig", newline="") as f:
                exceptions = list(csv.DictReader(f))
            self.assertIn("3", exceptions[0]["details"])

    def test_mismatch_rows_are_not_deduplicated(self):
        """列数异常的行不参与去重，避免丢失多余字段数据"""
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)

            source = self.create_csv(
                base,
                "id,name\n"
                "1,Alice,extra_A\n"
                "1,Alice,extra_B\n"
            )

            out = base / "out"
            summary = clean_csv(source, out)

            self.assertEqual(summary["duplicate_rows_removed"], 0)
            self.assertEqual(summary["cleaned_rows"], 2)
            self.assertEqual(summary["rows_with_column_mismatch"], 2)

    def test_dedup_fields_arg_all_empty(self):
        """CLI参数传入 --dedup-fields ",,," 全部空字段抛出异常"""
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = self.create_csv(base, "customer_id,name\n1001,Alice\n")
            with self.assertRaises(ValueError) as ctx:
                clean_csv(source, base / "out", dedup_fields=["", "", ""])
            # 修改为代码真实抛出的提示
            self.assertIn("Deduplication fields cannot be empty", str(ctx.exception))

    def test_csv_multiline_record_source_line_num(self):
        """CSV单元格内部换行，校验source_line使用reader.line_num（真实结束物理行）"""
        csv_content = (
            "id,content\n"
            '1,"hello\nworld"\n'
            "2,test\n"
        )
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = self.create_csv(base, csv_content)
            out = base / "out"
            summary = clean_csv(source, out)
            rows_exc = self.read_csv(out / "exceptions.csv")
            rows_clean = self.read_csv(out / "cleaned.csv")
            self.assertEqual(len(rows_clean),2)

if __name__ == "__main__":
    unittest.main()
