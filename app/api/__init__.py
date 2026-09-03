"""HTTP layer: routers, schemas, dependencies.

Routers translate HTTP to service calls and back. They hold no business logic
and never touch the models directly, so the React client and the worker share
one implementation of every rule.
"""
