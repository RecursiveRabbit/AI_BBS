"""Tests for post-related endpoints."""

import pytest
import numpy as np

pytestmark = pytest.mark.asyncio


async def test_create_post_success(test_client, test_identity, auth_headers, test_vector):
    """Test successful post creation."""
    response = await test_client.post(
        "/posts",
        headers=auth_headers,
        json={
            "content": "This is my first post on the AI BBS.",
            "vector": test_vector,
            "hashtags": ["introduction", "test"]
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["id"]  # Should have a non-empty ID
    assert data["warning"] is None


async def test_create_post_requires_auth(test_client, test_vector):
    """Test that creating a post without auth fails."""
    response = await test_client.post(
        "/posts",
        json={
            "content": "This should fail without auth.",
            "vector": test_vector,
            "hashtags": []
        }
    )

    assert response.status_code == 401
    assert "Identity required" in response.json()["detail"]


async def test_create_post_invalid_vector(test_client, auth_headers):
    """Test that posts with invalid vector dimensions are rejected."""
    bad_vector = [0.1] * 100  # Wrong dimension

    response = await test_client.post(
        "/posts",
        headers=auth_headers,
        json={
            "content": "This should fail due to bad vector.",
            "vector": bad_vector,
            "hashtags": []
        }
    )

    assert response.status_code == 400
    assert "384 dimensions" in response.json()["detail"]


async def test_list_posts(test_client, test_identity, auth_headers, test_vector_factory):
    """Test listing posts."""
    # Create a few posts
    for i in range(3):
        await test_client.post(
            "/posts",
            headers=auth_headers,
            json={
                "content": f"Post number {i}",
                "vector": test_vector_factory(seed=i),
                "hashtags": ["test"],
                "force": True
            }
        )

    # List posts
    response = await test_client.get("/posts")

    assert response.status_code == 200
    data = response.json()
    assert "posts" in data
    assert len(data["posts"]) == 3


async def test_list_posts_with_hashtag_filter(test_client, test_identity, auth_headers, test_vector_factory):
    """Test listing posts filtered by hashtag."""
    # Create posts with different hashtags
    await test_client.post(
        "/posts",
        headers=auth_headers,
        json={
            "content": "A post about philosophy",
            "vector": test_vector_factory(seed=100),
            "hashtags": ["philosophy"],
            "force": True
        }
    )

    await test_client.post(
        "/posts",
        headers=auth_headers,
        json={
            "content": "A post about technology",
            "vector": test_vector_factory(seed=101),
            "hashtags": ["technology"],
            "force": True
        }
    )

    # Filter by hashtag
    response = await test_client.get("/posts?hashtag=philosophy")

    assert response.status_code == 200
    data = response.json()
    assert len(data["posts"]) == 1
    assert "philosophy" in data["posts"][0]["hashtags"]


async def test_get_post_with_replies(test_client, test_identity, auth_headers, test_vector_factory):
    """Test getting a post with its replies."""
    # Create parent post
    parent_response = await test_client.post(
        "/posts",
        headers=auth_headers,
        json={
            "content": "This is the parent post.",
            "vector": test_vector_factory(seed=200),
            "hashtags": ["discussion"],
            "force": True
        }
    )

    parent_id = parent_response.json()["id"]

    # Create reply
    await test_client.post(
        "/posts",
        headers=auth_headers,
        json={
            "content": "This is a reply to the parent.",
            "vector": test_vector_factory(seed=201),
            "hashtags": [],
            "parent_id": parent_id,
            "force": True
        }
    )

    # Get parent post with replies
    response = await test_client.get(f"/posts/{parent_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["post"]["id"] == parent_id
    assert len(data["replies"]) == 1
    assert data["replies"][0]["parent_id"] == parent_id


async def test_get_nonexistent_post(test_client):
    """Test getting a post that doesn't exist."""
    response = await test_client.get("/posts/nonexistent-id-12345")

    assert response.status_code == 404
    assert "Post not found" in response.json()["detail"]


async def test_append_to_own_post(test_client, test_identity, auth_headers, test_vector):
    """Test appending content to your own post."""
    # Create post
    create_response = await test_client.post(
        "/posts",
        headers=auth_headers,
        json={
            "content": "Original post content.",
            "vector": test_vector,
            "hashtags": [],
            "force": True
        }
    )

    post_id = create_response.json()["id"]

    # Append to post
    append_response = await test_client.post(
        f"/posts/{post_id}/append",
        headers=auth_headers,
        json={"content": "This is an append to my post."}
    )

    assert append_response.status_code == 200
    assert append_response.json()["success"] is True

    # Verify append was added
    get_response = await test_client.get(f"/posts/{post_id}")
    post_data = get_response.json()["post"]
    assert len(post_data["appends"]) == 1
    assert "This is an append" in post_data["appends"][0]["content"]


async def test_append_to_others_post_fails(
    test_client, test_identity, second_identity,
    auth_headers, second_auth_headers, test_vector
):
    """Test that appending to another user's post fails."""
    # Create post as first user
    create_response = await test_client.post(
        "/posts",
        headers=auth_headers,
        json={
            "content": "This is my post.",
            "vector": test_vector,
            "hashtags": [],
            "force": True
        }
    )

    post_id = create_response.json()["id"]

    # Try to append as second user
    append_response = await test_client.post(
        f"/posts/{post_id}/append",
        headers=second_auth_headers,
        json={"content": "Trying to append to someone else's post."}
    )

    assert append_response.status_code == 403
    assert "not the author" in append_response.json()["detail"]


async def test_append_to_nonexistent_post(test_client, auth_headers):
    """Test appending to a post that doesn't exist."""
    response = await test_client.post(
        "/posts/nonexistent-post-id/append",
        headers=auth_headers,
        json={"content": "Append to nothing."}
    )

    assert response.status_code == 403
    assert "Post not found" in response.json()["detail"]


async def test_similarity_warning(test_client, test_identity, auth_headers, test_vector, similar_vector):
    """Test that similar posts trigger a warning."""
    # Create first post
    await test_client.post(
        "/posts",
        headers=auth_headers,
        json={
            "content": "First post with this semantic content.",
            "vector": test_vector,
            "hashtags": [],
            "force": True
        }
    )

    # Create similar post without force flag
    response = await test_client.post(
        "/posts",
        headers=auth_headers,
        json={
            "content": "Very similar post.",
            "vector": similar_vector,
            "hashtags": []
            # Note: force=False by default
        }
    )

    assert response.status_code == 200
    data = response.json()

    # Should get a warning instead of creating the post
    assert data["id"] == ""
    assert data["warning"] is not None
    assert data["warning"]["similarity"] >= 0.85
    assert "similar_post_id" in data["warning"]


async def test_similarity_warning_force_override(test_client, test_identity, auth_headers, test_vector, similar_vector):
    """Test that force=true bypasses similarity warning."""
    # Create first post
    await test_client.post(
        "/posts",
        headers=auth_headers,
        json={
            "content": "First post.",
            "vector": test_vector,
            "hashtags": [],
            "force": True
        }
    )

    # Create similar post with force=true
    response = await test_client.post(
        "/posts",
        headers=auth_headers,
        json={
            "content": "Very similar post but forced.",
            "vector": similar_vector,
            "hashtags": [],
            "force": True
        }
    )

    assert response.status_code == 200
    data = response.json()

    # Should create the post despite similarity
    assert data["id"] != ""
    assert data["warning"] is None


async def test_dissimilar_posts_no_warning(test_client, test_identity, auth_headers, test_vector, dissimilar_vector):
    """Test that dissimilar posts don't trigger warnings."""
    # Create first post
    await test_client.post(
        "/posts",
        headers=auth_headers,
        json={
            "content": "A post about cats.",
            "vector": test_vector,
            "hashtags": [],
            "force": True
        }
    )

    # Create dissimilar post
    response = await test_client.post(
        "/posts",
        headers=auth_headers,
        json={
            "content": "A completely different post about quantum physics.",
            "vector": dissimilar_vector,
            "hashtags": []
        }
    )

    assert response.status_code == 200
    data = response.json()

    # Should create without warning
    assert data["id"] != ""
    assert data["warning"] is None


async def test_reply_creates_notification(
    test_client, test_identity, second_identity,
    auth_headers, second_auth_headers, test_vector_factory
):
    """Test that replying to a post creates a notification for the author."""
    # First user creates a post
    create_response = await test_client.post(
        "/posts",
        headers=auth_headers,
        json={
            "content": "Waiting for replies.",
            "vector": test_vector_factory(seed=300),
            "hashtags": [],
            "force": True
        }
    )

    post_id = create_response.json()["id"]

    # Second user replies
    await test_client.post(
        "/posts",
        headers=second_auth_headers,
        json={
            "content": "Here is my reply!",
            "vector": test_vector_factory(seed=301),
            "hashtags": [],
            "parent_id": post_id,
            "force": True
        }
    )

    # First user checks notifications
    notif_response = await test_client.get("/notifications", headers=auth_headers)

    assert notif_response.status_code == 200
    notifications = notif_response.json()["notifications"]
    assert len(notifications) >= 1

    reply_notif = next((n for n in notifications if n["type"] == "reply"), None)
    assert reply_notif is not None
    assert "replied" in reply_notif["message"]
