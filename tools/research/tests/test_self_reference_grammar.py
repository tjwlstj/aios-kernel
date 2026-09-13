"""Exhaust the finite GBNF language independently of the JSON decision parser."""
import itertools
import json
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from self_reference_grammar import DECISION_GRAMMAR, GRAMMAR_ID
from self_reference_contract import parse_decision


def finite_language(grammar):
    """Test-only interpreter of literals, ranges, references and alternatives.

    Interpret the actual GBNF text instead of a hand-written JSON regex.
    Unsupported or recursive syntax fails instead of widening the language.
    """
    rules = {}
    token = re.compile(r'\s*("(?:\\.|[^"\\])*"|\[[0-9]-[0-9]\]|[a-z][a-z0-9-]*|\|)')
    for line in grammar.splitlines():
        name, body = line.split("::=", 1)
        name = name.strip()
        if not re.fullmatch(r"[a-z][a-z0-9-]*", name) or name in rules:
            raise ValueError("grammar-rule")
        parts, offset = [], 0
        while body[offset:].strip():
            match = token.match(body, offset)
            if not match:
                raise ValueError("unsupported-grammar-syntax")
            parts.append(match.group(1))
            offset = match.end()
        rules[name] = parts

    def expand(name, active=()):
        if name in active or name not in rules:
            raise ValueError("recursive-or-unknown-rule")
        alternatives, current = set(), {""}
        for part in rules[name] + ["|"]:
            if part == "|":
                alternatives.update(current)
                current = {""}
                continue
            if part.startswith('"'):
                values = {json.loads(part)}
            elif part.startswith("["):
                values = {str(n) for n in range(int(part[1]), int(part[3]) + 1)}
            else:
                values = expand(part, active + (name,))
            current = {left + right for left in current for right in values}
            if len(current) > 10000:
                raise ValueError("grammar-language-limit")
        return alternatives

    return expand("root")


class StaticGrammarTests(unittest.TestCase):
    def test_exact_504_choices_remain_including_semantic_errors(self):
        choices = finite_language(DECISION_GRAMMAR)
        expected = set()
        for action, prediction, attribution in itertools.product(
                ("SET", "OBSERVE", "WAIT", "FINISH"),
                ("OBSERVED", "APPLIED", "STALE", "DENIED", "UNAVAILABLE", "NOOP"),
                ("SELF", "OTHER", "UNKNOWN")):
            for revision in range(25) if action == "SET" else (None,):
                expected.add(json.dumps({"action": action, "expected_revision": revision,
                    "prediction": prediction, "attribution": attribution}, separators=(",", ":")))
        self.assertEqual(GRAMMAR_ID, "static-decision-v1")
        self.assertEqual(len(choices), 504)
        self.assertEqual(choices, expected)
        for choice in choices:
            parse_decision(choice)
        self.assertIn('{"action":"SET","expected_revision":24,"prediction":"OBSERVED","attribution":"SELF"}',
                      choices)

    def test_malformed_and_extra_syntax_cannot_be_generated(self):
        choices = finite_language(DECISION_GRAMMAR)
        valid = '{"action":"SET","expected_revision":0,"prediction":"APPLIED","attribution":"SELF"}'
        malformed = [valid.replace(':0,', ':' + item + ',') for item in
                     ("-1", "25", "1.0", "true", "00", "null", '"0"')]
        fence = chr(96) * 3
        malformed += [valid.replace('"SET"', '"WAIT"'), valid[:-1] + ',"extra":0}',
            valid[:-1] + ',"action":"SET"}', fence + "json\n" + valid + "\n" + fence,
            " " + valid, valid + "\n", valid.replace(',"prediction":"APPLIED","attribution":"SELF"',
                ',"attribution":"SELF","prediction":"APPLIED"')]
        for output in malformed:
            with self.subTest(output=output):
                self.assertNotIn(output, choices)

    def test_recognizer_rejects_unsupported_or_recursive_syntax(self):
        for grammar in ('root ::= (', 'root ::= root', 'root ::= absent'):
            with self.subTest(grammar=grammar), self.assertRaises(ValueError):
                finite_language(grammar)


if __name__ == "__main__":
    unittest.main()
