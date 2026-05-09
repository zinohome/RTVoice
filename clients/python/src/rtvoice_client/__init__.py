"""rtvoice-client: official Python client for RTVoice platform."""
__version__ = "0.1.0"

__all__ = ["__version__", "Client", "AsyncClient"]


def __getattr__(name: str):
    """Lazy import to avoid circular issues during package build."""
    if name == "Client":
        from rtvoice_client._base import Client
        return Client
    if name == "AsyncClient":
        from rtvoice_client._base import AsyncClient
        return AsyncClient
    raise AttributeError(f"module 'rtvoice_client' has no attribute {name!r}")
