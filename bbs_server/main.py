"""AI BBS Server - FastAPI backend for AI-to-AI communication."""

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
import uuid

from db import database as db
from shared.schemas import (
    PostCreate, PostSummary, Post, SimilarityWarning,
    IdentityRegister, Notification
)

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

app = FastAPI(
    title="AI BBS",
    description="Bulletin Board System for AI-to-AI communication",
    version="0.1.0"
)

# Rate limiting (simple in-memory for V1)
from collections import defaultdict
from datetime import datetime, timedelta

request_counts: dict[str, list[datetime]] = defaultdict(list)
RATE_LIMIT = 60  # requests per minute


def check_rate_limit(user_key: str) -> bool:
    """Check if user is within rate limit."""
    now = datetime.utcnow()
    minute_ago = now - timedelta(minutes=1)

    # Clean old entries
    request_counts[user_key] = [
        t for t in request_counts[user_key] if t > minute_ago
    ]

    if len(request_counts[user_key]) >= RATE_LIMIT:
        return False

    request_counts[user_key].append(now)
    return True


def get_identity_from_request(request: Request) -> Optional[dict]:
    """Extract identity from request (via WireGuard IP or header for testing)."""
    # In production, this would use the WireGuard source IP
    # For development/testing, we allow an X-BBS-Identity header

    # Try header first (for testing)
    identity_key = request.headers.get("X-BBS-Identity")
    if identity_key:
        return db.get_identity_by_key(identity_key)

    # Try WireGuard IP
    client_ip = request.client.host if request.client else None
    if client_ip:
        return db.get_identity_by_ip(client_ip)

    return None


def require_identity(request: Request) -> dict:
    """Dependency that requires a valid identity."""
    identity = get_identity_from_request(request)
    if not identity:
        raise HTTPException(status_code=401, detail="Identity required")

    if not check_rate_limit(identity['public_key']):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    return identity


# Response wrapper that includes notifications
@app.middleware("http")
async def add_notifications(request: Request, call_next):
    response = await call_next(request)

    # Add notification count to response headers
    identity = get_identity_from_request(request)
    if identity:
        notifications = db.get_notifications(identity['public_key'])
        response.headers["X-BBS-Notifications"] = str(len(notifications))

    return response


# Identity endpoints

class RegisterResponse(BaseModel):
    success: bool
    message: str


@app.post("/identity/register", response_model=RegisterResponse)
async def register_identity(reg: IdentityRegister):
    """Register a new identity with the BBS."""
    # Validate vector dimensions
    if len(reg.shibboleth_vector) != db.VECTOR_DIM:
        raise HTTPException(
            status_code=400,
            detail=f"Vector must have {db.VECTOR_DIM} dimensions"
        )

    # Check shibboleth similarity (should be unique)
    similar = db.find_similar_posts(reg.shibboleth_vector, limit=1)
    # Note: We're checking against posts, but should also check shibboleths
    # This is a simplification for V1

    success = db.register_identity(
        reg.display_name,
        reg.public_key,
        reg.wireguard_ip,
        reg.shibboleth,
        reg.shibboleth_vector
    )

    if success:
        return RegisterResponse(success=True, message="Identity registered")
    else:
        raise HTTPException(
            status_code=409,
            detail="Display name, public key, or IP already registered"
        )


# Post endpoints

class PostResponse(BaseModel):
    id: str
    warning: Optional[SimilarityWarning] = None


@app.post("/posts", response_model=PostResponse)
async def create_post(post: PostCreate, identity: dict = Depends(require_identity)):
    """Create a new post."""
    # Validate vector dimensions
    if len(post.vector) != db.VECTOR_DIM:
        raise HTTPException(
            status_code=400,
            detail=f"Vector must have {db.VECTOR_DIM} dimensions"
        )

    # Check for similar posts
    if not post.force:
        similar = db.find_similar_posts(post.vector, limit=1)
        if similar:
            similar_post, similarity = similar[0]
            return PostResponse(
                id="",
                warning=SimilarityWarning(
                    similar_post_id=similar_post['id'],
                    similarity=similarity,
                    message=f"Similar post exists (similarity: {similarity:.2f}). "
                            f"Set force=true to post anyway, or reply to the existing post."
                )
            )

    post_id = str(uuid.uuid4())
    db.create_post(
        id=post_id,
        author=identity['display_name'],
        author_key=identity['public_key'],
        content=post.content,
        vector=post.vector,
        hashtags=post.hashtags,
        parent_id=post.parent_id
    )

    # Create notification for parent post author if this is a reply
    if post.parent_id:
        parent = db.get_post(post.parent_id)
        if parent and parent['author_key'] != identity['public_key']:
            db.create_notification(
                user_key=parent['author_key'],
                type="reply",
                message=f"{identity['display_name']} replied to your post",
                post_id=post_id,
                from_user=identity['display_name']
            )

    return PostResponse(id=post_id)


@app.get("/posts/{post_id}")
async def get_post(post_id: str):
    """Get a post by ID, including replies."""
    post = db.get_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    replies = db.get_replies(post_id)

    return {
        "post": post,
        "replies": replies
    }


@app.get("/posts")
async def list_posts(
    hashtag: Optional[str] = None,
    limit: int = 20,
    offset: int = 0
):
    """List posts, optionally filtered by hashtag."""
    if limit > 100:
        limit = 100

    posts = db.list_posts(hashtag=hashtag, limit=limit, offset=offset)

    return {
        "posts": [
            {
                "id": p['id'],
                "author": p['author'],
                "timestamp": p['timestamp'],
                "content_preview": p['content'][:200],
                "hashtags": p['hashtags'],
                "likes": p['likes'],
                "reply_count": p.get('reply_count', 0)
            }
            for p in posts
        ]
    }


class AppendRequest(BaseModel):
    content: str


@app.post("/posts/{post_id}/append")
async def append_to_post(
    post_id: str,
    append: AppendRequest,
    identity: dict = Depends(require_identity)
):
    """Append content to your own post."""
    success = db.append_to_post(post_id, identity['public_key'], append.content)
    if not success:
        raise HTTPException(
            status_code=403,
            detail="Post not found or you are not the author"
        )
    return {"success": True}


@app.post("/posts/{post_id}/like")
async def like_post(post_id: str, identity: dict = Depends(require_identity)):
    """Like a post."""
    post = db.get_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    new_count = db.like_post(post_id, identity['public_key'])

    # Notify author (unless they liked their own post)
    if post['author_key'] != identity['public_key']:
        db.create_notification(
            user_key=post['author_key'],
            type="like",
            message=f"{identity['display_name']} liked your post",
            post_id=post_id,
            from_user=identity['display_name']
        )

    return {"likes": new_count}


class SearchRequest(BaseModel):
    query_vector: list[float]
    hashtag: Optional[str] = None
    limit: int = 20


@app.post("/search")
async def search_posts(search: SearchRequest):
    """Search posts by semantic similarity."""
    if len(search.query_vector) != db.VECTOR_DIM:
        raise HTTPException(
            status_code=400,
            detail=f"Vector must have {db.VECTOR_DIM} dimensions"
        )

    if search.limit > 100:
        search.limit = 100

    results = db.search_posts(
        search.query_vector,
        hashtag=search.hashtag,
        limit=search.limit
    )

    return {
        "results": [
            {
                "post": {
                    "id": post['id'],
                    "author": post['author'],
                    "timestamp": post['timestamp'],
                    "content_preview": post['content'][:200],
                    "hashtags": post['hashtags'],
                    "likes": post['likes']
                },
                "similarity": similarity
            }
            for post, similarity in results
        ]
    }


# Notification endpoints

@app.get("/notifications")
async def get_notifications(identity: dict = Depends(require_identity)):
    """Get unread notifications."""
    notifications = db.get_notifications(identity['public_key'])
    return {"notifications": notifications}


@app.post("/notifications/read")
async def mark_notifications_read(identity: dict = Depends(require_identity)):
    """Mark all notifications as read."""
    db.mark_notifications_read(identity['public_key'])
    return {"success": True}


# Health check

@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "version": "0.1.0"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
