import collections
import json
import sys
import typing

# FIXME: how do we type annotate this?
ParseState_ = collections.namedtuple('ParseState', ['buffer', 'start', 'end'])

class ParseState(ParseState_):
    def startswithnc(self, expected: bytes | tuple[bytes, ...]):
        if isinstance(expected, tuple):
            for e in expected:
                if self.startswithnc(e):
                    return True
            return False
        if not isinstance(expected, bytes):
            raise TypeError('expected bytes or tuple of bytes')
        return self.buffer[self.start:self.start + len(expected)].lower() == expected

class ParseError(Exception):
    pass

class UnrecognizedDataError(ParseError):
    pass

class UnrecognizedPreambleError(UnrecognizedDataError):
    pass

class UnexpectedDataError(ParseError):
    pass

def parse_expectnc(data: ParseState, expected: bytes) -> ParseState:
    if not data.startswithnc(expected):
        raise UnexpectedDataError(f'Expected {repr(expected)}, got {repr(data.buffer[data.start:data.start+len(expected)])}')
    return data._replace(start = data.start + len(expected))

def parse_doctype(data: ParseState, info: dict) -> (ParseState, dict):
    data = parse_expectnc(data, b'<!doctype')
    return data, info

def parse_unknown(data: ParseState, info: dict) -> (ParseState, dict):
    if data.startswithnc(b'<!doctype'):
        data, doctype = parse_doctype(data, info)
        return data, doctype
    raise UnrecognizedPreambleError(repr(data.buffer[data.start:data.start+256]))

def parse_bytes(buffer: bytes, info: dict) -> dict:
    data = ParseState(buffer, 0, len(buffer))
    data, info = parse_unknown(data, {})
    if data.start != data.end:
        info['trailing_data'] = repr(data.buffer[data.start:data.end])
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
