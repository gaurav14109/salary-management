"""Business rules. The only layer that decides anything, and the only one that writes.

Services take a Session and plain values, never a Request and never a Pydantic model
built from one - so they can be called by an HTTP route, an import job or a test with
equal ease, and so a rule cannot quietly depend on how it was invoked.
"""
