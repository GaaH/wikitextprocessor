import unittest
from wikitextprocessor import Wtp
from wikitextprocessor.common import preprocess_text, MAGIC_NOWIKI_CHAR
from wikitextprocessor.dumpparser import DumpParser

class DumpParserTests(unittest.TestCase):

    def test_namespaces(self):
        path = "tests/test-pages-articles.xml.bz2"
        parser = DumpParser(path)
        for page in parser:
            self.assertGreater(len(page.title), 0)
        self.assertEqual(len(parser.ns2key), 46)
