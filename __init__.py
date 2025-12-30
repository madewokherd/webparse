from __future__ import annotations

import collections
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

def parse_unknown(data: ParseState, info: dict) -> (ParseState, dict):
    if data.startswithnc(b'<!doctype'):
        data, doctype = parse_sgml_doctype(data, info)
        return data, doctype
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
            raise NotImplementedError("HTML fetch not implemented")

if __name__ == '__main__':
    main(sys.argv[1:])
