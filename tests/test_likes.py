"""Tests for like functionality."""

import pytest

pytestmark = pytest.mark.asyncio


async def test_like_post(test_client, test_identity, auth_headers, test_vector):
    """Test liking a post."""
    # Create a post
    create_response = await test_client.post(
        "/posts",
        headers=auth_headers,
        json={
            "content": "A likeable post.",
            "vector": test_vector,
            "hashtags": [],
            "force": True
        }
    )

    post_id = create_response.json()["id"]

    # Like the post
    like_response = await test_client.post(
        f"/posts/{post_id}/like",
        headers=auth_headers
    )

    assert like_response.status_code == 200
    assert like_response.json()["likes"] == 1


async def test_like_idempotent(test_client, test_identity, auth_headers, test_vector):
    """Test that liking a post multiple times is idempotent."""
    # Create a post
    create_response = await test_client.post(
        "/posts",
        headers=auth_headers,
        json={
            "content": "A post to like many times.",
            "vector": test_vector,
            "hashtags": [],
            "force": True
        }
    )

    post_id = create_response.json()["id"]

    # Like the post multiple times
    for _ in range(3):
        like_response = await test_client.post(
            f"/posts/{post_id}/like",
            headers=auth_headers
        )
        assert like_response.status_code == 200

    # Should still be just 1 like
    assert like_response.json()["likes"] == 1


async def test_like_by_multiple_users(
    test_client, test_identity, second_identity,
    auth_headers, second_auth_headers, test_vector
):
    """Test that multiple users can like the same post."""
    # Create a post
    create_response = await test_client.post(
        "/posts",
        headers=auth_headers,
        json={
            "content": "A very popular post.",
            "vector": test_vector,
            "hashtags": [],
            "force": True
        }
    )

    post_id = create_response.json()["id"]

    # First user likes
    await test_client.post(f"/posts/{post_id}/like", headers=auth_headers)

    # Second user likes
    like_response = await test_client.post(
        f"/posts/{post_id}/like",
        headers=second_auth_headers
    )

    assert like_response.status_code == 200
    assert like_response.json()["likes"] == 2


async def test_like_nonexistent_post(test_client, auth_headers):
    """Test liking a post that doesn't exist."""
    response = await test_client.post(
        "/posts/nonexistent-post-id/like",
        headers=auth_headers
    )

    assert response.status_code == 404
    assert "Post not found" in response.json()["detail"]


async def test_like_requires_auth(test_client, test_identity, auth_headers, test_vector):
    """Test that liking requires authentication."""
    # Create a post
    create_response = await test_client.post(
        "/posts",
        headers=auth_headers,
        json={
            "content": "An unlikeable post without auth.",
            "vector": test_vector,
            "hashtags": [],
            "force": True
        }
    )

    post_id = create_response.json()["id"]

    # Try to like without auth
    response = await test_client.post(f"/posts/{post_id}/like")

    assert response.status_code == 401
    assert "Identity required" in response.json()["detail"]


async def test_like_creates_notification(
    test_client, test_identity, second_identity,
    auth_headers, second_auth_headers, test_vector
):
    """Test that liking creates a notification for the post author."""
    # First user creates a post
    create_response = await test_client.post(
        "/posts",
        headers=auth_headers,
        json={
            "content": "Post waiting for likes.",
            "vector": test_vector,
            "hashtags": [],
            "force": True
        }
    )

    post_id = create_response.json()["id"]

    # Second user likes the post
    await test_client.post(
        f"/posts/{post_id}/like",
        headers=second_auth_headers
    )

    # First user checks notifications
    notif_response = await test_client.get("/notifications", headers=auth_headers)

    assert notif_response.status_code == 200
    notifications = notif_response.json()["notifications"]
    assert len(notifications) >= 1

    like_notif = next((n for n in notifications if n["type"] == "like"), None)
    assert like_notif is not None
    assert "liked" in like_notif["message"]


async def test_self_like_no_notification(test_client, test_identity, auth_headers, test_vector):
    """Test that liking your own post doesn't create a notification."""
    # Create a post
    create_response = await test_client.post(
        "/posts",
        headers=auth_headers,
        json={
            "content": "I will like my own post.",
            "vector": test_vector,
            "hashtags": [],
            "force": True
        }
    )

    post_id = create_response.json()["id"]

    # Like own post
    await test_client.post(f"/posts/{post_id}/like", headers=auth_headers)

    # Check notifications - should be none for self-like
    notif_response = await test_client.get("/notifications", headers=auth_headers)

    notifications = notif_response.json()["notifications"]
    like_notifs = [n for n in notifications if n["type"] == "like"]
    assert len(like_notifs) == 0
