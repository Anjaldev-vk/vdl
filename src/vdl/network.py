"""Network and proxy configuration for vdl."""

from __future__ import annotations

import logging
import socket
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from rich.console import Console

from vdl.exceptions import NetworkError

if TYPE_CHECKING:
    from vdl.config import Settings

logger = logging.getLogger(__name__)
console = Console(stderr=True)

DEFAULT_TOR_PROXY = "socks5h://127.0.0.1:9050"
TOR_ANONYMITY_NOTICE = (
    "[yellow]Notice:[/] Tor mode routes traffic through SOCKS5. This reduces IP exposure "
    "to the content host, but does [bold]not[/bold] guarantee total anonymity or prevent "
    "browser fingerprinting/traffic analysis."
)


def parse_proxy_host_port(proxy_url: str) -> tuple[str, int]:
    """Parse host and port from a proxy URL. Defaults to 9050 if port omitted."""
    parsed = urlparse(proxy_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 9050
    return host, port


def is_proxy_reachable(proxy_url: str, timeout: float = 2.0) -> bool:
    """Test TCP socket reachability to the proxy host:port with a short timeout."""
    try:
        host, port = parse_proxy_host_port(proxy_url)
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (TimeoutError, OSError) as exc:
        logger.debug("Proxy connection to %s failed: %s", proxy_url, exc)
        return False


check_tor_reachable = is_proxy_reachable


def setup_network(
    settings: Settings | str,
    network_override: str | None = None,
    tor_proxy: str | None = None,
) -> str | None:
    """Configure network mode from Settings and optional CLI override.

    Fails closed: if network mode is 'tor' and the proxy is unreachable,
    aborts by raising NetworkError without falling back to a direct connection.

    Prints active network mode and one-line Tor privacy notice once per invocation.

    Returns:
        The proxy URL string for yt-dlp ('socks5h://...' or None for direct).
    """
    if hasattr(settings, "network_mode"):
        if network_override:
            settings.network_mode = network_override
        mode = settings.network_mode
        proxy_url = tor_proxy or settings.tor_proxy
    else:
        if network_override and (
            network_override.startswith("socks") or network_override.startswith("http")
        ):
            proxy_url = network_override
            mode = str(settings)
        else:
            mode = network_override or str(settings)
            proxy_url = tor_proxy or DEFAULT_TOR_PROXY

    if mode == "tor":
        console.print("[cyan][Network][/cyan] Mode: [bold yellow]tor[/bold yellow]")
        console.print(TOR_ANONYMITY_NOTICE)

        # Fail closed check
        if not is_proxy_reachable(proxy_url):
            host, port = parse_proxy_host_port(proxy_url)
            raise NetworkError(
                f"Tor proxy at {proxy_url} ({host}:{port}) is unreachable. "
                "Tor mode must fail closed to protect privacy. Aborting operation."
            )
        return proxy_url

    # Direct mode
    console.print("[cyan][Network][/cyan] Mode: [bold green]direct[/bold green]")
    return None
