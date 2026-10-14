import unittest

from zammad_archive import textconv
from zammad_archive.config import DEFAULTS


QUOTES = textconv.compile_markers(DEFAULTS["privacy"]["quote_markers"])
SIGS = textconv.compile_markers(DEFAULTS["privacy"]["signature_markers"])


class HtmlToText(unittest.TestCase):
    def test_blocks_entities_and_scripts(self):
        html = "<div>Hallo&nbsp;Welt</div><p>a &amp; b</p><script>alert(1)</script><ul><li>eins</li><li>zwei</li></ul>"
        self.assertEqual(textconv.html_to_text(html), "Hallo Welt\n\na & b\n\n- eins\n\n- zwei")

    def test_drop_blockquotes(self):
        html = "<p>Antwort</p><blockquote>alte Mail</blockquote>"
        self.assertEqual(textconv.html_to_text(html, drop_blockquotes=True), "Antwort")
        self.assertIn("alte Mail", textconv.html_to_text(html))

    def test_pre_keeps_whitespace(self):
        self.assertIn("a  b\n  c", textconv.html_to_text("<pre>a  b\n  c</pre>"))

    def test_signature_marker(self):
        html = '<p>Text</p><span class="js-signatureMarker"></span><p>Max Muster</p>'
        text = textconv.html_to_text(html)
        self.assertEqual(textconv.cut_signature(text, SIGS), "Text")


class Quotes(unittest.TestCase):
    def test_original_message(self):
        text = "Bitte prüfen, der Server ist down.\n\n-----Original Message-----\nFrom: x\nalt"
        self.assertEqual(textconv.strip_quoted(text, QUOTES), "Bitte prüfen, der Server ist down.")

    def test_outlook_header(self):
        text = "Danke, funktioniert wieder einwandfrei.\n\nVon: Max <m@x.de>\nGesendet: Montag\nBetreff: alt\n\nalt"
        self.assertEqual(textconv.strip_quoted(text, QUOTES), "Danke, funktioniert wieder einwandfrei.")

    def test_wrote_line_and_gt(self):
        text = "Erledigt, bitte nochmal testen.\nOn Mon, 1 Apr 2026 Max wrote:\n> alt"
        self.assertEqual(textconv.strip_quoted(text, QUOTES), "Erledigt, bitte nochmal testen.")

    def test_pure_forward_is_kept(self):
        text = "-----Original Message-----\nFrom: x\nDer eigentliche Inhalt der Mail steht hier."
        self.assertIn("eigentliche Inhalt", textconv.strip_quoted(text, QUOTES))


class Signatures(unittest.TestCase):
    def test_keeps_greeting_line(self):
        text = "Hallo,\nes geht wieder.\n\nMit freundlichen Grüßen\nMax Mustermann\nTel 0123"
        self.assertEqual(textconv.cut_signature(text, SIGS, 1), "Hallo,\nes geht wieder.\n\nMit freundlichen Grüßen")

    def test_dash_dash(self):
        self.assertEqual(textconv.cut_signature("Text\n-- \nName", SIGS, 1), "Text")

    def test_marker_on_first_line_is_ignored(self):
        self.assertEqual(textconv.cut_signature("Grüße aus Köln\nText", SIGS, 1), "Grüße aus Köln\nText")


if __name__ == "__main__":
    unittest.main()
