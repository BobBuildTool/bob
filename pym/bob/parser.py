# Bob build tool
# Copyright (C) 2016  Karsten Heinze
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from collections import namedtuple
import re
from .errors import ParseError


Rule   = namedtuple( 'Rule', [ 'pattern', 'parser', 'once' ] )
Hit    = namedtuple( 'Hit', [ 'index', 'token', 'rule' ] )

class TokenType:
    BEG   = 'a'
    BEG_  = 'b'
    END   = 'c'
    END_  = 'd'
    REG   = 'e'
    SQUO  = 'f'
    DQUO  = 'g'
    VAR   = 'h'
    CMD   = 'i'
    DELIM = 'j'
    BS    = 'k'
    ECH   = 'l'
    CMOD  = 'm'
    RMVL  = 'n'
    REPL  = 'o'
    WHSP  = 'p'

    # virtual types
    TEXT  = '(' + '|'.join( [ REG, BS, ECH ] ) + ')'
    WHEV  = '(' + '|'.join( [ REG, SQUO, DQUO, VAR, CMD, DELIM, BS, ECH, CMOD, RMVL, REPL ] ) + ')'

TokenDesc = {
    TokenType.BEG:   'BEGIN',
    TokenType.BEG_:  'BEGIN',
    TokenType.END:   'END',
    TokenType.END_:  'END',
    TokenType.REG:   'REGULAR_TEXT',
    TokenType.SQUO:  'SINGLE_QUOTED',
    TokenType.DQUO:  'DOUBLE_QUOTED',
    TokenType.VAR:   'VARIABLE',
    TokenType.CMD:   'COMMAND',
    TokenType.DELIM: 'DELIMITER',
    TokenType.BS:    'BACKSLASH',
    TokenType.ECH:   'ESCAPED_CHAR',
    TokenType.CMOD:  'CASE_MODIFICATION',
    TokenType.RMVL:  'REMOVAL_RULE',
    TokenType.REPL:  'REPLACEMENT_RULE',
    TokenType.WHSP:  'WHITESPACE',
    }

class Token:
    def __init__( self, name, text, sub = [] ):
        self.name = name
        self.text = text
        self.sub = sub

    def refresh( self ):
        if self.sub:
            self.text = ''.join( [ s.text for s in self.sub ] )

    def __str__( self ):
        return self.toStringLong()

    def toStringShort( self ):
        return "{} /{}/".format( TokenDesc[ self.name ], self.text )

    def toStringLong( self ):
        return "{}: [{}]".format( self.toStringShort(), ', '.join( [ str( s ) for s in self.sub ] ) )

class Tokenizer:
    def __init__( self ):
        self.string  = ''
        self.pos = 0
        self.ignore = []
        self.cache = None
        self.finished = False

    def reset( self, string = '', offset = 0 ):
        self.string = string
        self.pos = offset
        self.ignore.clear()
        self.cache = None
        self.finished = False

    def getTokens( self ):
        raise ValueError( 'tokens not set' )

    def getDefault( self ):
        raise ValueError( 'default not set' )

    def getNext( self ):
        if self.finished:
            return None

        hit = None

        if not self.cache:
            hit = Hit( len( self.string ), None, None )
            for token in self.getTokens():
                if token[0] in self.ignore: continue

                rule = token[1]
                m = rule.pattern.search( self.string[self.pos:] )
                if m and ( ( self.pos + m.start() ) < hit.index ):
                    hit = Hit( self.pos + m.start(), Token( token[0], m.group() ), rule )
        else:
            hit = self.cache
            self.cache = None

        result = None

        if hit.token:
            if hit.index > self.pos:
                result = Token( self.getDefault(), self.string[self.pos:hit.index] )
                self.pos = hit.index
                self.cache = hit
            else:
                if hit.rule.once:
                    self.ignore.append( hit.token.name )

                parser = hit.rule.parser

                if parser:
                    text, tokens = parser().parse( self.string, self.pos )
                    result = Token( hit.token.name, text, tokens )
                    self.pos += len( text )
                else:
                    result = hit.token
                    self.pos += len( hit.token.text )

        else:
            result = Token( self.getDefault(), self.string[self.pos:] )
            self.pos = len( self.string )

        if ( result and result.name in [ TokenType.END, TokenType.END_ ] ) or self.pos == len( self.string ):
            self.finished = True

        return result

class Parser:
    def __init__( self ):
        None

    def parse( self, text, offset = 0 ):
        self.getTokenizer().reset( text, offset )
        tokens = []

        while ( True ):
            token = self.getTokenizer().getNext()
            if not token: break
            tokens.append( token )

        # Check against grammar!
        line = ' '.join( [ t.name for t in tokens ] )
        if not self.getGrammar().match( line ):
            raise ParseError( 'parse error: {}'.format( '  '.join( [ t.toStringShort() for t in tokens ] ) ) )

        return ''.join( [ t.text for t in tokens ] ), tokens

    def getTokenizer( self ):
        raise ValueError( 'tokenizer not set' )

    def getGrammar( self ):
        raise ValueError( 'grammar not set' )

class VariableTokenizer( Tokenizer ):
    __default = TokenType.REG

    __tokens = []

    def __init__( self ):
        super().__init__()

    def getDefault( self ):
        return self.__default

    def getTokens( self ):
        return self.__tokens

class VariableParser( Parser ):
    __grammar = re.compile( r'^{beg}( {text}| {var})( {cmod}|( {delim}| {rmvl}| {repl})( {whev})*)? {end}$'.format(
            beg   = TokenType.BEG_,
            text  = TokenType.TEXT,
            var   = TokenType.VAR,
            delim = TokenType.DELIM,
            dquo  = TokenType.DQUO,
            squo  = TokenType.SQUO,
            cmod  = TokenType.CMOD,
            rmvl  = TokenType.RMVL,
            repl  = TokenType.REPL,
            whev  = TokenType.WHEV,
            end   = TokenType.END_
            )
        )

    def __init__( self ):
        super().__init__()
        self.__tokenizer = VariableTokenizer()

    def getTokenizer( self ):
        return self.__tokenizer

    def getGrammar( self ):
        return self.__grammar

class CommandTokenizer( Tokenizer ):
    __default = TokenType.REG

    __tokens = []

    def __init__( self ):
        super().__init__()

    def getDefault( self ):
        return self.__default

    def getTokens( self ):
        return self.__tokens

class CommandParser( Parser ):
    __grammar = re.compile( r'^{beg} {text}( {delim}( {text}| {dquo}| {squo}| {var}| {cmd})+)* {end}$'.format(
            beg   = TokenType.BEG_,
            text  = TokenType.TEXT,
            delim = TokenType.DELIM,
            dquo  = TokenType.DQUO,
            squo  = TokenType.SQUO,
            var   = TokenType.VAR,
            cmd   = TokenType.CMD,
            end   = TokenType.END_
            )
        )

    def __init__( self ):
        super().__init__()
        self.__tokenizer = CommandTokenizer()

    def getTokenizer( self ):
        return self.__tokenizer

    def getGrammar( self ):
        return self.__grammar

class StringTokenizer( Tokenizer ):
    __default = TokenType.REG

    __tokens = []

    def __init__( self, style = 'unquoted' ):
        super().__init__()
        self.__style = style

    def getDefault( self ):
        return self.__default

    def getTokens( self ):
        return self.__tokens[ self.__style ]

class StringParserSingle( Parser ):
    __grammar =  re.compile( r'^{beg} {text} {end}$'.format(
            beg  = TokenType.BEG_,
            text = TokenType.TEXT,
            end  = TokenType.END_
            )
        )

    def __init__( self ):
        super().__init__()
        self.__tokenizer = StringTokenizer( 'single' )

    def getTokenizer( self ):
        return self.__tokenizer

    def getGrammar( self ):
        return self.__grammar

class StringParserDouble( Parser ):
    __grammar = re.compile( r'^{beg}( {text}| {squo}| {dquo}| {var}| {cmd})* {end}$'.format(
            beg  = TokenType.BEG_,
            text = TokenType.TEXT,
            squo = TokenType.SQUO,
            dquo = TokenType.DQUO,
            var  = TokenType.VAR,
            cmd  = TokenType.CMD,
            end  = TokenType.END_
            )
        )

    def __init__( self ):
        super().__init__()
        self.__tokenizer = StringTokenizer( 'double' )

    def getTokenizer( self ):
        return self.__tokenizer

    def getGrammar( self ):
        return self.__grammar

class RemovalParser( Parser ):
    __grammar = re.compile( r'^{beg} {whev} {end}$'.format(
            beg  = TokenType.BEG,
            whev = TokenType.WHEV,
            end  = TokenType.END
            )
        )

    def __init__( self ):
        super().__init__()
        self.__tokenizer = StringTokenizer( 'removal' )

    def getTokenizer( self ):
        return self.__tokenizer

    def getGrammar( self ):
        return self.__grammar

class ReplacementParser( Parser ):
    __grammar =  re.compile( r'^{beg}( {whev})+ {end}$'.format(
            beg  = TokenType.BEG,
            whev = TokenType.WHEV,
            end  = TokenType.END
            )
        )

    def __init__( self ):
        super().__init__()
        self.__tokenizer = StringTokenizer( 'replacement' )

    def getTokenizer( self ):
        return self.__tokenizer

    def getGrammar( self ):
        return self.__grammar

class IfConditionTokenizer( Tokenizer ):
    __default = TokenType.REG

    __tokens = []

    def __init__( self ):
        super().__init__()

    def getDefault( self ):
        return self.__default

    def getTokens( self ):
        return self.__tokens

class IfConditionParser( Parser ):
    __grammar = re.compile( r'^({var}|{cmd}|({text}(( {whsp}| {text})* {text})?))$'.format(
            text = TokenType.TEXT,
            var  = TokenType.VAR,
            cmd  = TokenType.CMD,
            whsp = TokenType.WHSP,
            )
        )

    def __init__( self ):
        super().__init__()
        self.__tokenizer = IfConditionTokenizer()

    def parse( self, text, offset = 0 ):
        # Return text as is if no special chars were found.
        if all( (c not in text[offset:]) for c in '\t \\\"\'$' ):
            return text[offset:], [ Token( self.getTokenizer().getDefault(), text[offset:], [] ) ]
        else:
            return super().parse( text, offset )

    def getTokenizer( self ):
        return self.__tokenizer

    def getGrammar( self ):
        return self.__grammar


# Set tokens here because of cross references to parsers.
VariableTokenizer._VariableTokenizer__tokens = [
    ( TokenType.BEG_,  Rule( re.compile( r'\$\{' ), None, True ) ),
    ( TokenType.ECH,   Rule( re.compile( r'\\[^\\]' ), None, False ) ),
    ( TokenType.BS,    Rule( re.compile( r'\\\\' ), None, False ) ),
    ( TokenType.SQUO,  Rule( re.compile( r"'" ), StringParserSingle, False ) ),
    ( TokenType.DQUO,  Rule( re.compile( r'"' ), StringParserDouble, False ) ),
    ( TokenType.DELIM, Rule( re.compile( r'(:?-|:?\+)' ), None, True ) ),
    ( TokenType.CMOD,  Rule( re.compile( r'(\^{1,2}|,{1,2})' ), None, True ) ),
    ( TokenType.VAR,   Rule( re.compile( r'\$\{' ), VariableParser, False ) ),
    ( TokenType.RMVL,  Rule( re.compile( r'(%{1,2}|#{1,2})' ), RemovalParser, True ) ),
    ( TokenType.REPL,  Rule( re.compile( r'(/{1,2})' ), ReplacementParser, True ) ),
    ( TokenType.END_,  Rule( re.compile( r'\}' ), None, False ) ),
    ]

CommandTokenizer._CommandTokenizer__tokens = [
    ( TokenType.BEG_,  Rule( re.compile( r'\$\(' ), None, True ) ),
    ( TokenType.ECH,   Rule( re.compile( r'\\[^\\]' ), None, False ) ),
    ( TokenType.BS,    Rule( re.compile( r'\\\\' ), None, False ) ),
    ( TokenType.SQUO,  Rule( re.compile( r"'" ), StringParserSingle, False ) ),
    ( TokenType.DQUO,  Rule( re.compile( r'"' ), StringParserDouble, False ) ),
    ( TokenType.DELIM, Rule( re.compile( r',' ), None, False ) ),
    ( TokenType.VAR,   Rule( re.compile( r'\$\{' ), VariableParser, False ) ),
    ( TokenType.CMD,   Rule( re.compile( r'\$\(' ), CommandParser, False ) ),
    ( TokenType.END_,  Rule( re.compile( r'\)' ), None, False ) ),
    ]

IfConditionTokenizer._IfConditionTokenizer__tokens = [
    ( TokenType.WHSP,  Rule( re.compile( r'\s+' ), None, False ) ),
    ( TokenType.ECH,   Rule( re.compile( r'\\[^\\]' ), None, False ) ),
    ( TokenType.BS,    Rule( re.compile( r'\\\\' ), None, False ) ),
    ( TokenType.SQUO,  Rule( re.compile( r"'" ), StringParserSingle, False ) ),
    ( TokenType.DQUO,  Rule( re.compile( r'"' ), StringParserDouble, False ) ),
    ( TokenType.VAR,   Rule( re.compile( r'\$\{' ), VariableParser, False ) ),
    ( TokenType.CMD,   Rule( re.compile( r'\$\(' ), CommandParser, False ) ),
    ]

StringTokenizer._StringTokenizer__tokens = {
    'unquoted': [
        ( TokenType.ECH,  Rule( re.compile( r'\\[^\\]' ), None, False ) ),
        ( TokenType.BS,   Rule( re.compile( r'\\\\' ), None, False ) ),
        ( TokenType.SQUO, Rule( re.compile( r"'" ), StringParserSingle, False ) ),
        ( TokenType.DQUO, Rule( re.compile( r'"' ), StringParserDouble, False ) ),
        ( TokenType.VAR,  Rule( re.compile( r'\$\{' ), VariableParser, False ) ),
        ( TokenType.CMD,  Rule( re.compile( r'\$\(' ), CommandParser, False ) ),
        ],
    'single': [
        ( TokenType.BEG_, Rule( re.compile( r"'" ), None, True ) ),
        ( TokenType.END_, Rule( re.compile( r"'" ), None, False ) ),
        ],
    'double': [
        ( TokenType.BEG_, Rule( re.compile( r'"' ), None, True ) ),
        ( TokenType.VAR,  Rule( re.compile( r'\$\{' ), VariableParser, False ) ),
        ( TokenType.CMD,  Rule( re.compile( r'\$\(' ), CommandParser, False ) ),
        ( TokenType.ECH,  Rule( re.compile( r'\\[^\\]' ), None, False ) ),
        ( TokenType.BS,   Rule( re.compile( r'\\\\' ), None, False ) ),
        ( TokenType.END_, Rule( re.compile( r'"' ), None, False ) ),
        ],
    'removal': [
        ( TokenType.BEG,  Rule( re.compile( r'(%{1,2}|#{1,2})' ), None, True ) ),
        ( TokenType.VAR,  Rule( re.compile( r'(?<!\\)\$\{' ), VariableParser, False ) ),
        ( TokenType.END,  Rule( re.compile( r'(?<!\\)(?=\})' ), None, False ) ),
        ],
    'replacement': [
        ( TokenType.BEG , Rule( re.compile( r'(/{1,2})' ), None, True ) ),
        ( TokenType.VAR,  Rule( re.compile( r'(?<!\\)\$\{' ), VariableParser, False ) ),
        ( TokenType.END,  Rule( re.compile( r'(?<!\\)/' ), None, False ) ),
        ],
    }

class StringParser( Parser ):
    __grammar = re.compile( r'^$|^({text}|{squo}|{dquo}|{var}|{cmd})( {text}| {squo}| {dquo}| {var}| {cmd})*$'.format(
            text = TokenType.TEXT,
            squo = TokenType.SQUO,
            dquo = TokenType.DQUO,
            var  = TokenType.VAR,
            cmd  = TokenType.CMD
            )
        )

    def __init__( self ):
        super().__init__()
        self.__tokenizer = StringTokenizer()

    def parse( self, text, offset = 0 ):
        # Return text as is if no special chars were found.
        if all( (c not in text[offset:]) for c in '\\\"\'$' ):
            return text[offset:], [ Token( self.getTokenizer().getDefault(), text[offset:], [] ) ]
        else:
            return super().parse( text, offset )

    def getTokenizer( self ):
        return self.__tokenizer

    def getGrammar( self ):
        return self.__grammar


def substituteParseResult( tokens, env, funs, funArgs, level = 0 ):
    def getVarArgs( tokens ):
        delimiterHandled = False
        args = [ '' ]

        for t in token.sub[1:-1]:
            if t.name == TokenType.CMOD:
                args.append( t.text )
                break

            if not delimiterHandled:
                if t.name in TokenType.DELIM:
                    args.append( t.text )
                    args.append( '' )
                    delimiterHandled = True
                elif t.name == TokenType.RMVL:
                    for tt in t.sub:
                        if tt.name == TokenType.BEG:
                            args.append( tt.text )
                            args.append( '' )
                        else:
                            args[ -1 ] += tt.text

                    delimiterHandled = True
                elif t.name == TokenType.REPL:
                    for tt in t.sub:
                        if tt.name in [ TokenType.BEG, TokenType.END ]:
                            args.append( tt.text )
                            args.append( '' )
                        else:
                            args[ -1 ] += tt.text

                    delimiterHandled = True
                else:
                    args[-1] += t.text
            else:
                args[-1] += t.text

        return args

    def getCmdArgs( tokens ):
        args = [ '' ]
        for t in token.sub[1:-1]:
            if t.name == TokenType.DELIM:
                args.append( '' )
            else:
                args[-1] += t.text

        return args

    def toRegex( glob, mode = 'remove' ):
        pattern = glob
        pattern = re.sub( r'(?<!\\)\.', r'\.', pattern )
        pattern = re.sub( r'(?<!\\)\?', r'.', pattern )
        pattern = re.sub( r'(?<!\\)\*', r'.*', pattern )
        pattern = re.sub( r'(?<!\\)\$', r'\$', pattern )
        pattern = re.sub( r'(?<!\\)\^', r'\^', pattern )
        pattern = re.sub( r'(?<!\\)\{', r'\{', pattern )
        pattern = re.sub( r'(?<!\\)\}', r'\}', pattern )
        pattern = re.sub( r'(?<!\\)\(', r'\(', pattern )
        pattern = re.sub( r'(?<!\\)\)', r'\(', pattern )

        # Search and replace: Undo escaping of /
        if mode == 'replace':
            pattern = re.sub( r'\\/', r'/', pattern )

        return pattern

    for i, token in enumerate( tokens ):
        if token.sub:
            substituteParseResult( token.sub, env, funs, funArgs, level + 1 )
            token.refresh()

        if token.name in [ TokenType.BEG_, TokenType.END_ ]:
            token.text = ''
        elif token.name == TokenType.BS:
            token.text = '\\'
        elif token.name == TokenType.ECH:
            token.text = token.text[1:]
        elif token.name == TokenType.VAR:
            args = getVarArgs( token.sub )

            # Lookup variable name in env.
            varName = args[ 0 ]
            varUnset = varName not in env
            varValue = ( env[ varName ] if not varUnset else '' )

            # Expand!

            # Use the parameter value from environment.
            if len( args ) == 1:
                if varUnset:
                    raise ParseError( 'variable "{}" is unset'.format( varName ) )
            # Paramater expansion.
            elif len( args ) == 2:
                # Downcase, upcase etc.
                if args[ 1 ] == ',':
                    if len( varValue ) == 1:
                        varValue = varValue[0].lower()
                    elif len( varValue ) > 1:
                        varValue = varValue[0].lower() + varValue[1:]
                    else:
                        pass
                elif args[1] == ',,':
                    varValue = varValue.lower()
                elif args[1] == '^':
                    varValue = varValue.capitalize()
                elif args[1] == '^^':
                    varValue = varValue.upper()
                else:
                    pass
            else:
                # Use a default value.
                if ( args[1] == ':-' ):
                    if varUnset or len( varValue ) == 0:
                        varValue = args[2]
                elif ( args[1] == '-' ):
                    if varUnset:
                        varValue = args[2]
                # Use an alternate value.
                elif ( args[1] == ':+' ):
                    if not ( varUnset or len( varValue ) == 0 ):
                        varValue = args[2]
                elif ( args[1] == '+' ):
                    if not varUnset:
                        varValue = args[2]
                # Remove from the end.
                elif args[1] == '%':
                    pattern = re.compile(toRegex(args[2]) + '$')
                    for k in range(len(varValue)-1,-1,-1):
                        if pattern.match(varValue[k:]):
                            varValue = varValue[:k]
                            break
                elif args[1] == '%%':
                    pattern = re.compile(toRegex(args[2]) + '$')
                    for k in range(0,len(varValue)-1):
                        if pattern.match(varValue[k:]):
                            varValue = varValue[:k]
                            break
                # Remove from the beginning.
                elif args[1] == '#':
                    if args[2] == '*':
                        varValue = varValue[1:]
                    else:
                        pattern = re.compile(toRegex(args[2]) + '$')
                        for k in range(0,len(varValue)):
                            if pattern.match(varValue[:k]):
                                varValue = varValue[k:]
                                break
                elif args[1] == '##':
                    pattern = re.compile(toRegex(args[2]) + '$')
                    for k in range(len(varValue),-1,-1):
                        if pattern.match(varValue[:k]):
                            varValue = varValue[k:]
                            break
                elif args[1] == '/':
                    pattern = re.compile(toRegex(args[2], 'replace'))
                    varValue = pattern.sub( args[4], varValue, 1 )
                elif args[1] == '//':
                    pattern = re.compile(toRegex(args[2], 'replace'))
                    varValue = pattern.sub( args[4], varValue )

            token.text = varValue
            token.sub.clear()

        elif token.name == TokenType.CMD:
            args = getCmdArgs( token.sub )

            if args[0] not in funs:
                raise ParseError( 'function "{}" is unknown'.format( args[0] ) )

            funValue = funs[ args[0] ]( args[1:], env=env, **funArgs )
            token.text = funValue
            token.sub.clear()

        else:
            pass

    return ''.join( [ t.text for t in tokens ] )
