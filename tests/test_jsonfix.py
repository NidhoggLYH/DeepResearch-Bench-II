import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_evaluation_jsonfix as fixed


class JsonFixTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.previous_audit = fixed.AUDIT_DIR
        fixed.AUDIT_DIR = Path(self.temporary.name)

    def tearDown(self):
        fixed.AUDIT_DIR = self.previous_audit
        self.temporary.cleanup()

    def test_valid_compact_response_untouched(self):
        expected = {"results": [{"rubric_item": "条目", "score": 1,
                                 "reason": "符合", "evidence": '原文 "quote" \\delta\n下一行'}]}
        actual, ok = fixed.parse_model_text(json.dumps(expected, ensure_ascii=False))
        self.assertTrue(ok)
        self.assertEqual(actual, expected)
        self.assertEqual(list(fixed.AUDIT_DIR.iterdir()), [])

    def test_latex_escape_repair_and_audit(self):
        raw = r'```json {"evidence":"$\delta \in [0,1]$"} ```'
        parsed, ok = fixed.parse_model_text(raw)
        self.assertTrue(ok)
        self.assertEqual(parsed["evidence"], r"$\delta \in [0,1]$")
        directory = next(fixed.AUDIT_DIR.iterdir())
        self.assertEqual((directory / "response.txt").read_text(), raw)
        self.assertEqual(len(json.loads((directory / "audit.json").read_text())["changes"]), 2)

    def test_legal_escapes_and_quotes_preserved(self):
        raw = r'{"value":"quote: \"x\"; slash: \\delta; tab: \t; formfeed: \f; unicode: \u03b4"}'
        repaired, changes = fixed.repair_invalid_escapes(raw)
        self.assertEqual(repaired, raw)
        self.assertEqual(changes, [])
        self.assertEqual(fixed.parse_model_text(raw), (json.loads(raw), True))

    def test_backslash_outside_string_not_repaired(self):
        raw = r'{"x": \delta}'
        self.assertEqual(fixed.repair_invalid_escapes(raw), (raw, []))
        self.assertEqual(fixed.parse_model_text(raw), (None, False))

    def test_missing_delimiter_and_bad_unicode_rejected(self):
        for raw in [r'{"x": "a" "y":1}', r'{"x":"\uZZZZ"}']:
            with self.subTest(raw=raw):
                self.assertEqual(fixed.parse_model_text(raw), (None, False))

    def test_full_rubric_and_score_validation(self):
        row = {"rubric_item": "A", "score": 1, "reason": "符合", "evidence": "原文"}
        self.assertTrue(fixed.validate_batch_result(["A"], {"results": [row]}))
        self.assertFalse(fixed.validate_batch_result(["A", "B"], {"results": [row, row]}))
        for invalid in [True, "1", 2, None]:
            self.assertFalse(fixed.validate_batch_result(["A"], {"results": [{**row, "score": invalid}]}))


if __name__ == "__main__":
    unittest.main()
