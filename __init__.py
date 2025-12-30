from __future__ import annotations

import collections
import html.parser
import json
import sys
import typing

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
    # unrecognized data
    data = data.skiptoken()

    if 'unknown_tokens' not in info:
        info['unknown_tokens'] = []

    info['unknown_tokens'].append({'kind': token.kind, 'tag': token.tag, 'attrs': token.attr_seq, 'data': token.data})

    return data, info

def tokenparse_html(data: TokenParseState, info: dict) -> dict:
    while data.start < data.end:
        data, info = tokenparse_html_toplevel(data, info)
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
