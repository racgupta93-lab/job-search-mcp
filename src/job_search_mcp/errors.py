"""Errors whose message is written for the user and is safe to show.

Anything that is NOT a UserError is treated as an unexpected crash: the tool layer
shows only a generic message, so internal details (paths, response text) never leak.
"""


class UserError(Exception):
    """A deliberate, user-readable error (bad id, conflicting filters, ...)."""
