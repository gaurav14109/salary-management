"""The HTTP layer: validate, authorize, call a service, serialize.

Routes hold no rules. They decide nothing about money, dates or eligibility - if a
test here asserts a business outcome rather than a status code or a shape, the rule
is in the wrong place.
"""
