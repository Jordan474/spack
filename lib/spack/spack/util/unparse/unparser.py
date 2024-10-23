# Copyright (c) 2014-2021, Simon Percivall and Spack Project Developers.
#
# SPDX-License-Identifier: Python-2.0
"Usage: unparse.py <path to source file>"
import ast
import sys
from contextlib import contextmanager
from enum import IntEnum, auto
from io import StringIO


# TODO: if we require Python 3.7, use its `nullcontext()`
@contextmanager
def nullcontext():
    yield


# Large float and imaginary literals get turned into infinities in the AST.
# We unparse those infinities to INFSTR.
_INFSTR = "1e" + repr(sys.float_info.max_10_exp + 1)


class _Precedence(IntEnum):
    """Precedence table that originated from python grammar."""

    NAMED_EXPR = auto()  # <target> := <expr1>
    TUPLE = auto()  # <expr1>, <expr2>
    YIELD = auto()  # 'yield', 'yield from'
    TEST = auto()  # 'if'-'else', 'lambda'
    OR = auto()  # 'or'
    AND = auto()  # 'and'
    NOT = auto()  # 'not'
    CMP = auto()  # '<', '>', '==', '>=', '<=', '!=',
    # 'in', 'not in', 'is', 'is not'
    EXPR = auto()
    BOR = EXPR  # '|'
    BXOR = auto()  # '^'
    BAND = auto()  # '&'
    SHIFT = auto()  # '<<', '>>'
    ARITH = auto()  # '+', '-'
    TERM = auto()  # '*', '@', '/', '%', '//'
    FACTOR = auto()  # unary '+', '-', '~'
    POWER = auto()  # '**'
    AWAIT = auto()  # 'await'
    ATOM = auto()

    def next(self):
        try:
            return self.__class__(self + 1)
        except ValueError:
            return self


_SINGLE_QUOTES = ("'", '"')
_MULTI_QUOTES = ('"""', "'''")
_ALL_QUOTES = (*_SINGLE_QUOTES, *_MULTI_QUOTES)


def is_simple_tuple(slice_value):
    # when unparsing a non-empty tuple, the parantheses can be safely
    # omitted if there aren't any elements that explicitly requires
    # parantheses (such as starred expressions).
    return (
        isinstance(slice_value, ast.Tuple)
        and slice_value.elts
        and not any(isinstance(elt, ast.Starred) for elt in slice_value.elts)
    )


class Unparser:
    """Methods in this class recursively traverse an AST and
    output source code for the abstract syntax; original formatting
    is disregarded."""

    def __init__(self, py_ver_consistent=False, _avoid_backslashes=False):
        """Traverse an AST and generate its source.

        Arguments:
            py_ver_consistent (bool): if True, generate unparsed code that is
                consistent between Python versions 3.5-3.11.

        For legacy reasons, consistency is achieved by unparsing Python3 unicode literals
        the way Python 2 would. This preserved Spack package hash consistency during the
        python2/3 transition
        """
        self.future_imports = []
        self._indent = 0
        self._py_ver_consistent = py_ver_consistent
        self._precedences = {}
        self._avoid_backslashes = _avoid_backslashes

    def interleave(self, inter, f, seq):
        """Call f on each item in seq, calling inter() in between."""
        seq = iter(seq)
        try:
            f(next(seq))
        except StopIteration:
            pass
        else:
            for x in seq:
                inter()
                f(x)

    def items_view(self, traverser, items):
        """Traverse and separate the given *items* with a comma and append it to
        the buffer. If *items* is a single item sequence, a trailing comma
        will be added."""
        if len(items) == 1:
            traverser(items[0])
            self.write(",")
        else:
            self.interleave(lambda: self.write(", "), traverser, items)

    def fill(self, text=""):
        "Indent a piece of text, according to the current indentation level"
        self.f.write("\n" + "    " * self._indent + text)

    def write(self, text):
        "Append a piece of text to the current line."
        self.f.write(str(text))

    class _Block:
        """A context manager for preparing the source for blocks. It adds
        the character ':', increases the indentation on enter and decreases
        the indentation on exit."""

        def __init__(self, unparser):
            self.unparser = unparser

        def __enter__(self):
            self.unparser.write(":")
            self.unparser._indent += 1

        def __exit__(self, exc_type, exc_value, traceback):
            self.unparser._indent -= 1

    def block(self):
        return self._Block(self)

    @contextmanager
    def delimit(self, start, end):
        """A context manager for preparing the source for expressions. It adds
        *start* to the buffer and enters, after exit it adds *end*."""

        self.write(start)
        yield
        self.write(end)

    def delimit_if(self, start, end, condition):
        if condition:
            return self.delimit(start, end)
        else:
            return nullcontext()

    def require_parens(self, precedence, node):
        """Shortcut to adding precedence related parens"""
        return self.delimit_if("(", ")", self.get_precedence(node) > precedence)

    def get_precedence(self, node):
        return self._precedences.get(node, _Precedence.TEST)

    def set_precedence(self, precedence, *nodes):
        for node in nodes:
            self._precedences[node] = precedence

    def traverse(self, tree):
        "Dispatcher function, dispatching tree type T to method _T."
        if isinstance(tree, list):
            for node in tree:
                self.traverse(node)
            return
        meth = getattr(self, "visit_" + tree.__class__.__name__)
        meth(tree)

    def visit(self, tree, output_file):
        """Traverse tree and write source code to output_file."""
        self.f = output_file
        self.traverse(tree)
        self.f.flush()

    #
    # Unparsing methods
    #
    # There should be one method per concrete grammar type Constructors
    # should be # grouped by sum type. Ideally, this would follow the order
    # in the grammar, but currently doesn't.

    def visit_Module(self, tree):
        for stmt in tree.body:
            self.traverse(stmt)

    def visit_Interactive(self, tree):
        for stmt in tree.body:
            self.traverse(stmt)

    def visit_Expression(self, tree):
        self.traverse(tree.body)

    # stmt
    def visit_Expr(self, tree):
        self.fill()
        self.set_precedence(_Precedence.YIELD, tree.value)
        self.traverse(tree.value)

    def visit_NamedExpr(self, tree):
        with self.require_parens(_Precedence.TUPLE, tree):
            self.set_precedence(_Precedence.ATOM, tree.target, tree.value)
            self.traverse(tree.target)
            self.write(" := ")
            self.traverse(tree.value)

    def visit_Import(self, node):
        self.fill("import ")
        self.interleave(lambda: self.write(", "), self.traverse, node.names)

    def visit_ImportFrom(self, node):
        # A from __future__ import may affect unparsing, so record it.
        if node.module and node.module == "__future__":
            self.future_imports.extend(n.name for n in node.names)

        self.fill("from ")
        self.write("." * node.level)
        if node.module:
            self.write(node.module)
        self.write(" import ")
        self.interleave(lambda: self.write(", "), self.traverse, node.names)

    def visit_Assign(self, node):
        self.fill()
        for target in node.targets:
            self.traverse(target)
            self.write(" = ")
        self.traverse(node.value)

    def visit_AugAssign(self, node):
        self.fill()
        self.traverse(node.target)
        self.write(" " + self.binop[node.op.__class__.__name__] + "= ")
        self.traverse(node.value)

    def visit_AnnAssign(self, node):
        self.fill()
        with self.delimit_if("(", ")", not node.simple and isinstance(node.target, ast.Name)):
            self.traverse(node.target)
        self.write(": ")
        self.traverse(node.annotation)
        if node.value:
            self.write(" = ")
            self.traverse(node.value)

    def visit_Return(self, node):
        self.fill("return")
        if node.value:
            self.write(" ")
            self.traverse(node.value)

    def visit_Pass(self, node):
        self.fill("pass")

    def visit_Break(self, node):
        self.fill("break")

    def visit_Continue(self, node):
        self.fill("continue")

    def visit_Delete(self, node):
        self.fill("del ")
        self.interleave(lambda: self.write(", "), self.traverse, node.targets)

    def visit_Assert(self, node):
        self.fill("assert ")
        self.traverse(node.test)
        if node.msg:
            self.write(", ")
            self.traverse(node.msg)

    def visit_Global(self, node):
        self.fill("global ")
        self.interleave(lambda: self.write(", "), self.write, node.names)

    def visit_Nonlocal(self, node):
        self.fill("nonlocal ")
        self.interleave(lambda: self.write(", "), self.write, node.names)

    def visit_Await(self, node):
        with self.require_parens(_Precedence.AWAIT, node):
            self.write("await")
            if node.value:
                self.write(" ")
                self.set_precedence(_Precedence.ATOM, node.value)
                self.traverse(node.value)

    def visit_Yield(self, node):
        with self.require_parens(_Precedence.YIELD, node):
            self.write("yield")
            if node.value:
                self.write(" ")
                self.set_precedence(_Precedence.ATOM, node.value)
                self.traverse(node.value)

    def visit_YieldFrom(self, node):
        with self.require_parens(_Precedence.YIELD, node):
            self.write("yield from")
            if node.value:
                self.write(" ")
                self.set_precedence(_Precedence.ATOM, node.value)
                self.traverse(node.value)

    def visit_Raise(self, node):
        self.fill("raise")
        if not node.exc:
            assert not node.cause
            return
        self.write(" ")
        self.traverse(node.exc)
        if node.cause:
            self.write(" from ")
            self.traverse(node.cause)

    def visit_Try(self, node):
        self.fill("try")
        with self.block():
            self.traverse(node.body)
        for ex in node.handlers:
            self.traverse(ex)
        if node.orelse:
            self.fill("else")
            with self.block():
                self.traverse(node.orelse)
        if node.finalbody:
            self.fill("finally")
            with self.block():
                self.traverse(node.finalbody)

    def visit_ExceptHandler(self, node):
        self.fill("except")
        if node.type:
            self.write(" ")
            self.traverse(node.type)
        if node.name:
            self.write(" as ")
            self.write(node.name)
        with self.block():
            self.traverse(node.body)

    def visit_ClassDef(self, node):
        self.write("\n")
        for deco in node.decorator_list:
            self.fill("@")
            self.traverse(deco)
        self.fill("class " + node.name)
        if getattr(node, "type_params", False):
            self.write("[")
            self.interleave(lambda: self.write(", "), self.traverse, node.type_params)
            self.write("]")
        with self.delimit_if("(", ")", condition=node.bases or node.keywords):
            comma = False
            for e in node.bases:
                if comma:
                    self.write(", ")
                else:
                    comma = True
                self.traverse(e)
            for e in node.keywords:
                if comma:
                    self.write(", ")
                else:
                    comma = True
                self.traverse(e)
        with self.block():
            self.traverse(node.body)

    def visit_FunctionDef(self, node):
        self.__FunctionDef_helper(node, "def")

    def visit_AsyncFunctionDef(self, node):
        self.__FunctionDef_helper(node, "async def")

    def __FunctionDef_helper(self, node, fill_suffix):
        self.write("\n")
        for deco in node.decorator_list:
            self.fill("@")
            self.traverse(deco)
        def_str = fill_suffix + " " + node.name
        self.fill(def_str)
        if getattr(node, "type_params", False):
            self.write("[")
            self.interleave(lambda: self.write(", "), self.traverse, node.type_params)
            self.write("]")
        with self.delimit("(", ")"):
            self.traverse(node.args)
        if getattr(node, "returns", False):
            self.write(" -> ")
            self.traverse(node.returns)
        with self.block():
            self.traverse(node.body)

    def visit_For(self, node):
        self.__For_helper("for ", node)

    def visit_AsyncFor(self, node):
        self.__For_helper("async for ", node)

    def __For_helper(self, fill, node):
        self.fill(fill)
        self.traverse(node.target)
        self.write(" in ")
        self.traverse(node.iter)
        with self.block():
            self.traverse(node.body)
        if node.orelse:
            self.fill("else")
            with self.block():
                self.traverse(node.orelse)

    def visit_If(self, node):
        self.fill("if ")
        self.traverse(node.test)
        with self.block():
            self.traverse(node.body)
        # collapse nested ifs into equivalent elifs.
        while node.orelse and len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
            node = node.orelse[0]
            self.fill("elif ")
            self.traverse(node.test)
            with self.block():
                self.traverse(node.body)
        # final else
        if node.orelse:
            self.fill("else")
            with self.block():
                self.traverse(node.orelse)

    def visit_While(self, node):
        self.fill("while ")
        self.traverse(node.test)
        with self.block():
            self.traverse(node.body)
        if node.orelse:
            self.fill("else")
            with self.block():
                self.traverse(node.orelse)

    def _generic_With(self, node, async_=False):
        self.fill("async with " if async_ else "with ")
        if hasattr(node, "items"):
            self.interleave(lambda: self.write(", "), self.traverse, node.items)
        else:
            self.traverse(node.context_expr)
            if node.optional_vars:
                self.write(" as ")
                self.traverse(node.optional_vars)
        with self.block():
            self.traverse(node.body)

    def visit_With(self, node):
        self._generic_With(node)

    def visit_AsyncWith(self, node):
        self._generic_With(node, async_=True)

    def _str_literal_helper(
        self, string, quote_types=_ALL_QUOTES, escape_special_whitespace=False
    ):
        """Helper for writing string literals, minimizing escapes.
        Returns the tuple (string literal to write, possible quote types).
        """

        def escape_char(c):
            # \n and \t are non-printable, but we only escape them if
            # escape_special_whitespace is True
            if not escape_special_whitespace and c in "\n\t":
                return c
            # Always escape backslashes and other non-printable characters
            if c == "\\" or not c.isprintable():
                return c.encode("unicode_escape").decode("ascii")
            return c

        escaped_string = "".join(map(escape_char, string))
        possible_quotes = quote_types
        if "\n" in escaped_string:
            possible_quotes = [q for q in possible_quotes if q in _MULTI_QUOTES]
        possible_quotes = [q for q in possible_quotes if q not in escaped_string]
        if not possible_quotes:
            # If there aren't any possible_quotes, fallback to using repr
            # on the original string. Try to use a quote from quote_types,
            # e.g., so that we use triple quotes for docstrings.
            string = repr(string)
            quote = next((q for q in quote_types if string[0] in q), string[0])
            return string[1:-1], [quote]
        if escaped_string:
            # Sort so that we prefer '''"''' over """\""""
            possible_quotes.sort(key=lambda q: q[0] == escaped_string[-1])
            # If we're using triple quotes and we'd need to escape a final
            # quote, escape it
            if possible_quotes[0][0] == escaped_string[-1]:
                assert len(possible_quotes[0]) == 3
                escaped_string = escaped_string[:-1] + "\\" + escaped_string[-1]
        return escaped_string, possible_quotes

    def _write_str_avoiding_backslashes(self, string, quote_types=_ALL_QUOTES):
        """Write string literal value w/a best effort attempt to avoid backslashes."""
        string, quote_types = self._str_literal_helper(string, quote_types=quote_types)
        quote_type = quote_types[0]
        self.write("{quote_type}{string}{quote_type}".format(quote_type=quote_type, string=string))

    # expr
    def visit_Bytes(self, node):
        self.write(repr(node.s))

    def visit_Str(self, tree):
        # Python 3.5, 3.6, and 3.7 can't tell if something was written as a
        # unicode constant. Try to make that consistent with 'u' for '\u- literals
        if self._py_ver_consistent and repr(tree.s).startswith("'\\u"):
            self.write("u")
        self._write_constant(tree.s)

    def visit_JoinedStr(self, node):
        # JoinedStr(expr* values)
        self.write("f")

        if self._avoid_backslashes:
            string = StringIO()
            self._fstring_JoinedStr(node, string.write)
            self._write_str_avoiding_backslashes(string.getvalue())
            return

        # If we don't need to avoid backslashes globally (i.e., we only need
        # to avoid them inside FormattedValues), it's cosmetically preferred
        # to use escaped whitespace. That is, it's preferred to use backslashes
        # for cases like: f"{x}\n". To accomplish this, we keep track of what
        # in our buffer corresponds to FormattedValues and what corresponds to
        # Constant parts of the f-string, and allow escapes accordingly.
        buffer = []
        for value in node.values:
            meth = getattr(self, "_fstring_" + type(value).__name__)
            string = StringIO()
            meth(value, string.write)
            buffer.append((string.getvalue(), isinstance(value, ast.Constant)))
        new_buffer = []
        quote_types = _ALL_QUOTES
        for value, is_constant in buffer:
            # Repeatedly narrow down the list of possible quote_types
            value, quote_types = self._str_literal_helper(
                value, quote_types=quote_types, escape_special_whitespace=is_constant
            )
            new_buffer.append(value)
        value = "".join(new_buffer)
        quote_type = quote_types[0]
        self.write("{quote_type}{value}{quote_type}".format(quote_type=quote_type, value=value))

    def visit_FormattedValue(self, node):
        # FormattedValue(expr value, int? conversion, expr? format_spec)
        self.write("f")
        string = StringIO()
        self._fstring_JoinedStr(node, string.write)
        self._write_str_avoiding_backslashes(string.getvalue())

    def _fstring_JoinedStr(self, node, write):
        for value in node.values:
            meth = getattr(self, "_fstring_" + type(value).__name__)
            meth(value, write)

    def _fstring_Str(self, node, write):
        value = node.s.replace("{", "{{").replace("}", "}}")
        write(value)

    def _fstring_Constant(self, node, write):
        assert isinstance(node.value, str)
        value = node.value.replace("{", "{{").replace("}", "}}")
        write(value)

    def _fstring_FormattedValue(self, node, write):
        write("{")

        expr = StringIO()
        unparser = type(self)(py_ver_consistent=self._py_ver_consistent, _avoid_backslashes=True)
        unparser.set_precedence(_Precedence.TEST.next(), node.value)
        unparser.visit(node.value, expr)
        expr = expr.getvalue().rstrip("\n")

        if expr.startswith("{"):
            write(" ")  # Separate pair of opening brackets as "{ {"
        if "\\" in expr:
            raise ValueError("Unable to avoid backslash in f-string expression part")
        write(expr)
        if node.conversion != -1:
            conversion = chr(node.conversion)
            assert conversion in "sra"
            write("!{conversion}".format(conversion=conversion))
        if node.format_spec:
            write(":")
            meth = getattr(self, "_fstring_" + type(node.format_spec).__name__)
            meth(node.format_spec, write)
        write("}")

    def visit_Name(self, node):
        self.write(node.id)

    def visit_NameConstant(self, node):
        self.write(repr(node.value))

    def _write_constant(self, value):
        if isinstance(value, (float, complex)):
            # Substitute overflowing decimal literal for AST infinities.
            self.write(repr(value).replace("inf", _INFSTR))
        elif isinstance(value, str) and self._py_ver_consistent:
            # emulate a python 2 repr with raw unicode escapes
            # see _Str for python 2 counterpart
            raw = repr(value.encode("raw_unicode_escape")).lstrip("b")
            if raw.startswith(r"'\\u"):
                raw = "'\\" + raw[3:]
            self.write(raw)
        elif self._avoid_backslashes and isinstance(value, str):
            self._write_str_avoiding_backslashes(value)
        else:
            self.write(repr(value))

    def visit_Constant(self, node):
        value = node.value
        if isinstance(value, tuple):
            with self.delimit("(", ")"):
                self.items_view(self._write_constant, value)
        elif value is Ellipsis:  # instead of `...` for Py2 compatibility
            self.write("...")
        else:
            if node.kind == "u":
                self.write("u")
            self._write_constant(node.value)

    def visit_Num(self, node):
        repr_n = repr(node.n)
        self.write(repr_n.replace("inf", _INFSTR))

    def visit_List(self, node):
        with self.delimit("[", "]"):
            self.interleave(lambda: self.write(", "), self.traverse, node.elts)

    def visit_ListComp(self, node):
        with self.delimit("[", "]"):
            self.traverse(node.elt)
            for gen in node.generators:
                self.traverse(gen)

    def visit_GeneratorExp(self, node):
        with self.delimit("(", ")"):
            self.traverse(node.elt)
            for gen in node.generators:
                self.traverse(gen)

    def visit_SetComp(self, node):
        with self.delimit("{", "}"):
            self.traverse(node.elt)
            for gen in node.generators:
                self.traverse(gen)

    def visit_DictComp(self, node):
        with self.delimit("{", "}"):
            self.traverse(node.key)
            self.write(": ")
            self.traverse(node.value)
            for gen in node.generators:
                self.traverse(gen)

    def visit_comprehension(self, node):
        if getattr(node, "is_async", False):
            self.write(" async for ")
        else:
            self.write(" for ")
        self.set_precedence(_Precedence.TUPLE, node.target)
        self.traverse(node.target)
        self.write(" in ")
        self.set_precedence(_Precedence.TEST.next(), node.iter, *node.ifs)
        self.traverse(node.iter)
        for if_clause in node.ifs:
            self.write(" if ")
            self.traverse(if_clause)

    def visit_IfExp(self, node):
        with self.require_parens(_Precedence.TEST, node):
            self.set_precedence(_Precedence.TEST.next(), node.body, node.test)
            self.traverse(node.body)
            self.write(" if ")
            self.traverse(node.test)
            self.write(" else ")
            self.set_precedence(_Precedence.TEST, node.orelse)
            self.traverse(node.orelse)

    def visit_Set(self, node):
        assert node.elts  # should be at least one element
        with self.delimit("{", "}"):
            self.interleave(lambda: self.write(", "), self.traverse, node.elts)

    def visit_Dict(self, node):
        def write_key_value_pair(k, v):
            self.traverse(k)
            self.write(": ")
            self.traverse(v)

        def write_item(item):
            k, v = item
            if k is None:
                # for dictionary unpacking operator in dicts {**{'y': 2}}
                # see PEP 448 for details
                self.write("**")
                self.set_precedence(_Precedence.EXPR, v)
                self.traverse(v)
            else:
                write_key_value_pair(k, v)

        with self.delimit("{", "}"):
            self.interleave(lambda: self.write(", "), write_item, zip(node.keys, node.values))

    def visit_Tuple(self, node):
        with self.delimit("(", ")"):
            self.items_view(self.traverse, node.elts)

    unop = {"Invert": "~", "Not": "not", "UAdd": "+", "USub": "-"}

    unop_precedence = {
        "~": _Precedence.FACTOR,
        "not": _Precedence.NOT,
        "+": _Precedence.FACTOR,
        "-": _Precedence.FACTOR,
    }

    def visit_UnaryOp(self, node):
        operator = self.unop[node.op.__class__.__name__]
        operator_precedence = self.unop_precedence[operator]
        with self.require_parens(operator_precedence, node):
            self.write(operator)
            # factor prefixes (+, -, ~) shouldn't be separated
            # from the value they belong, (e.g: +1 instead of + 1)
            if operator_precedence != _Precedence.FACTOR:
                self.write(" ")
            self.set_precedence(operator_precedence, node.operand)
            self.traverse(node.operand)

    binop = {
        "Add": "+",
        "Sub": "-",
        "Mult": "*",
        "MatMult": "@",
        "Div": "/",
        "Mod": "%",
        "LShift": "<<",
        "RShift": ">>",
        "BitOr": "|",
        "BitXor": "^",
        "BitAnd": "&",
        "FloorDiv": "//",
        "Pow": "**",
    }

    binop_precedence = {
        "+": _Precedence.ARITH,
        "-": _Precedence.ARITH,
        "*": _Precedence.TERM,
        "@": _Precedence.TERM,
        "/": _Precedence.TERM,
        "%": _Precedence.TERM,
        "<<": _Precedence.SHIFT,
        ">>": _Precedence.SHIFT,
        "|": _Precedence.BOR,
        "^": _Precedence.BXOR,
        "&": _Precedence.BAND,
        "//": _Precedence.TERM,
        "**": _Precedence.POWER,
    }

    binop_rassoc = frozenset(("**",))

    def visit_BinOp(self, node):
        operator = self.binop[node.op.__class__.__name__]
        operator_precedence = self.binop_precedence[operator]
        with self.require_parens(operator_precedence, node):
            if operator in self.binop_rassoc:
                left_precedence = operator_precedence.next()
                right_precedence = operator_precedence
            else:
                left_precedence = operator_precedence
                right_precedence = operator_precedence.next()

            self.set_precedence(left_precedence, node.left)
            self.traverse(node.left)
            self.write(" %s " % operator)
            self.set_precedence(right_precedence, node.right)
            self.traverse(node.right)

    cmpops = {
        "Eq": "==",
        "NotEq": "!=",
        "Lt": "<",
        "LtE": "<=",
        "Gt": ">",
        "GtE": ">=",
        "Is": "is",
        "IsNot": "is not",
        "In": "in",
        "NotIn": "not in",
    }

    def visit_Compare(self, node):
        with self.require_parens(_Precedence.CMP, node):
            self.set_precedence(_Precedence.CMP.next(), node.left, *node.comparators)
            self.traverse(node.left)
            for o, e in zip(node.ops, node.comparators):
                self.write(" " + self.cmpops[o.__class__.__name__] + " ")
                self.traverse(e)

    boolops = {"And": "and", "Or": "or"}

    boolop_precedence = {"and": _Precedence.AND, "or": _Precedence.OR}

    def visit_BoolOp(self, node):
        operator = self.boolops[node.op.__class__.__name__]

        # use a dict instead of nonlocal for Python 2 compatibility
        op = {"precedence": self.boolop_precedence[operator]}

        def increasing_level_dispatch(node):
            op["precedence"] = op["precedence"].next()
            self.set_precedence(op["precedence"], node)
            self.traverse(node)

        with self.require_parens(op["precedence"], node):
            s = " %s " % operator
            self.interleave(lambda: self.write(s), increasing_level_dispatch, node.values)

    def visit_Attribute(self, node):
        self.set_precedence(_Precedence.ATOM, node.value)
        self.traverse(node.value)
        # Special case: 3.__abs__() is a syntax error, so if node.value
        # is an integer literal then we need to either parenthesize
        # it or add an extra space to get 3 .__abs__().
        num_type = getattr(ast, "Constant", getattr(ast, "Num", None))
        if isinstance(node.value, num_type) and isinstance(node.value.n, int):
            self.write(" ")
        self.write(".")
        self.write(node.attr)

    def visit_Call(self, node):
        self.set_precedence(_Precedence.ATOM, node.func)

        args = node.args
        self.traverse(node.func)

        with self.delimit("(", ")"):
            comma = False

            # NOTE: this code is no longer compatible with python versions 2.7:3.4
            # If you run on python@:3.4, you will see instability in package hashes
            # across python versions

            for e in args:
                if comma:
                    self.write(", ")
                else:
                    comma = True
                self.traverse(e)

            for e in node.keywords:
                if comma:
                    self.write(", ")
                else:
                    comma = True
                self.traverse(e)

    def visit_Subscript(self, node):
        self.set_precedence(_Precedence.ATOM, node.value)
        self.traverse(node.value)
        with self.delimit("[", "]"):
            if is_simple_tuple(node.slice):
                self.items_view(self.traverse, node.slice.elts)
            else:
                self.traverse(node.slice)

    def visit_Starred(self, node):
        self.write("*")
        self.set_precedence(_Precedence.EXPR, node.value)
        self.traverse(node.value)

    # slice
    def visit_Ellipsis(self, node):
        self.write("...")

    # used in Python <= 3.8 -- see _Subscript for 3.9+
    def visit_Index(self, node):
        if is_simple_tuple(node.value):
            self.set_precedence(_Precedence.ATOM, node.value)
            self.items_view(self.traverse, node.value.elts)
        else:
            self.set_precedence(_Precedence.TUPLE, node.value)
            self.traverse(node.value)

    def visit_Slice(self, node):
        if node.lower:
            self.traverse(node.lower)
        self.write(":")
        if node.upper:
            self.traverse(node.upper)
        if node.step:
            self.write(":")
            self.traverse(node.step)

    def visit_ExtSlice(self, node):
        self.interleave(lambda: self.write(", "), self.traverse, node.dims)

    # argument
    def visit_arg(self, node):
        self.write(node.arg)
        if node.annotation:
            self.write(": ")
            self.traverse(node.annotation)

    # others
    def visit_arguments(self, node):
        first = True
        # normal arguments
        all_args = getattr(node, "posonlyargs", []) + node.args
        defaults = [None] * (len(all_args) - len(node.defaults)) + node.defaults
        for index, elements in enumerate(zip(all_args, defaults), 1):
            a, d = elements
            if first:
                first = False
            else:
                self.write(", ")
            self.traverse(a)
            if d:
                self.write("=")
                self.traverse(d)
            if index == len(getattr(node, "posonlyargs", ())):
                self.write(", /")

        # varargs, or bare '*' if no varargs but keyword-only arguments present
        if node.vararg or getattr(node, "kwonlyargs", False):
            if first:
                first = False
            else:
                self.write(", ")
            self.write("*")
            if node.vararg:
                self.write(node.vararg.arg)
                if node.vararg.annotation:
                    self.write(": ")
                    self.traverse(node.vararg.annotation)

        # keyword-only arguments
        if getattr(node, "kwonlyargs", False):
            for a, d in zip(node.kwonlyargs, node.kw_defaults):
                if first:
                    first = False
                else:
                    self.write(", ")
                self.traverse(a),
                if d:
                    self.write("=")
                    self.traverse(d)

        # kwargs
        if node.kwarg:
            if first:
                first = False
            else:
                self.write(", ")
            self.write("**" + node.kwarg.arg)
            if node.kwarg.annotation:
                self.write(": ")
                self.traverse(node.kwarg.annotation)

    def visit_keyword(self, node):
        if node.arg is None:
            # starting from Python 3.5 this denotes a kwargs part of the invocation
            self.write("**")
        else:
            self.write(node.arg)
            self.write("=")
        self.traverse(node.value)

    def visit_Lambda(self, node):
        with self.require_parens(_Precedence.TEST, node):
            self.write("lambda ")
            self.traverse(node.args)
            self.write(": ")
            self.set_precedence(_Precedence.TEST, node.body)
            self.traverse(node.body)

    def visit_alias(self, node):
        self.write(node.name)
        if node.asname:
            self.write(" as " + node.asname)

    def visit_withitem(self, node):
        self.traverse(node.context_expr)
        if node.optional_vars:
            self.write(" as ")
            self.traverse(node.optional_vars)

    def visit_Match(self, node):
        self.fill("match ")
        self.traverse(node.subject)
        with self.block():
            for case in node.cases:
                self.traverse(case)

    def visit_match_case(self, node):
        self.fill("case ")
        self.traverse(node.pattern)
        if node.guard:
            self.write(" if ")
            self.traverse(node.guard)
        with self.block():
            self.traverse(node.body)

    def visit_MatchValue(self, node):
        self.traverse(node.value)

    def visit_MatchSingleton(self, node):
        self._write_constant(node.value)

    def visit_MatchSequence(self, node):
        with self.delimit("[", "]"):
            self.interleave(lambda: self.write(", "), self.traverse, node.patterns)

    def visit_MatchStar(self, node):
        name = node.name
        if name is None:
            name = "_"
        self.write("*{}".format(name))

    def visit_MatchMapping(self, node):
        def write_key_pattern_pair(pair):
            k, p = pair
            self.traverse(k)
            self.write(": ")
            self.traverse(p)

        with self.delimit("{", "}"):
            keys = node.keys
            self.interleave(
                lambda: self.write(", "), write_key_pattern_pair, zip(keys, node.patterns)
            )
            rest = node.rest
            if rest is not None:
                if keys:
                    self.write(", ")
                self.write("**{}".format(rest))

    def visit_MatchClass(self, node):
        self.set_precedence(_Precedence.ATOM, node.cls)
        self.traverse(node.cls)
        with self.delimit("(", ")"):
            patterns = node.patterns
            self.interleave(lambda: self.write(", "), self.traverse, patterns)
            attrs = node.kwd_attrs
            if attrs:

                def write_attr_pattern(pair):
                    attr, pattern = pair
                    self.write("{}=".format(attr))
                    self.traverse(pattern)

                if patterns:
                    self.write(", ")
                self.interleave(
                    lambda: self.write(", "), write_attr_pattern, zip(attrs, node.kwd_patterns)
                )

    def visit_MatchAs(self, node):
        name = node.name
        pattern = node.pattern
        if name is None:
            self.write("_")
        elif pattern is None:
            self.write(node.name)
        else:
            with self.require_parens(_Precedence.TEST, node):
                self.set_precedence(_Precedence.BOR, node.pattern)
                self.traverse(node.pattern)
                self.write(" as {}".format(node.name))

    def visit_MatchOr(self, node):
        with self.require_parens(_Precedence.BOR, node):
            self.set_precedence(_Precedence.BOR.next(), *node.patterns)
            self.interleave(lambda: self.write(" | "), self.traverse, node.patterns)

    def visit_TypeAlias(self, node):
        self.fill("type ")
        self.traverse(node.name)
        if node.type_params:
            self.write("[")
            self.interleave(lambda: self.write(", "), self.traverse, node.type_params)
            self.write("]")
        self.write(" = ")
        self.traverse(node.value)

    def visit_TypeVar(self, node):
        self.write(node.name)
        if node.bound:
            self.write(": ")
            self.traverse(node.bound)

    def visit_TypeVarTuple(self, node):
        self.write("*")
        self.write(node.name)

    def visit_ParamSpec(self, node):
        self.write("**")
        self.write(node.name)
