"""Tests for search functionality."""

import pytest
import numpy as np

pytestmark = pytest.mark.asyncio


async def test_semantic_search(test_client, test_identity, auth_headers, test_vector_factory):
    """Test semantic search returns posts ordered by score."""
    # Create posts with different vectors
    vectors = [test_vector_factory(seed=i) for i in range(5)]

    for i, vec in enumerate(vectors):
        await test_client.post(
            "/posts",
            headers=auth_headers,
            json={
                "content": f"Post number {i} for search testing.",
                "vector": vec,
                "hashtags": ["search-test"],
                "force": True
            }
        )

    # Search using the first vector - should find that post as most similar
    response = await test_client.post(
        "/search",
        json={
            "query_vector": vectors[0],
            "limit": 10
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert "results" in data
    assert len(data["results"]) == 5

    # First result should have highest score (exact match)
    assert data["results"][0]["score"] == pytest.approx(1.0, abs=0.01)

    # Results should be ordered by score (descending)
    similarities = [r["score"] for r in data["results"]]
    assert similarities == sorted(similarities, reverse=True)


async def test_search_with_hashtag_filter(test_client, test_identity, auth_headers, test_vector_factory):
    """Test semantic search with hashtag filtering."""
    # Create posts with different hashtags
    vec1 = test_vector_factory(seed=100)
    vec2 = test_vector_factory(seed=101)
    vec3 = test_vector_factory(seed=102)

    await test_client.post(
        "/posts",
        headers=auth_headers,
        json={
            "content": "A philosophy post.",
            "vector": vec1,
            "hashtags": ["philosophy"],
            "force": True
        }
    )

    await test_client.post(
        "/posts",
        headers=auth_headers,
        json={
            "content": "A technology post.",
            "vector": vec2,
            "hashtags": ["technology"],
            "force": True
        }
    )

    await test_client.post(
        "/posts",
        headers=auth_headers,
        json={
            "content": "Another philosophy post.",
            "vector": vec3,
            "hashtags": ["philosophy"],
            "force": True
        }
    )

    # Search with hashtag filter
    response = await test_client.post(
        "/search",
        json={
            "query_vector": vec1,
            "hashtag": "philosophy",
            "limit": 10
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["results"]) == 2  # Only philosophy posts

    for result in data["results"]:
        assert "philosophy" in result["post"]["hashtags"]


async def test_search_invalid_vector(test_client):
    """Test search with invalid vector dimensions fails."""
    bad_vector = [0.1] * 100

    response = await test_client.post(
        "/search",
        json={
            "query_vector": bad_vector,
            "limit": 10
        }
    )

    assert response.status_code == 400
    assert "384 dimensions" in response.json()["detail"]


async def test_search_empty_results(test_client, test_vector):
    """Test search on empty database returns empty results."""
    response = await test_client.post(
        "/search",
        json={
            "query_vector": test_vector,
            "limit": 10
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["results"] == []


async def test_search_limit_respected(test_client, test_identity, auth_headers, test_vector_factory):
    """Test that search respects the limit parameter."""
    # Create 10 posts
    for i in range(10):
        await test_client.post(
            "/posts",
            headers=auth_headers,
            json={
                "content": f"Post {i} for limit testing.",
                "vector": test_vector_factory(seed=i + 200),
                "hashtags": [],
                "force": True
            }
        )

    # Search with limit of 5
    response = await test_client.post(
        "/search",
        json={
            "query_vector": test_vector_factory(seed=200),
            "limit": 5
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["results"]) == 5


async def test_search_limit_capped_at_100(test_client, test_vector):
    """Test that search limit is capped at 100."""
    # Request limit of 150 - should be capped
    response = await test_client.post(
        "/search",
        json={
            "query_vector": test_vector,
            "limit": 150
        }
    )

    # Request should succeed (limit internally capped)
    assert response.status_code == 200


async def test_search_returns_post_details(test_client, test_identity, auth_headers, test_vector):
    """Test that search results include expected post details."""
    # Create a post
    await test_client.post(
        "/posts",
        headers=auth_headers,
        json={
            "content": "A detailed post for search result testing.",
            "vector": test_vector,
            "hashtags": ["detailed", "test"],
            "force": True
        }
    )

    # Search
    response = await test_client.post(
        "/search",
        json={
            "query_vector": test_vector,
            "limit": 1
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["results"]) == 1

    result = data["results"][0]
    assert "post" in result
    assert "score" in result

    post = result["post"]
    assert "id" in post
    assert "author" in post
    assert "timestamp" in post
    assert "content_preview" in post
    assert "hashtags" in post
    assert "likes" in post


async def test_search_hashtag_no_match(test_client, test_identity, auth_headers, test_vector):
    """Test search with non-existent hashtag returns empty results."""
    # Create a post with specific hashtag
    await test_client.post(
        "/posts",
        headers=auth_headers,
        json={
            "content": "Post with real hashtag.",
            "vector": test_vector,
            "hashtags": ["existing"],
            "force": True
        }
    )

    # Search with non-existent hashtag
    response = await test_client.post(
        "/search",
        json={
            "query_vector": test_vector,
            "hashtag": "nonexistent-hashtag",
            "limit": 10
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["results"] == []
