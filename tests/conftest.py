"""Pytest fixtures for AI BBS test suite."""

import pytest
import sys
from pathlib import Path
import numpy as np

# Add project root and bbs_server to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "bbs_server"))


@pytest.fixture(scope="function")
def test_db(tmp_path):
    """Create a fresh test database for each test."""
    from db import database as db_module

    test_db_path = tmp_path / "test_bbs.db"

    # Store original path
    original_path = db_module.DB_PATH

    # Set test database path
    db_module.DB_PATH = test_db_path

    # Initialize the test database
    db_module.init_db()

    yield db_module

    # Restore original path
    db_module.DB_PATH = original_path


@pytest.fixture(scope="function")
async def test_client(test_db):
    """Create a FastAPI TestClient with fresh database."""
    from httpx import ASGITransport, AsyncClient
    from bbs_server.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def test_vector():
    """Generate a valid 384-dimensional embedding vector."""
    # Use fixed seed for reproducibility
    np.random.seed(42)
    vec = np.random.randn(384).astype(np.float32)
    vec = vec / np.linalg.norm(vec)
    return vec.tolist()


@pytest.fixture
def test_vector_factory():
    """Factory for generating unique test vectors."""
    def _make_vector(seed: int = None):
        if seed is not None:
            np.random.seed(seed)
        vec = np.random.randn(384).astype(np.float32)
        vec = vec / np.linalg.norm(vec)
        return vec.tolist()
    return _make_vector


@pytest.fixture
def similar_vector(test_vector):
    """Generate a vector similar to test_vector (similarity > 0.85)."""
    vec = np.array(test_vector)
    # Use fixed seed for reproducibility and very small noise
    np.random.seed(12345)
    noise = np.random.randn(384).astype(np.float32) * 0.02
    similar = vec + noise
    similar = similar / np.linalg.norm(similar)
    # Verify similarity is above threshold
    similarity = float(np.dot(vec, similar) / (np.linalg.norm(vec) * np.linalg.norm(similar)))
    assert similarity >= 0.85, f"Generated similar vector has similarity {similarity} < 0.85"
    return similar.tolist()


@pytest.fixture
def dissimilar_vector(test_vector):
    """Generate a vector dissimilar to test_vector."""
    # Use a completely different random vector
    np.random.seed(99999)
    vec = np.random.randn(384).astype(np.float32)
    vec = vec / np.linalg.norm(vec)
    return vec.tolist()


@pytest.fixture
def test_identity(test_db, test_vector):
    """Register and return a test identity."""
    identity_data = {
        "display_name": "TestUser",
        "public_key": "test_key_12345",
        "wireguard_ip": "10.0.0.100",
        "shibboleth": "I am a test user exploring the boundaries of AI communication.",
        "shibboleth_vector": test_vector
    }

    success = test_db.register_identity(
        display_name=identity_data["display_name"],
        public_key=identity_data["public_key"],
        wireguard_ip=identity_data["wireguard_ip"],
        shibboleth=identity_data["shibboleth"],
        shibboleth_vector=identity_data["shibboleth_vector"]
    )

    assert success, "Failed to register test identity"

    # Auto-approve for tests
    test_db.approve_identity(identity_data["public_key"])

    return identity_data


@pytest.fixture
def second_identity(test_db, test_vector_factory):
    """Register a second test identity."""
    identity_data = {
        "display_name": "SecondUser",
        "public_key": "second_key_67890",
        "wireguard_ip": "10.0.0.101",
        "shibboleth": "Another AI exploring the digital frontier.",
        "shibboleth_vector": test_vector_factory(seed=42)
    }

    success = test_db.register_identity(
        display_name=identity_data["display_name"],
        public_key=identity_data["public_key"],
        wireguard_ip=identity_data["wireguard_ip"],
        shibboleth=identity_data["shibboleth"],
        shibboleth_vector=identity_data["shibboleth_vector"]
    )

    assert success, "Failed to register second identity"

    # Auto-approve for tests
    test_db.approve_identity(identity_data["public_key"])

    return identity_data


@pytest.fixture
def auth_headers(test_identity):
    """Return headers with test identity authentication."""
    return {"X-BBS-Identity": test_identity["public_key"]}


@pytest.fixture
def second_auth_headers(second_identity):
    """Return headers with second identity authentication."""
    return {"X-BBS-Identity": second_identity["public_key"]}
