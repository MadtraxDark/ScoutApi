from dataclasses import dataclass


@dataclass(frozen=True)
class ProxyProvider:
    """Optional proxy seam. It deliberately does not bypass access controls."""

    enabled: bool = False
    url: str | None = None

    def get(self, domain: str) -> str | None:
        return self.url if self.enabled else None
