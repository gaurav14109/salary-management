"""Operational scripts. Not imported by the application.

Kept inside the backend package rather than in devops/ because these import the
application's own models and services - a generator that built rows by hand would
be a second, divergent definition of what a valid row looks like.
"""
