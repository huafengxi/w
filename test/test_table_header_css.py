"""Run with an existing tinycss2 installation; no browser/layout assertions."""
import unittest
from pathlib import Path

import tinycss2


CSS_PATH = Path(__file__).resolve().parents[1] / "view" / "my.css"


def check_header_rule(css):
    """Require an unpolluted `th` rule with the intended parsed declarations."""
    for rule in tinycss2.parse_stylesheet(css, skip_comments=True, skip_whitespace=True):
        if rule.type != "qualified-rule":
            continue
        selector = [t for t in rule.prelude if t.type not in ("whitespace", "comment")]
        # tinycss2 tokenizes selectors, not their grammar. Exactly one ident
        # 'th' is a valid type selector; e.g. '; th' must NOT qualify.
        if len(selector) != 1 or selector[0].type != "ident" or selector[0].value != "th":
            continue
        declarations = {}
        for decl in tinycss2.parse_declaration_list(
            rule.content, skip_comments=True, skip_whitespace=True
        ):
            if decl.type == "declaration":
                declarations[decl.lower_name] = tinycss2.serialize(decl.value).strip()
        if declarations.get("background") == "#f3f5f7" and declarations.get("color") == "#222":
            return
    raise AssertionError("No valid standalone th rule with background #f3f5f7 and color #222")


class TableHeaderCssTest(unittest.TestCase):
    def test_live_stylesheet(self):
        check_header_rule(CSS_PATH.read_text())

    def test_b1_pollution_is_rejected(self):
        # Isolated in-memory fixture: never write the broken CSS to production.
        clean = "h1,h2,h3,h4,h5 {}\nth { background: #f3f5f7; color: #222; }"
        check_header_rule(clean)
        polluted = clean.replace("{}", "{};", 1)
        with self.assertRaises(AssertionError):
            check_header_rule(polluted)


if __name__ == "__main__":
    unittest.main(verbosity=2)
