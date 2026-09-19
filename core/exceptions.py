class NetBoxHubError(Exception):
    """Base exception for NetBox Hub application."""
    pass

class AIProviderError(NetBoxHubError):
    """Raised when OmniRoute/OpenRouter encounters an error."""
    pass

class GitHubCatalogError(NetBoxHubError):
    """Raised when GitHub devicetype repository catalog fails to index."""
    pass