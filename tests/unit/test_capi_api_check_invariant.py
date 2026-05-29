"""AST-level invariant: every public ``OpenLayersApi`` method calls ``check(...)``.

This is the docs/design.md §17.4 "single ``check`` seam" invariant.
If a future PR adds a new SDK-wrapping method to
:class:`~dtollib.capi.api.OpenLayersApi` without routing through
:func:`dtollib.capi.errors.check`, this test fails *before* the
bypass reaches runtime.

The test walks the source of each public method and asserts the
method body contains at least one ``Call`` node whose function is a
``Name`` of ``"check"``.  Helpers like
:meth:`OpenLayersApi.dlls` (a read-only property) are exempt — only
methods that call into the SDK need the gate.
"""

from __future__ import annotations

import ast
import inspect
import itertools
import textwrap

import pytest

from dtollib.capi.api import (
    OpenLayersApi,
    continuous_method_names,
    counter_method_names,
    discovery_method_names,
    multi_sensor_method_names,
    output_method_names,
    single_value_method_names,
)


def _method_source(name: str) -> str:
    method = getattr(OpenLayersApi, name)
    return textwrap.dedent(inspect.getsource(method))


def _contains_check_call(source: str) -> bool:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "check":
                return True
    return False


_ALL_METHODS = list(
    itertools.chain(
        discovery_method_names(),
        single_value_method_names(),
        continuous_method_names(),
        output_method_names(),
        counter_method_names(),
        multi_sensor_method_names(),
    )
)


@pytest.mark.parametrize("method_name", _ALL_METHODS)
def test_every_method_calls_check(method_name: str) -> None:
    """Each public ``OpenLayersApi`` method routes through ``check``."""
    src = _method_source(method_name)
    assert _contains_check_call(src), (
        f"OpenLayersApi.{method_name} does not call ``check`` — this is the "
        f"docs/design.md §17.4 invariant; add a ``check(self._dlls, status, ...)`` "
        f"call or document the exemption."
    )
