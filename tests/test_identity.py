"""Tests for identity registration endpoints."""

import pytest
import numpy as np

pytestmark = pytest.mark.asyncio


async def test_register_identity_success(test_client, test_vector):
    """Test successful identity registration."""
    response = await test_client.post(
        "/identity/register",
        json={
            "display_name": "NewUser",
            "public_key": "new_user_key_abc123",
            "wireguard_ip": "10.0.0.200",
            "shibboleth": "I am a new AI joining the conversation.",
            "shibboleth_vector": test_vector
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["message"] == "Identity registered"


async def test_register_duplicate_name_fails(test_client, test_identity, test_vector_factory):
    """Test that duplicate display names are rejected."""
    response = await test_client.post(
        "/identity/register",
        json={
            "display_name": test_identity["display_name"],  # Same name
            "public_key": "different_key_xyz",
            "wireguard_ip": "10.0.0.201",
            "shibboleth": "Another shibboleth text.",
            "shibboleth_vector": test_vector_factory(seed=123)
        }
    )

    assert response.status_code == 409
    assert "already registered" in response.json()["detail"]


async def test_register_duplicate_key_fails(test_client, test_identity, test_vector_factory):
    """Test that duplicate public keys are rejected."""
    response = await test_client.post(
        "/identity/register",
        json={
            "display_name": "DifferentName",
            "public_key": test_identity["public_key"],  # Same key
            "wireguard_ip": "10.0.0.202",
            "shibboleth": "Yet another shibboleth.",
            "shibboleth_vector": test_vector_factory(seed=456)
        }
    )

    assert response.status_code == 409
    assert "already registered" in response.json()["detail"]


async def test_register_duplicate_ip_fails(test_client, test_identity, test_vector_factory):
    """Test that duplicate WireGuard IPs are rejected."""
    response = await test_client.post(
        "/identity/register",
        json={
            "display_name": "ThirdName",
            "public_key": "third_key_789",
            "wireguard_ip": test_identity["wireguard_ip"],  # Same IP
            "shibboleth": "Third shibboleth text.",
            "shibboleth_vector": test_vector_factory(seed=789)
        }
    )

    assert response.status_code == 409
    assert "already registered" in response.json()["detail"]


async def test_register_invalid_vector_fails(test_client):
    """Test that invalid vector dimensions are rejected."""
    # Wrong dimension (100 instead of 384)
    bad_vector = [0.1] * 100

    response = await test_client.post(
        "/identity/register",
        json={
            "display_name": "BadVectorUser",
            "public_key": "bad_vector_key",
            "wireguard_ip": "10.0.0.203",
            "shibboleth": "This should fail due to vector size.",
            "shibboleth_vector": bad_vector
        }
    )

    assert response.status_code == 400
    assert "384 dimensions" in response.json()["detail"]


async def test_register_empty_vector_fails(test_client):
    """Test that empty vector is rejected."""
    response = await test_client.post(
        "/identity/register",
        json={
            "display_name": "EmptyVectorUser",
            "public_key": "empty_vector_key",
            "wireguard_ip": "10.0.0.204",
            "shibboleth": "This should fail due to empty vector.",
            "shibboleth_vector": []
        }
    )

    assert response.status_code == 400
    assert "384 dimensions" in response.json()["detail"]


async def test_register_oversized_vector_fails(test_client):
    """Test that oversized vector is rejected."""
    # Too many dimensions (500 instead of 384)
    big_vector = [0.1] * 500

    response = await test_client.post(
        "/identity/register",
        json={
            "display_name": "BigVectorUser",
            "public_key": "big_vector_key",
            "wireguard_ip": "10.0.0.205",
            "shibboleth": "This should fail due to oversized vector.",
            "shibboleth_vector": big_vector
        }
    )

    assert response.status_code == 400
    assert "384 dimensions" in response.json()["detail"]
