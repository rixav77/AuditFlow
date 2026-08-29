"""Router registry: import submodules so main.py can mount them."""

from api.routers import batches, chat, exceptions, transactions  # noqa: F401
