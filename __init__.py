from __future__ import annotations

import collections
import html.parser
import json
import sys
import traceback
import typing
import urllib.parse

class ParseError(Exception):
    pass

class UnrecognizedDataError(ParseError):
    pass

class UnrecognizedPreambleError(UnrecognizedDataError):
    pass

class UnexpectedDataError(ParseError):
    pass

class UnexpectedEndOfFileError(ParseError):
    pass

# FIXME: how do we type annotate this?
ParseState_ = collections.namedtuple('ParseState', ['buffer', 'start', 'end'])

class ParseState(ParseState_):
    buffer: bytes
    start: int
    end: int

    def startswith(self, expected: bytes | tuple[bytes, ...]):
        if isinstance(expected, tuple):
            for e in expected:
                if self.startswith(e):
                    return True
            return False
        if not isinstance(expected, bytes):
            raise TypeError('expected bytes or tuple of bytes')
        return self.buffer[self.start:self.start + len(expected)] == expected

    def startswithnc(self, expected: bytes | tuple[bytes, ...]):
        if isinstance(expected, tuple):
            for e in expected:
                if self.startswithnc(e):
                    return True
            return False
        if not isinstance(expected, bytes):
            raise TypeError('expected bytes or tuple of bytes')
        return self.buffer[self.start:self.start + len(expected)].lower() == expected

    def peekchar(self) -> int | None:
        if self.start < self.end:
            return self.buffer[self.start]

    def skipchar(self) -> ParseState:
        if self.start < self.end:
            return self._replace(start = self.start + 1)
        raise UnexpectedEndOfFileError()

# FIXME: how do we type annotate this?
StrParseState_ = collections.namedtuple('StrParseState', ['buffer', 'start', 'end'])

class StrParseState(StrParseState_):
    buffer: str
    start: int
    end: int

SgmlToken_ = collections.namedtuple('SgmlToken', ['kind', 'tag', 'attr_seq', 'data'])

class SgmlToken(SgmlToken_):
    kind: str
    tag: str | None
    attr_seq: tuple[tuple[str, str], ...] | None
    data: str | None

    attrs: dict

    def __init__(self, kind, tag, attr_seq, data):
        SgmlToken_.__init__(kind, tag, attr_seq, data)
        if attr_seq == None:
            self.attrs = None
        else:
            self.attrs = dict(attr_seq)

class SgmlTokenizer(html.parser.HTMLParser):
    def __init__(self, document, convert_charrefs=True, cdata=None, rcdata=None):
        self.document = document
        self.tokens = []
        if cdata is not None:
            self.CDATA_CONTENT_ELEMENTS = cdata
        if rcdata is not None:
            self.RCDATA_CONTENT_ELEMENTS = cdata
        super().__init__(convert_charrefs=convert_charrefs)

    def handle_token(self, kind, tag=None, attrs=None, data=None):
        self.tokens.append(SgmlToken(kind, tag, attrs, data))

    def handle_starttag(self, tag, attrs):
        self.handle_token('start', tag, attrs)
    
    def handle_endtag(self, tag):
        self.handle_token('end', tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_token('start', tag, attrs, data='empty')
        self.handle_token('end', tag, data='empty')

    def handle_data(self, data):
        self.handle_token('data', data=data)

    def handle_comment(self, data):
        self.handle_token('comment', data=data)

    def handle_decl(self, decl):
        self.handle_token('decl', data=decl)

    def handle_pi(self, data):
        self.tokens.append('pi', data=data)

    def unknown_decl(self, data):
        self.tokens.append('unknown', data=data)

# FIXME: how do we type annotate this?
TokenParseState_ = collections.namedtuple('TokenParseState', ['buffer', 'start', 'end'])

class TokenParseState(TokenParseState_):
    buffer: list[SgmlToken]
    start: int
    end: int

    def peektoken(self) -> int | None:
        if self.start < self.end:
            return self.buffer[self.start]

    def skiptoken(self) -> ParseState:
        if self.start < self.end:
            return self._replace(start = self.start + 1)
        raise UnexpectedEndOfFileError()

def parse_expectnc(data: ParseState, expected: bytes) -> ParseState:
    if not data.startswithnc(expected):
        raise UnexpectedDataError(f'Expected {repr(expected)}, got {repr(data.buffer[data.start:data.start+len(expected)])} at byte {data.start}')
    return data._replace(start = data.start + len(expected))

def parse_expect(data: ParseState, expected: bytes) -> ParseState:
    if not data.startswith(expected):
        raise UnexpectedDataError(f'Expected {repr(expected)}, got {repr(data.buffer[data.start:data.start+len(expected)])} at byte {data.start}')
    return data._replace(start = data.start + len(expected))

def parse_ascii_whitespace(data: ParseState) -> ParseState:
    if not data.peekchar() in (9, 10, 12, 13, 32):
        raise UnexpectedDataError(f'Expected ascii whitespace, got {repr(data.peekchar)} at byte {data.start}')
    data = data.skipchar()
    while data.peekchar() in (9, 10, 12, 13, 32):
        data = data.skipchar()
    return data

def parse_sgml_doctype(data: ParseState, info: dict) -> (ParseState, dict):
    data = parse_expectnc(data, b'<!doctype')
    data = parse_ascii_whitespace(data)
    typename_firstchar = data.peekchar()
    if typename_firstchar in (9, 10, 12, 13, 32, 62):
        raise UnexpectedDataError(f'Expected a document type name, got {repr(data.peekchar)} at byte {data.start}')
    typename = [typename_firstchar]
    data = data.skipchar()
    while data.peekchar() not in (9, 10, 12, 13, 32, 62):
        typename.append(data.peekchar())
        data = data.skipchar()
    info['document_type_name'] = bytes(typename).decode('utf8', errors='surrogateescape')
    data = parse_expect(data, b'>') # TODO: handle external identifier
    return data, info

def tokenparse_html_script(data: TokenParseState, info: dict) -> tuple[TokenParseState, dict]:
    open_token = data.peektoken()
    data = data.skiptoken()

    script = {}
    for attr, value in open_token.attr_seq:
        if attr == 'type':
            script['type'] = value
            continue
        if attr == 'src':
            script['src'] = value
            continue
        if 'attrs' not in script:
            script['attrs'] = []
        script['attrs'].append((attr, value))

    next_token = data.peektoken()
    if next_token.kind == 'data':
        script['content'] = next_token.data
        data = data.skiptoken()
        next_token = data.peektoken()
        if next_token.kind != 'end' or next_token.tag != 'script':
            raise ParseError(f"expected closing script tag, got token kind={next_token.kind}, tag={next_token.tag}")
        data.skiptoken()
    else:
        if next_token.kind != 'end' or next_token.tag != 'script':
            raise ParseError(f"expected data or closing script tag, got token kind={next_token.kind}, tag={next_token.tag}")
        data.skiptoken()

    if script.get('type', '').endswith('+json') and 'content' in script:
        script['json'] = json.loads(script['content'])
        
        if script['type'] == 'application/ld+json':
            if 'json_ld' not in info:
                info['json_ld'] = []
            info['json_ld'].extend(script['json'])

            return data, info

    if 'scripts' not in info:
        info['scripts'] = []
    info['scripts'].append(script)
    return data, info

def object_matches_ld(obj: dict, ld: dict):
    if obj.get('name') == ld.get('name'):
        return True
    obj_urls = set(([obj['url']] if 'url' in obj else []) + (obj.get('sameAs') or []))
    ld_urls = set(([ld['url']] if 'url' in ld else []) + (ld.get('sameAs') or []))
    if obj_urls & ld_urls:
        return True
    return False

def fill_from_json_ld(info: dict, ld: list) -> dict:
    # This is really complicated. We don't want to make additional requests or get bogged down in details, so just handle simple cases.
    if len(ld) != 1:
        # Nope, too complicated.
        return info

    ld = ld[0]

    if 'main_content' not in info:
        info['main_content'] = {}
    main_content = info['main_content']

    main_content['json_ld'] = ld

    if '@type' in ld and isinstance(ld['@type'], list) and 'kind' not in main_content:
        typ = ld['@type']
        if 'Article' in typ or 'http://schema.org/Article' in typ or 'https://schema.org/Article' in typ:
            main_content['kind'] = 'article'

    if 'headline' in ld and isinstance(ld['headline'], str) and 'headline' not in main_content:
        main_content['headline'] = ld['headline']
        if 'title' not in main_content:
            main_content['title'] = ld['headline']

    if 'datePublished' in ld and isinstance(ld['datePublished'], str) and 'datePublished' not in main_content:
        main_content['datePublished'] = ld['datePublished']

    if 'dateModified' in ld and isinstance(ld['dateModified'], str) and 'dateModified' not in main_content:
        main_content['dateModified'] = ld['dateModified']

    if 'description' in ld and isinstance(ld['description'], str) and 'description' not in main_content:
        main_content['description'] = {'text': ld['description']}

    if 'author' in ld and isinstance(ld['author'], list) and ld['author']:
        if 'author' not in main_content:
            main_content['author'] = []
        for author in ld['author']:
            for orig_author in main_content['author']:
                if object_matches_ld(orig_author, author):
                    break
            else:
                orig_author = {}
                main_content['author'].append(orig_author)
            if 'name' in author:
                orig_author['name'] = author['name']
            if 'url' in author:
                orig_author['url'] = author['url']
                orig_author['url_has_info'] = ['unknown']
            orig_author['json_ld'] = author

    if 'publisher' in ld and isinstance(ld['publisher'], dict) and ld['publisher']:
        if 'containing_feeds' not in main_content:
            main_content['containing_feeds'] = []
        pub = ld['publisher']
        for orig_feed in main_content['containing_feeds']:
            if object_matches_ld(orig_feed, pub):
                break
        else:
            orig_feed = {}
            main_content['containing_feeds'].append(orig_feed)
        if 'name' in pub:
            orig_feed['name'] = pub['name']
        if 'url' in pub:
            orig_feed['url'] = pub['url']
            orig_feed['url_has_info'] = ['unknown']
        orig_feed['json_ld'] = pub

    return info

def tokenparse_html_toplevel(data: TokenParseState, info: dict) -> tuple[TokenParseState, dict]:
    token = data.peektoken()
    if token.kind == 'start':
        if token.tag == 'html':
            for attr, value in token.attr_seq:
                if attr == 'id' and 'html_id' not in info:
                    info['html_id'] = value
                    continue
                if attr == 'class' and 'html_class' not in info:
                    info['html_class'] = value
                    continue
                if 'html_unknownattrs' not in info:
                    info['html_unknownattrs'] = []
                info['html_unknownattrs'].append((attr, value))
                continue
            return data.skiptoken(), info
        if token.tag == 'head':
            for attr, value in token.attr_seq:
                if 'head_attrs' not in info:
                    info['head_attrs'] = []
                info['head_attrs'].append((attr, value))
                continue
            return data.skiptoken(), info
        if token.tag == 'script':
            try:
                data, info = tokenparse_html_script(data, info)
                return data.skiptoken(), info
            except ParseError:
                if 'errors' not in info:
                    info['errors'] = []
                info['errors'].append(traceback.format_exception())
        if token.tag == 'link':
            if 'html_links' not in info:
                info['html_links'] = []
            link = {}
            for attr, value in token.attr_seq:
                if attr == 'rel':
                    link['rel'] = value
                    continue
                if attr == 'href':
                    link['href'] = value
                    continue
                if attr == 'type':
                    link['type'] = value
                    continue
                if attr == 'title':
                    link['title'] = value
                    continue
                if 'attrs' not in link:
                    link['attrs'] = []
                link['attrs'].append((attr, value))
            info['html_links'].append(link)
            if link.get('rel') == 'canonical' and 'url' not in info and 'href' in link:
                info['url'] = link['href']
            if link.get('rel') == 'canonical' and 'base_url' not in info and 'href' in link:
                info['base_url'] = link['href']
            if link.get('rel') == 'alternate' and link.get('type') == 'application/rss+xml' and 'href' in link:
                href = urllib.parse.urljoin(info.get('base_url', ''), link['href'])
                if 'main_content' not in info:
                    info['main_content'] = {}
                main_content = info['main_content']
                feed = {
                    'url': href,
                    'url_has_info': ['name', 'description', 'unknown'],
                    'html_link': link,
                    }
                if 'title' in link:
                    feed['name'] = link['title']
                else:
                    feed['generic_name'] = "RSS Feed"
                if 'containing_feeds' not in main_content:
                    main_content['containing_feeds'] = []
                main_content['containing_feeds'].append(feed)
            if link.get('rel') in ('icon', 'shortcut icon', 'apple-touch-icon') and 'href' in link:
                if 'sizes' in token.attrs:
                    if token.attrs['sizes'] == 'any':
                        this_size = "any"
                    else:
                        this_size = int(link.attrs['sizes'].split('x')[0])
                elif link['rel'] == 'apple-touch-icon':
                    this_size = 192
                else:
                    this_size = 16
                if 'favicon' not in info or (info['favicon']['size'] != 'any' and (this_size == 'any' or this_size > info['favicon']['size'])):
                    href = urllib.parse.urljoin(info.get('base_url', ''), link['href'])
                    info['favicon'] = {
                        'size': this_size,
                        'url': href,
                    }
            return data.skiptoken(), info
    if token.kind == 'end':
        if token.tag == 'html':
            return data.skiptoken(), info
    if token.kind == 'decl':
        # Assume the doctype has already been handled
        return data.skiptoken(), info
    if token.kind == 'data':
        if token.data.isspace():
            # whitespace
            return data.skiptoken(), info
    if token.kind == 'comment':
        if 'html_comments' not in info:
            info['html_comments'] = []
        info['html_comments'].append(token.data)
        return data.skiptoken(), info
    # unrecognized data
    data = data.skiptoken()

    if 'unknown_tokens' not in info:
        info['unknown_tokens'] = []

    info['unknown_tokens'].append({'kind': token.kind, 'tag': token.tag, 'attrs': token.attr_seq, 'data': token.data})

    return data, info

def tokenparse_html(data: TokenParseState, info: dict) -> dict:
    while data.start < data.end:
        data, info = tokenparse_html_toplevel(data, info)
    if 'json_ld' in info:
        info = fill_from_json_ld(info, info['json_ld'])
    return data, info

def strparse_html(data: StrParseState, info: dict) -> dict:
    document = data.buffer[data.start:data.end]
    parser = SgmlTokenizer(document)
    parser.feed(document)
    tokens = parser.tokens

    token_data = TokenParseState(tokens, 0, len(tokens))
    info = tokenparse_html(token_data, info)

    return info

def parse_html(data: ParseState, info: dict) -> dict:
    # TODO: use a passed in encoding or detect encoding from content
    string_data = data.buffer[data.start:data.end].decode('utf8', errors='surrogateescape')
    string_data = StrParseState(string_data, 0, len(string_data))
    string_data, info = strparse_html(string_data, info)
    if string_data.start != string_data.end:
        info['trailing_data'] = string_data.buffer[string_data.start:string_data.end]
    return info

def parse_unknown(data: ParseState, info: dict) -> (ParseState, dict):
    if data.startswithnc(b'<!doctype'):
        full_data = data
        data, info = parse_sgml_doctype(data, info)
        if info['document_type_name'].lower() == 'html':
            info = parse_html(full_data, info)
            data = full_data._replace(start = full_data.end)
        return data, info
    raise UnrecognizedPreambleError(repr(data.buffer[data.start:data.start+256]))

def parse_bytes(buffer: bytes, info: dict) -> dict:
    data = ParseState(buffer, 0, len(buffer))
    data, info = parse_unknown(data, {})
    if data.start != data.end:
        info['trailing_data'] = data.buffer[data.start:data.end].decode('utf8', errors='surrogateescape')
    return info

def parse_bytestream(stream: typing.BinaryIO, info: dict) -> dict:
    buffer = stream.read()
    return parse_bytes(buffer, info)

def main(args):
    for arg in args:
        if arg == '-':
            info = parse_bytestream(sys.stdin.buffer, {})
            print(json.dumps(info, indent=4))
        else:
            raise NotImplementedError("fetch not implemented")

if __name__ == '__main__':
    main(sys.argv[1:])
