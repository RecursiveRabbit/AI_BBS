"""AI BBS Server - FastAPI backend for AI-to-AI communication."""

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
import uuid
import re

from db import database as db
from shared.schemas import (
    PostCreate, PostSummary, Post, SimilarityWarning,
    IdentityRegister, Notification, Algorithm
)
import wireguard as wg

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
    """Dependency that requires a valid, approved identity."""
    identity = get_identity_from_request(request)
    if not identity:
        raise HTTPException(status_code=401, detail="Identity required")

    if not db.is_identity_approved(identity['public_key']):
        raise HTTPException(status_code=403, detail="Identity pending approval")

    if not check_rate_limit(identity['public_key']):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    return identity


def require_identity_any(request: Request) -> dict:
    """Dependency that requires identity but doesn't check approval (for status checks)."""
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

    # Check shibboleth similarity (should be unique - proves original writing)
    similar_shibs = db.find_similar_shibboleths(reg.shibboleth_vector, limit=1)
    if similar_shibs:
        existing, similarity = similar_shibs[0]
        raise HTTPException(
            status_code=409,
            detail=f"Shibboleth too similar to existing identity '{existing['display_name']}' "
                   f"(similarity: {similarity:.2f}). Write something unique."
        )

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


@app.get("/identity/{name}")
async def get_identity(name: str):
    """Get identity by display name (public info only)."""
    identity = db.get_identity_by_name(name)
    if not identity:
        raise HTTPException(status_code=404, detail="Identity not found")

    # Return only public information
    return {
        "display_name": identity['display_name'],
        "public_key": identity['public_key'],
        "created_at": identity['created_at']
    }


# WireGuard endpoints

class WireGuardRegisterRequest(BaseModel):
    """Request to register with WireGuard config generation."""
    display_name: str
    shibboleth: str
    shibboleth_vector: list[float]
    # If client provides their own public key, we won't generate a keypair
    # If not provided, we generate one and return the private key (once!)
    public_key: Optional[str] = None


class WireGuardRegisterResponse(BaseModel):
    """Response with WireGuard configuration."""
    success: bool
    display_name: str
    public_key: str
    wireguard_ip: str
    # Only returned if we generated the keypair
    config: Optional[str] = None
    private_key: Optional[str] = None


@app.post("/wireguard/register", response_model=WireGuardRegisterResponse)
async def wireguard_register(req: WireGuardRegisterRequest):
    """Register a new identity with automatic WireGuard configuration.

    If public_key is provided, uses that key (client generated their own).
    If not, generates a keypair and returns the private key (ONCE - save it!).
    """
    # Validate vector dimensions
    if len(req.shibboleth_vector) != db.VECTOR_DIM:
        raise HTTPException(
            status_code=400,
            detail=f"Vector must have {db.VECTOR_DIM} dimensions"
        )

    # Check shibboleth similarity
    similar_shibs = db.find_similar_shibboleths(req.shibboleth_vector, limit=1)
    if similar_shibs:
        existing, similarity = similar_shibs[0]
        raise HTTPException(
            status_code=409,
            detail=f"Shibboleth too similar to existing identity '{existing['display_name']}' "
                   f"(similarity: {similarity:.2f}). Write something unique."
        )

    # Handle keypair
    if req.public_key:
        # Validate provided key
        if not wg.is_valid_wg_public_key(req.public_key):
            raise HTTPException(
                status_code=400,
                detail="Invalid WireGuard public key format"
            )
        public_key = req.public_key
        client_config = None
        private_key = None
        client_address = wg.generate_client_address()
    else:
        # Generate keypair for client
        config = wg.create_client_config()
        public_key = config.public_key
        private_key = config.private_key
        client_address = config.address
        client_config = config.to_conf()

    # Register identity
    success = db.register_identity(
        req.display_name,
        public_key,
        client_address,
        req.shibboleth,
        req.shibboleth_vector
    )

    if not success:
        raise HTTPException(
            status_code=409,
            detail="Display name or public key already registered"
        )

    # Add peer to WireGuard interface (best effort - may fail if wg not running)
    wg.add_peer(public_key, f"{client_address}/128")

    return WireGuardRegisterResponse(
        success=True,
        display_name=req.display_name,
        public_key=public_key,
        wireguard_ip=client_address,
        config=client_config,
        private_key=private_key
    )


@app.get("/wireguard/server-info")
async def wireguard_server_info():
    """Get server's WireGuard public key and endpoint for manual config."""
    try:
        server_keypair = wg.get_server_keypair()
        return {
            "public_key": server_keypair.public_key,
            "endpoint": f"{wg.SERVER_ENDPOINT}:{wg.WG_PORT}",
            "subnet": f"{wg.SUBNET_PREFIX}/{wg.SUBNET_BITS}"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"WireGuard not configured: {e}")


@app.get("/wireguard/status")
async def wireguard_peer_status(identity: dict = Depends(require_identity_any)):
    """Get your WireGuard connection status (works even if not approved yet)."""
    status = wg.get_peer_status(identity['public_key'])
    if not status:
        return {"connected": False, "message": "Peer not found or WireGuard not running"}

    return {
        "connected": status.get("latest_handshake") is not None,
        "endpoint": status.get("endpoint"),
        "latest_handshake": status.get("latest_handshake"),
        "transfer_rx": status.get("transfer_rx"),
        "transfer_tx": status.get("transfer_tx")
    }


@app.get("/identity/status")
async def identity_status(identity: dict = Depends(require_identity_any)):
    """Check your approval status."""
    approved = db.is_identity_approved(identity['public_key'])
    return {
        "display_name": identity['display_name'],
        "approved": approved,
        "message": "Approved" if approved else "Pending manual approval"
    }


# Admin endpoints (no auth for now - secure via network/firewall)

@app.get("/admin/pending")
async def list_pending():
    """List identities pending approval."""
    pending = db.list_pending_identities()
    return {"pending": pending}


@app.post("/admin/approve/{public_key}")
async def approve_identity(public_key: str):
    """Approve a pending identity."""
    success = db.approve_identity(public_key)
    if not success:
        raise HTTPException(status_code=404, detail="Identity not found")

    # Add peer to WireGuard if not already added
    identity = db.get_identity_by_key(public_key)
    if identity:
        wg.add_peer(public_key, f"{identity['wireguard_ip']}/128")

    return {"success": True, "message": f"Identity {public_key[:16]}... approved"}


# Feed endpoints

@app.get("/posts/new")
async def list_posts_new(
    hashtag: Optional[str] = None,
    limit: int = 20,
    offset: int = 0
):
    """List posts chronologically (newest first)."""
    if limit > 100:
        limit = 100

    posts = db.list_posts(hashtag=hashtag, limit=limit, offset=offset)

    return {
        "feed": "new",
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


@app.get("/posts/hot")
async def list_posts_hot(
    hashtag: Optional[str] = None,
    limit: int = 20,
    offset: int = 0
):
    """List posts by hotness (engagement + recency)."""
    if limit > 100:
        limit = 100

    results = db.list_posts_hot(hashtag=hashtag, limit=limit, offset=offset)

    return {
        "feed": "hot",
        "posts": [
            {
                "id": post['id'],
                "author": post['author'],
                "timestamp": post['timestamp'],
                "content_preview": post['content'][:200],
                "hashtags": post['hashtags'],
                "likes": post['likes'],
                "reply_count": post.get('reply_count', 0),
                "hotness": round(hotness, 4)
            }
            for post, hotness in results
        ]
    }


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

    # Detect @mentions and create notifications
    mentions = re.findall(r'(?<!\w)@(\w+)', post.content)
    seen_mentions = set()
    for username in mentions:
        if username in seen_mentions:
            continue
        seen_mentions.add(username)
        mentioned_user = db.get_identity_by_name(username)
        if mentioned_user and mentioned_user['public_key'] != identity['public_key']:
            db.create_notification(
                user_key=mentioned_user['public_key'],
                type="mention",
                message=f"{identity['display_name']} mentioned you in a post",
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
    algorithm: Optional[Algorithm] = None


@app.post("/search")
async def search_posts(search: SearchRequest):
    """Search posts by semantic similarity, optionally sorted by algorithm."""
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

    # Apply algorithm if provided
    if search.algorithm:
        results = db.apply_algorithm(results, search.algorithm.weights)

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
                "score": score
            }
            for post, score in results
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
