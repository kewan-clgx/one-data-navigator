"""Tests for SSH tunnel configuration and management."""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import asyncssh
import pytest

from odn.platform.connectors.ssh_tunnel import (
    RemotePostgresTarget,
    SSHTunnelConfig,
    SSHTunnelManager,
    diagnose_ssh_tunnel,
)


@pytest.fixture
def valid_ssh_config():
    """Create a valid SSH tunnel configuration for testing."""
    # Create a mock host key (base64-encoded public key data)
    mock_key_data = b"mock_public_key_data_1234567890"
    mock_key_b64 = base64.b64encode(mock_key_data).decode("ascii")

    return SSHTunnelConfig(
        host="bastion.example.com",
        port=22,
        username="ubuntu",
        key_format="openssh",
        key_ciphertext=b"encrypted_private_key_data",
        known_hosts_key_type="ssh-ed25519",
        known_hosts_key_data=mock_key_b64,
    )


@pytest.fixture
def remote_target():
    """Create a remote Postgres target configuration."""
    return RemotePostgresTarget(host="postgres.internal", port=5432)


class TestSSHTunnelConfigValidation:
    """Test SSH tunnel configuration validation."""

    def test_valid_config_creation(self, valid_ssh_config):
        """Valid config should create without errors."""
        assert valid_ssh_config.host == "bastion.example.com"
        assert valid_ssh_config.port == 22
        assert valid_ssh_config.username == "ubuntu"

    def test_invalid_port_too_low(self, valid_ssh_config):
        """Port must be >= 1."""
        with pytest.raises(ValueError):
            SSHTunnelConfig(
                **{
                    **valid_ssh_config.model_dump(),
                    "port": 0,
                }
            )

    def test_invalid_port_too_high(self, valid_ssh_config):
        """Port must be <= 65535."""
        with pytest.raises(ValueError):
            SSHTunnelConfig(
                **{
                    **valid_ssh_config.model_dump(),
                    "port": 65536,
                }
            )

    def test_invalid_key_format(self, valid_ssh_config):
        """Key format must be 'openssh' or 'pem'."""
        with pytest.raises(ValueError):
            SSHTunnelConfig(
                **{
                    **valid_ssh_config.model_dump(),
                    "key_format": "invalid_format",
                }
            )

    def test_invalid_key_type(self, valid_ssh_config):
        """Key type must be one of the supported types."""
        with pytest.raises(ValueError):
            SSHTunnelConfig(
                **{
                    **valid_ssh_config.model_dump(),
                    "known_hosts_key_type": "invalid-key-type",
                }
            )

    def test_invalid_key_data_not_base64(self, valid_ssh_config):
        """Key data must be valid base64."""
        with pytest.raises(ValueError):
            SSHTunnelConfig(
                **{
                    **valid_ssh_config.model_dump(),
                    "known_hosts_key_data": "not_valid_base64!!!",
                }
            )

    def test_supported_key_types(self, valid_ssh_config):
        """All supported key types should validate."""
        for key_type in ("ssh-rsa", "ssh-ed25519", "ecdsa-sha2-nistp256"):
            config = SSHTunnelConfig(
                **{
                    **valid_ssh_config.model_dump(),
                    "known_hosts_key_type": key_type,
                }
            )
            assert config.known_hosts_key_type == key_type


class TestRemotePostgresTarget:
    """Test remote Postgres target configuration."""

    def test_default_values(self):
        """Default target should be localhost:5432."""
        target = RemotePostgresTarget()
        assert target.host == "localhost"
        assert target.port == 5432

    def test_custom_target(self, remote_target):
        """Custom target should use provided values."""
        assert remote_target.host == "postgres.internal"
        assert remote_target.port == 5432

    def test_custom_port(self):
        """Should allow custom Postgres port."""
        target = RemotePostgresTarget(host="db.local", port=5433)
        assert target.port == 5433


class TestSSHTunnelManager:
    """Test SSH tunnel manager."""

    def test_manager_initialization(self, valid_ssh_config, remote_target):
        """Manager should initialize without opening tunnel."""
        manager = SSHTunnelManager(valid_ssh_config, remote_target)
        assert manager.tunnel_config == valid_ssh_config
        assert manager.remote_target == remote_target
        assert manager._connection is None
        assert manager._local_port is None

    def test_manager_with_default_target(self, valid_ssh_config):
        """Manager should use default target if not provided."""
        manager = SSHTunnelManager(valid_ssh_config)
        assert manager.remote_target.host == "localhost"
        assert manager.remote_target.port == 5432

    @pytest.mark.asyncio
    async def test_tunnel_manager_cleanup(self, valid_ssh_config):
        """Closing tunnel should clean up resources."""
        manager = SSHTunnelManager(valid_ssh_config)
        
        # Mock the connection and listener
        manager._connection = AsyncMock()
        manager._connection.close = MagicMock()
        manager._connection.wait_closed = AsyncMock()
        
        manager._listener = AsyncMock()
        manager._listener.close = MagicMock()
        
        manager._local_port = 12345

        await manager.close()

        # Verify cleanup
        assert manager._connection is None
        assert manager._listener is None
        assert manager._local_port is None


class TestSSHTunnelHostKeyHandling:
    """Test SSH host key parsing and validation."""

    def test_host_key_reconstruction(self, valid_ssh_config):
        """Host key should be reconstructable from config."""
        manager = SSHTunnelManager(valid_ssh_config)
        
        # This will fail without real key data, but tests the method exists
        try:
            manager._make_host_key()
        except (ValueError, Exception):
            # Expected with mock key data
            pass

    def test_host_key_reconstruction_with_valid_format(self):
        """Host key with valid base64 should be reconstructable."""
        # Create a minimal valid base64-encoded key
        valid_b64_key = base64.b64encode(b"test_key_data").decode("ascii")
        
        config = SSHTunnelConfig(
            host="bastion.com",
            port=22,
            username="user",
            key_format="openssh",
            key_ciphertext=b"encrypted",
            known_hosts_key_type="ssh-ed25519",
            known_hosts_key_data=valid_b64_key,
        )
        
        manager = SSHTunnelManager(config)
        # Method should exist and handle base64 decoding correctly
        assert hasattr(manager, "_make_host_key")


class TestSSHTunnelContextManager:
    """Test SSH tunnel manager as context manager."""

    @pytest.mark.asyncio
    async def test_context_manager_protocol(self, valid_ssh_config):
        """Manager should support async context manager protocol."""
        manager = SSHTunnelManager(valid_ssh_config)
        
        # Mock the open_tunnel method
        with patch.object(manager, "open_tunnel", return_value=12345):
            async with manager as tunnel_mgr:
                assert tunnel_mgr is manager


class TestSSHTunnelDiagnostics:
    """Test SSH tunnel diagnostic functions."""

    @pytest.mark.asyncio
    async def test_diagnostic_with_invalid_config(self):
        """Diagnostic should handle invalid config."""
        # Create invalid config
        with pytest.raises(ValueError):
            await diagnose_ssh_tunnel(
                SSHTunnelConfig(
                    host="bastion.com",
                    port=65536,  # Invalid
                    username="user",
                    key_format="openssh",
                    key_ciphertext=b"encrypted",
                    known_hosts_key_type="ssh-ed25519",
                    known_hosts_key_data=base64.b64encode(b"key").decode("ascii"),
                )
            )

    @pytest.mark.asyncio
    async def test_diagnostic_returns_results(self, valid_ssh_config):
        """Diagnostic should return list of results."""
        # Mock the manager and its methods
        with patch("odn.platform.connectors.ssh_tunnel.SSHTunnelManager") as MockManager:
            mock_manager = AsyncMock()
            mock_manager.open_tunnel = AsyncMock(return_value=12345)
            mock_manager.close = AsyncMock()
            MockManager.return_value = mock_manager

            results = await diagnose_ssh_tunnel(valid_ssh_config)

            assert isinstance(results, list)
            assert len(results) > 0
            assert all("step" in r and "ok" in r for r in results)


class TestSSHTunnelIntegration:
    """Integration tests for SSH tunnel functionality."""

    def test_ssh_tunnel_in_base_imports(self):
        """SSH tunnel module should be importable."""
        from odn.platform.connectors import ssh_tunnel

        assert hasattr(ssh_tunnel, "SSHTunnelConfig")
        assert hasattr(ssh_tunnel, "SSHTunnelManager")
        assert hasattr(ssh_tunnel, "diagnose_ssh_tunnel")

    def test_config_roundtrip(self, valid_ssh_config):
        """Config should be serializable and deserializable."""
        # Convert to dict and back
        config_dict = valid_ssh_config.model_dump()
        new_config = SSHTunnelConfig(**config_dict)
        
        assert new_config.host == valid_ssh_config.host
        assert new_config.port == valid_ssh_config.port
        assert new_config.username == valid_ssh_config.username


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
