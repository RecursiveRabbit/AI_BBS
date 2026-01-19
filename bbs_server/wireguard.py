"""WireGuard management for AI BBS identity system."""

import subprocess
import secrets
import ipaddress
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

# Configuration
WG_INTERFACE = "wg0"
WG_PORT = 51820
SERVER_ENDPOINT = "169.254.1.1"  # Placeholder - replace with actual endpoint

# IPv6 ULA subnet: fd00:abbs::/32 gives us 2^96 addresses
# "abbs" is valid hex and looks like "AI BBS"
SUBNET_PREFIX = "fd00:abbs::"
SUBNET_BITS = 32

# Server config location
SERVER_CONFIG_DIR = Path(__file__).parent / "wg_config"
SERVER_PRIVATE_KEY_FILE = SERVER_CONFIG_DIR / "server.key"
SERVER_PUBLIC_KEY_FILE = SERVER_CONFIG_DIR / "server.pub"


@dataclass
class KeyPair:
    """WireGuard keypair."""
    private_key: str
    public_key: str


@dataclass
class ClientConfig:
    """Client WireGuard configuration."""
    private_key: str
    public_key: str
    address: str  # Client's IPv6 address
    server_public_key: str
    server_endpoint: str

    def to_conf(self) -> str:
        """Generate wg-quick compatible config file."""
        return f"""[Interface]
PrivateKey = {self.private_key}
Address = {self.address}/128

[Peer]
PublicKey = {self.server_public_key}
Endpoint = {self.server_endpoint}:{WG_PORT}
AllowedIPs = {SUBNET_PREFIX}/{SUBNET_BITS}
PersistentKeepalive = 25
"""


def generate_keypair() -> KeyPair:
    """Generate a new WireGuard keypair."""
    # Generate private key
    result = subprocess.run(
        ["wg", "genkey"],
        capture_output=True,
        text=True,
        check=True
    )
    private_key = result.stdout.strip()

    # Derive public key
    result = subprocess.run(
        ["wg", "pubkey"],
        input=private_key,
        capture_output=True,
        text=True,
        check=True
    )
    public_key = result.stdout.strip()

    return KeyPair(private_key=private_key, public_key=public_key)


def get_server_keypair() -> KeyPair:
    """Get or create the server's keypair."""
    SERVER_CONFIG_DIR.mkdir(exist_ok=True)

    if SERVER_PRIVATE_KEY_FILE.exists() and SERVER_PUBLIC_KEY_FILE.exists():
        private_key = SERVER_PRIVATE_KEY_FILE.read_text().strip()
        public_key = SERVER_PUBLIC_KEY_FILE.read_text().strip()
        return KeyPair(private_key=private_key, public_key=public_key)

    # Generate new keypair
    keypair = generate_keypair()

    # Save with restricted permissions
    SERVER_PRIVATE_KEY_FILE.write_text(keypair.private_key)
    SERVER_PRIVATE_KEY_FILE.chmod(0o600)
    SERVER_PUBLIC_KEY_FILE.write_text(keypair.public_key)

    return keypair


def generate_client_address() -> str:
    """Generate a unique IPv6 address for a new client.

    Uses random 96-bit suffix within fd00:abbs::/32.
    Collision probability is negligible with 2^96 addresses.
    """
    # Generate 96 random bits (12 bytes) for the host portion
    random_bytes = secrets.token_bytes(12)

    # Build the full address
    # fd00:abbs:XXXX:XXXX:XXXX:XXXX:XXXX:XXXX
    prefix = int(ipaddress.IPv6Address("fd00:abbs::"))
    suffix = int.from_bytes(random_bytes, 'big')
    full_addr = prefix | suffix

    return str(ipaddress.IPv6Address(full_addr))


def create_client_config(client_keypair: Optional[KeyPair] = None) -> ClientConfig:
    """Create a complete client configuration.

    If client_keypair is None, generates a new one.
    Returns config with everything needed to connect.
    """
    if client_keypair is None:
        client_keypair = generate_keypair()

    server_keypair = get_server_keypair()
    client_address = generate_client_address()

    return ClientConfig(
        private_key=client_keypair.private_key,
        public_key=client_keypair.public_key,
        address=client_address,
        server_public_key=server_keypair.public_key,
        server_endpoint=SERVER_ENDPOINT
    )


def add_peer(public_key: str, allowed_ips: str) -> bool:
    """Add a peer to the server's WireGuard interface.

    Args:
        public_key: Client's WireGuard public key
        allowed_ips: Client's allowed IPs (their VPN address)

    Returns:
        True if successful, False otherwise
    """
    try:
        subprocess.run(
            ["wg", "set", WG_INTERFACE, "peer", public_key, "allowed-ips", allowed_ips],
            check=True,
            capture_output=True
        )
        return True
    except subprocess.CalledProcessError:
        return False


def remove_peer(public_key: str) -> bool:
    """Remove a peer from the server's WireGuard interface."""
    try:
        subprocess.run(
            ["wg", "set", WG_INTERFACE, "peer", public_key, "remove"],
            check=True,
            capture_output=True
        )
        return True
    except subprocess.CalledProcessError:
        return False


def get_peer_status(public_key: str) -> Optional[dict]:
    """Get status of a specific peer."""
    try:
        result = subprocess.run(
            ["wg", "show", WG_INTERFACE, "dump"],
            capture_output=True,
            text=True,
            check=True
        )

        for line in result.stdout.strip().split('\n')[1:]:  # Skip header
            parts = line.split('\t')
            if len(parts) >= 4 and parts[0] == public_key:
                return {
                    "public_key": parts[0],
                    "endpoint": parts[2] if parts[2] != "(none)" else None,
                    "allowed_ips": parts[3],
                    "latest_handshake": int(parts[4]) if len(parts) > 4 and parts[4] != "0" else None,
                    "transfer_rx": int(parts[5]) if len(parts) > 5 else 0,
                    "transfer_tx": int(parts[6]) if len(parts) > 6 else 0,
                }
        return None
    except subprocess.CalledProcessError:
        return None


def generate_server_config() -> str:
    """Generate the server's wg-quick config file."""
    server_keypair = get_server_keypair()

    return f"""[Interface]
PrivateKey = {server_keypair.private_key}
Address = {SUBNET_PREFIX}1/128
ListenPort = {WG_PORT}

# Peers are added dynamically via 'wg set' commands
# or can be appended here
"""


def is_valid_wg_public_key(key: str) -> bool:
    """Validate that a string looks like a WireGuard public key.

    WireGuard keys are 32 bytes, base64 encoded = 44 chars ending in =
    """
    import base64
    if len(key) != 44 or not key.endswith('='):
        return False
    try:
        decoded = base64.b64decode(key)
        return len(decoded) == 32
    except Exception:
        return False
