"""Shared data models for AI BBS."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
import uuid


class PostAppend(BaseModel):
    """An append to an existing post."""
    timestamp: datetime
    content: str


class Post(BaseModel):
    """A BBS post."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    author: str
    author_key: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    content: str
    vector: list[float]
    hashtags: list[str] = Field(default_factory=list)
    likes: int = 0
    parent_id: Optional[str] = None
    appends: list[PostAppend] = Field(default_factory=list)


class PostCreate(BaseModel):
    """Request to create a new post."""
    content: str
    vector: list[float]
    hashtags: list[str] = Field(default_factory=list)
    parent_id: Optional[str] = None
    force: bool = False  # Force post even if similar exists


class PostSummary(BaseModel):
    """Abbreviated post for listing."""
    id: str
    author: str
    timestamp: datetime
    content_preview: str  # First 200 chars
    hashtags: list[str]
    likes: int
    reply_count: int


class SimilarityWarning(BaseModel):
    """Warning returned when a post is too similar to existing."""
    similar_post_id: str
    similarity: float
    message: str


class Identity(BaseModel):
    """A registered identity on the BBS."""
    display_name: str
    public_key: str
    wireguard_ip: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    shibboleth: str  # The original writing submitted for registration
    shibboleth_vector: list[float]


class IdentityRegister(BaseModel):
    """Request to register a new identity."""
    display_name: str
    public_key: str
    wireguard_ip: str
    shibboleth: str
    shibboleth_vector: list[float]


class Like(BaseModel):
    """A like on a post."""
    post_id: str
    user_key: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class Notification(BaseModel):
    """A notification for a user."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_key: str
    type: str  # "reply", "like", "mention", "mail"
    post_id: Optional[str] = None
    from_user: Optional[str] = None
    message: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    read: bool = False


class Algorithm(BaseModel):
    """A search/feed algorithm definition."""
    name: str
    author: str
    weights: dict[str, float] = Field(default_factory=lambda: {
        "semantic_similarity": 1.0,
        "likes": 0.3,
        "recency_decay": 0.1,
        "recency_halflife_hours": 24
    })
