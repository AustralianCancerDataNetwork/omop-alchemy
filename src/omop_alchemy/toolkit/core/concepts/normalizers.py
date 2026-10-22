from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .lookup import Normaliser

def compose_normalizers(*fns: "Normaliser") -> "Normaliser":
    def _inner(s: str) -> str:
        for fn in fns:
            s = fn(s)
        return s
    return _inner

"""
This module contains core normalisation functions for concept lookups.

Custom normalisers can be passed to LookupSpec instances to perform normalisation 
on input values before lookup, and if similar functions are needed in multiple places,
they can be added here and composed as needed.
"""

def normalize_default(s: str) -> str:
    return s.strip().lower()

def strip_uicc(code: str) -> str:
    return code.lower().replace('ajcc', 'ajcc/uicc')

def make_stage(val: str) -> str:
    val = val.lower()
    roman_lookup = [('-iii', '-3'), ('-iv', '-4'), ('-ii', '-2'), ('-i', '-1'), ('nos', '')]
    for replacement in roman_lookup:
        val = val.replace(*replacement)
    return val

def site_to_NOS(icdo_topog: str) -> str:
    split_topog = icdo_topog.split('.')
    if '.' not in icdo_topog:
        return f'{icdo_topog}.9'
    # a couple of codes have a third decimal point?
    elif len(split_topog[-1]) > 2:
        return ''.join(split_topog[:-1] + ['.', split_topog[-1][:2]])
    return icdo_topog
