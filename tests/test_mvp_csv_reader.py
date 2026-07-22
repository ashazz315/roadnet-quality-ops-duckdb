from __future__ import annotations

import unittest

from quality_mvp.csv_reader import CsvReadError, read_csv_bytes


class CsvReaderTests(unittest.TestCase):
    def test_reads_utf8_csv(self) -> None:
        frame = read_csv_bytes("record_id,longitude,latitude\nA1,116.4,39.9\n".encode("utf-8"))
        self.assertEqual(
            frame.to_dict("records"),
            [{"record_id": "A1", "longitude": "116.4", "latitude": "39.9"}],
        )

    def test_reads_gb18030_csv(self) -> None:
        content = "record_id,longitude,latitude,description\nA1,116.4,39.9,道路缺失\n".encode("gb18030")
        frame = read_csv_bytes(content)
        self.assertEqual(frame.loc[0, "description"], "道路缺失")

    def test_rejects_empty_csv(self) -> None:
        with self.assertRaisesRegex(CsvReadError, "为空"):
            read_csv_bytes(b"")


if __name__ == "__main__":
    unittest.main()
