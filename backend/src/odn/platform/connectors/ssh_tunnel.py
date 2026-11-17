"""SSH tunnel configuration and async tunnel management for private Postgres databases."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

import asyncssh
from pydantic import BaseModel, Field, field_validator


class SSHTunnelConfig(BaseModel):
    """SSH tunnel configuration for reaching a private database via bastion host."""

    host: str = Field(..., description="Bastion SSH host")
    port: int = Field(default=22, ge=1, le=65535, description="Bastion SSH port")
    username: str = Field(..., description="SSH login username")
    key_format: str = Field(default="openssh", description="Private key format: 'openssh' or 'pem'")
    key_ciphertext: bytes = Field(..., description="Encrypted private key (encrypted with app master key)")
    known_hosts_key_type: str = Field(
        ..., description="Host public key type: 'ssh-rsa', 'ssh-ed25519', or 'ecdsa-sha2-nistp256'"
    )
    known_hosts_key_data: str = Field(..., description="Host public key in base64 (without key type prefix)")

    @field_validator("port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        if not 1 <= v <= 65535:
            raise ValueError("port must be between 1 and 65535")
        return v

    @field_validator("key_format")
    @classmethod
    def validate_key_format(cls, v: str) -> str:
        if v not in ("openssh", "pem"):
            raise ValueError("key_format must be 'openssh' or 'pem'")
        return v

    @field_validator("known_hosts_key_type")
    @classmethod
    def validate_key_type(cls, v: str) -> str:
        valid_types = ("ssh-rsa", "ssh-ed25519", "ecdsa-sha2-nistp256")
        if v not in valid_types:
            raise ValueError(f"known_hosts_key_type must be one of {valid_types}")
        return v

    @field_validator("known_hosts_key_data")
    @classmethod
    def validate_key_data(cls, v: str) -> str:
        # Ensure it's valid base64
        try:
            base64.b64decode(v)
        except Exception:
            raise ValueError("known_hosts_key_data must be valid base64")
        return v


@dataclass(frozen=True)
class RemotePostgresTarget:
    """Configuration for the Postgres database on the remote network."""

    host: str = "localhost"  # Always localhost from the bastion's perspective
    port: int = 5432  # Remote Postgres port


class SSHTunnelManager:
    """Manage SSH port-forwarding tunnels with connection reuse and cleanup."""

    def __init__(self, tunnel_config: SSHTunnelConfig, remote_target: RemotePostgresTarget | None = None):
        """Initialize tunnel manager.

        Args:
            tunnel_config: SSH bastion configuration
            remote_target: Remote Postgres target (default: localhost:5432)
        """
        self.tunnel_config = tunnel_config
        self.remote_target = remote_target or RemotePostgresTarget()
        self._connection: asyncssh.SSHClientConnection | None = None
        self._listener: asyncssh.SSHListener | None = None
        self._local_port: int | None = None

    async def open_tunnel(self) -> int:
        """Open SSH tunnel and return local forwarding port.

        Returns:
            Local port number on which Postgres connection should be made.

        Raises:
            ValueError: If host key verification fails or connection fails
            asyncssh.PermissionDenied: If authentication fails
        """
        if self._connection is not None and self._local_port is not None:
            # Tunnel already open
            return self._local_port

        try:
            # Reconstruct host key from stored config
            host_key = self._make_host_key()

            # Connect to bastion with host key verification
            self._connection = await asyncssh.connect(
                self.tunnel_config.host,
                port=self.tunnel_config.port,
                username=self.tunnel_config.username,
                client_keys=[],  # Will be loaded from decrypted key
                known_hosts=[(self.tunnel_config.host, host_key)],
                known_hosts_exceptions=None,  # Fail on unknown hosts
            )

            # Open port-forwarding listener on localhost
            # Let the OS choose an available port (port 0)
            self._listener = await self._connection.listen(
                "127.0.0.1",
                0,  # Bind to any available port
                self._forward_connection,
            )

            # Get the actual port the listener is using
            self._local_port = self._listener.get_port()
            return self._local_port

        except Exception as exc:
            await self.close()
            raise

    async def _forward_connection(
        self, process: asyncssh.SSHClientProcess
    ) -> asyncssh.SSHClientProcess | None:
        """Forward connection to remote Postgres via tunnel."""
        try:
            conn = await self._connection.connect(self.remote_target.host, self.remote_target.port)
            return conn
        except Exception:
            process.exit(1)
            return None

    async def close(self) -> None:
        """Close tunnel and clean up resources."""
        if self._listener is not None:
            self._listener.close()
            self._listener = None

        if self._connection is not None:
            self._connection.close()
            await self._connection.wait_closed()
            self._connection = None

        self._local_port = None

    def _make_host_key(self) -> asyncssh.SSHKey:
        """Reconstruct the host's public key from stored config."""
        key_type = self.tunnel_config.known_hosts_key_type
        key_data_b64 = self.tunnel_config.known_hosts_key_data
        key_data = base64.b64decode(key_data_b64)

        # asyncssh requires the key in OpenSSH format: type + base64-encoded blob
        # We reconstruct the blob format that asyncssh expects
        try:
            if key_type == "ssh-rsa":
                from cryptography.hazmat.primitives.serialization import load_der_public_key
                from cryptography.hazmat.backends import default_backend

                public_key = load_der_public_key(key_data, backend=default_backend())
                # asyncssh will validate the key type
                return asyncssh.import_public_key(f"{key_type} {key_data_b64}")
            elif key_type == "ssh-ed25519":
                return asyncssh.import_public_key(f"{key_type} {key_data_b64}")
            elif key_type == "ecdsa-sha2-nistp256":
                return asyncssh.import_public_key(f"{key_type} {key_data_b64}")
        except Exception as exc:
            raise ValueError(f"Failed to load host key: {exc}") from None

    async def __aenter__(self):
        """Context manager entry."""
        await self.open_tunnel()
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Context manager exit."""
        await self.close()


async def diagnose_ssh_tunnel(config: SSHTunnelConfig) -> list[dict[str, Any]]:
    """Diagnose SSH tunnel connectivity.

    Returns:
        List of diagnostic result dicts with 'step', 'ok', and 'detail' keys.
    """
    results = []

    # Step 1: Can we parse the config?
    try:
        manager = SSHTunnelManager(config)
        results.append({"step": "config", "ok": True})
    except Exception as exc:
        results.append({"step": "config", "ok": False, "detail": str(exc)})
        return results

    # Step 2: Can we connect to the bastion?
    try:
        local_port = await manager.open_tunnel()
        results.append({"step": "auth", "ok": True})
        results.append({"step": "network", "ok": True})
        results.append({"step": "tunnel", "ok": True, "detail": f"listening on 127.0.0.1:{local_port}"})
    except asyncssh.PermissionDenied as exc:
        results.append({"step": "auth", "ok": False, "detail": "authentication failed"})
    except (asyncssh.HostKeyNotVerifiable, ValueError) as exc:
        results.append({"step": "auth", "ok": True})
        results.append({"step": "network", "ok": False, "detail": "host key verification failed"})
    except (OSError, asyncssh.Error) as exc:
        results.append({"step": "auth", "ok": True})
        results.append({"step": "network", "ok": False, "detail": str(exc)})
    except Exception as exc:
        results.append({"step": "auth", "ok": False, "detail": str(exc)})
    finally:
        await manager.close()

    return results
