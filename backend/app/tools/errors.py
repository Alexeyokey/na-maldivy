"""Shared domain errors; unsupported routes remain ordinary ValueErrors."""


class UnsupportedTransfer(ValueError):
    """The transfer source does not support the requested route or query."""
