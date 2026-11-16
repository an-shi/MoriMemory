"""MoriBot-specific MoriMemory integration helpers.

These modules are copied into MoriBot's source tree (or imported from a local
checkout). They intentionally depend only on FastAPI, httpx and loguru, which
MoriBot already ships.
"""

from .proxy import create_memory_proxy_router
from .service_manager import (
    MemoryServiceManager,
    MemoryServiceSettings,
    MemoryServiceStatus,
)

__all__ = [
    "MemoryServiceManager",
    "MemoryServiceSettings",
    "MemoryServiceStatus",
    "create_memory_proxy_router",
]
