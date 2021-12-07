# WikiMedia dump file parser for Wiktionary, Wikipedia, and other projects.
#
# Copyright (c) 2018-2021 Tatu Ylonen.  See file LICENSE and https://ylonen.org

import re
import sys
import os
import html
import traceback
import subprocess
from xml.etree import ElementTree as ET

# These XML tags are ignored when parsing.
ignore_xml_tags = {
    "sha1", "comment", "username", "timestamp",
    "sitename", "dbname", "base", "generator", "case",
    "restrictions", "contributor", "username",
    "minor", "parentid", "revision",
    "siteinfo", "mediawiki", "namespaces",
    "id", "revision", "format",
}

# Other tags are ignored inside these tags.
xml_stack_ignore = ("contributor",)


def open_dump(path):
    _, ext = os.path.splitext(path)
    if ext == ".bz2":
        import bz2
        return bz2.open(path, "rb")
    elif ext == ".gz":
        import gzip
        return gzip.open(path, "rb")
    else:
        return open(path, "rb", buffering=(256 * 1024))

class WikiNamespace:
    def __init__(self, ns, key):
        self.ns = ns
        self.key = key


class WikiPage:
    def __init__(self, title, text, model, ns, redirect=None):
        self.title = title
        self.text = text
        self.model = model
        self.ns = ns
        self.redirect = redirect

class DumpParser(object):
    """This class is used for XML parsing the MediaWiki dump file."""
    
    TAG_RE = re.compile(
    rb"""(?s)<!--[^\0]*?-->|"""
    rb"""<([^>\s]+)"""
    rb"""(\s+[^"'>/=\s]+\b(\s*=\s*("[^"]*"|'[^']*'|[^ \t\n"'`=<>]*))?)*?"""
    rb"""\s*(/\s*)?>""")

    ARG_RE = re.compile(
        rb"""([^"'>/=\s]+)(\s*=\s*("[^"]*"|'[^']*'|[^ \t\n"'`=<>]*))?"""
    )

    __slots__ = (
        "tag",
        "stack",
        "stack_ignore",
        "dump_path",
        "text",
        "title",
        "redirect",
        "model",
        "aborted",
        "data",
        "args",
        "buf",
        "ofs",
        "nskey",
        "key2ns",
        "ns2key",
        "mediawiki_xmlns",
        "xmlns",
    )

    def __init__(self, dump_path):
        self.dump_path = dump_path
        
        self.tag = None
        self.stack = []
        self.stack_ignore = False
        self.text = None
        self.title = None
        self.redirect = None
        self.model = None
        self.aborted = False
        self.data = []
        self.args = b""
        self.key2ns = {}
        self.ns2key = {}
        self.mediawiki_xmlns = "{http://www.mediawiki.org/xml/export-0.10/}"
        self.xmlns = {"": "http://www.mediawiki.org/xml/export-0.10/"} 
        
    def __iter__(self):
        self.buf = b""
        self.ofs = 0

        def handle_start(tag, args):
            """This is called whenever an XML start tag is encountered."""
            assert isinstance(tag, str)
            assert isinstance(args, bytes)
            self.args = args
            self.tag = tag
            self.stack.append(tag)
            self.data = []
            if tag == "page":
                self.text = None
                self.title = None
                self.redirect = None
                self.model = None
                self.nskey = None
            elif tag in xml_stack_ignore:
                self.stack_ignore = True

        def parse_attrs(args):
            attrs = {}
            print(args, file=sys.stderr)
            for m in re.finditer(DumpParser.ARG_RE, args):
                print(m, file=sys.stderr)
                name = m.group(1).decode("utf-8")
                if m.group(2):
                    value = m.group(3).decode("utf-8")
                else:
                    value = ""
                if value.startswith("'") or value.startswith('"'):
                    value = value[1:-1]
                attrs[name] = value
            return attrs

        def handle_end(tag):
            """This function is called whenever an XML end tag is encountered."""
            ptag = self.stack.pop()
            if ptag in xml_stack_ignore:
                self.stack_ignore = False
            if tag in ignore_xml_tags or self.stack_ignore:
                return None

            data = b"".join(self.data).decode("utf-8")
            self.data = []

            if tag == "title":
                self.title = data
            elif tag == "text":
                self.text = data
            elif tag == "redirect":
                attrs = parse_attrs(self.args)
                self.redirect = attrs.get("title")
            elif tag == "page":
                ns = WikiNamespace(self.key2ns[self.nskey], self.nskey)
                if self.redirect:
                    # return "redirect", dp.title, dp.redirect
                    return WikiPage(self.title, None, None, ns, redirect=self.redirect)
                # return dp.model, dp.title, dp.text
                return WikiPage(self.title, self.text, self.model, ns)
            elif tag == "model":
                self.model = data
            elif tag == "ns":
                self.nskey = data
            elif tag == "namespace":
                attrs = parse_attrs(self.args)
                print(tag, file=sys.stderr)
                print(data, file=sys.stderr)
                print(attrs, file=sys.stderr)
                self.key2ns[attrs["key"]] = data
            else:
                attrs = parse_attrs(self.args)
                print("UNSUPPORTED", tag, len(data), attrs)
            return None
        
        def add_namespace(elem):
            # Text can be empty if tag is auto-closing
            ns = elem.text or ""
            key = elem.attrib["key"]
            self.key2ns[key] = ns
            self.ns2key[ns] = key

        def make_wikipage(elem):
            # id = elem.findtext("id", namespaces=self.xmlns)
            title = elem.findtext("title", namespaces=self.xmlns)
            text = elem.findtext("./revision/text", namespaces=self.xmlns)
            key = elem.findtext("ns", namespaces=self.xmlns)
            ns = self.key2ns[key]
            model = elem.findtext("./revision/model", namespaces=self.xmlns)
            
            redirect = None
            redirect_elem = elem.find("redirect", namespaces=self.xmlns)
            if redirect_elem is not None:
                redirect = redirect_elem.attrib["title"]
                model = "redirect"
            
            ns_obj = WikiNamespace(ns, key)
            page = WikiPage(title, text, model, ns_obj, redirect=redirect)
            return page

        tag2fn = {
            f"{self.mediawiki_xmlns}namespace": add_namespace,
            f"{self.mediawiki_xmlns}page": make_wikipage,
        }

        try:
            with open_dump(self.dump_path) as f:
                for (ev, elem) in ET.iterparse(f, events=("start", "end")):
                    if ev == "start":
                        pass
                    elif ev == "end":
                        fn = tag2fn.get(elem.tag, None)
                        if fn is not None:
                            ret = fn(elem)
                            if ret is not None:
                                yield ret

                                        
                # while not self.aborted:
                #     more_data = f.read(64 * 1024)
                #     if not more_data:
                #         rest = self.buf[self.ofs:]
                #         self.data.append(rest)
                #         break
                #     self.buf = self.buf[self.ofs:] + more_data
                #     self.ofs = 0
                #     for m in re.finditer(DumpParser.TAG_RE, self.buf):
                #         before = self.buf[self.ofs:m.start()]
                #         if before:
                #             self.data.append(before)
                #         self.ofs = m.end()
                #         tag = m.group(1)
                #         if not tag:
                #             continue
                #         tag = tag.lower().decode("utf-8").strip()
                #         args = m.group(2) or b""
                #         close = m.group(5)
                #         if tag.startswith("/"):
                #             tag = tag[1:]
                #             art = handle_end(tag)
                #             if art:
                #                 yield art
                #         elif close:
                #             handle_start(tag, args)
                #             art = handle_end(tag)
                #             if art:
                #                 yield art
                #         else:
                #             handle_start(tag, args)
        except Exception as e:
            print("GOT EXCEPTION", str(e))
            traceback.print_exc()
            raise


def process_input(path, page_cb):
    """Processes the entire input once, calling page_cb for each chunk.
    A chunk is a list of data, where ``data`` is a dict
    containing at least "title" and "text" keys.  This returns a list
    of the values returned by ``page_cb`` in arbitrary order.  Each return
    value must be json-serializable."""
    assert isinstance(path, str)
    assert callable(page_cb)

    # Create an iterator that produces chunks of articles to process.
    parser = DumpParser(path)
    lst = []
    for page in parser:
        # title = html.unescape(page.title)
        # text = html.unescape(page.text)
        # ret = page_cb(page.model, title, text)
        ret = page_cb(page)
        if ret is not None:
            lst.append(ret)

    return lst


def process_dump(ctx, path):
    """Parses a WikiMedia dump file ``path`` (which should point to a
    "<project>-<date>-pages-articles.xml.bz2" file. This implements
    the first phase of processing a dump - copying it to a temporary
    file with some preprocessing. The Wtp.reprocess() must then be
    called to actually process the data."""
    assert isinstance(path, str)

    def phase1_page_handler(page):
        """Handler for pages in Phase 1, for extracting special pages and saving
        data about all pages."""
        ctx.add_page(page)

    # Run Phase 1 in a single thread; this mostly just extracts pages into
    # a temporary file.
    process_input(path, phase1_page_handler)

    # Analyze which templates should be expanded before parsing
    if not ctx.quiet:
        print("Analyzing which templates should be expanded before parsing")
        sys.stdout.flush()
    ctx.analyze_templates()

# XXX parse <namespaces> and use that in both Python and Lua code

# XXX parse <case> to determine whether titles are case-sensitive
