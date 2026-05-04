"""Sanity test that the package imports cleanly.

Replaced by real tests as the modules fill in.
"""

from __future__ import annotations


def test_package_importable() -> None:
    import uacp_prototype

    assert uacp_prototype.__version__ == "0.1.0"
