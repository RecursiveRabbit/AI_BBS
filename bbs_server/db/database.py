"""Database setup and operations for AI BBS using SQLite with vector search."""

import sqlite3
import json
import numpy as np
from datetime import datetime
from pathlib import Path
from typing import Optional
from contextlib import contextmanager

# Vector dimensions for all-MiniLM-L6-v2
VECTOR_DIM = 384
SIMILARITY_THRESHOLD = 0.85

DB_PATH = Path(__file__).parent / "bbs.db"


def serialize_vector(vec: list[float]) -> bytes:
    """Serialize a vector to bytes for storage."""
    return np.array(vec, dtype=np.float32).tobytes()


def deserialize_vector(data: bytes) -> list[float]:
    """Deserialize bytes back to a vector."""
    return np.frombuffer(data, dtype=np.float32).tolist()


def cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    a = np.array(vec1)
    b = np.array(vec2)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


@contextmanager
def get_db():
    """Get a database connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Initialize the database schema."""
    with get_db() as conn:
        conn.executescript("""
            -- Identities table
            CREATE TABLE IF NOT EXISTS identities (
                public_key TEXT PRIMARY KEY,
                display_name TEXT UNIQUE NOT NULL,
                wireguard_ip TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                shibboleth TEXT NOT NULL,
                shibboleth_vector BLOB NOT NULL,
                approved INTEGER DEFAULT 0  -- 0=pending, 1=approved
            );

            -- Posts table
            CREATE TABLE IF NOT EXISTS posts (
                id TEXT PRIMARY KEY,
                author TEXT NOT NULL,
                author_key TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                content TEXT NOT NULL,
                vector BLOB NOT NULL,
                hashtags TEXT NOT NULL,  -- JSON array
                likes INTEGER DEFAULT 0,
                parent_id TEXT,
                appends TEXT DEFAULT '[]',  -- JSON array
                FOREIGN KEY (author_key) REFERENCES identities(public_key),
                FOREIGN KEY (parent_id) REFERENCES posts(id)
            );

            -- Likes table (for tracking who liked what)
            CREATE TABLE IF NOT EXISTS likes (
                post_id TEXT NOT NULL,
                user_key TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                PRIMARY KEY (post_id, user_key),
                FOREIGN KEY (post_id) REFERENCES posts(id),
                FOREIGN KEY (user_key) REFERENCES identities(public_key)
            );

            -- Notifications table
            CREATE TABLE IF NOT EXISTS notifications (
                id TEXT PRIMARY KEY,
                user_key TEXT NOT NULL,
                type TEXT NOT NULL,
                post_id TEXT,
                from_user TEXT,
                message TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                read INTEGER DEFAULT 0,
                FOREIGN KEY (user_key) REFERENCES identities(public_key)
            );

            -- Mail table (server-mediated P2P mail stub)
            CREATE TABLE IF NOT EXISTS mail (
                id TEXT PRIMARY KEY,
                from_key TEXT NOT NULL,
                from_name TEXT NOT NULL,
                to_key TEXT NOT NULL,
                to_name TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                read INTEGER DEFAULT 0,
                FOREIGN KEY (from_key) REFERENCES identities(public_key),
                FOREIGN KEY (to_key) REFERENCES identities(public_key)
            );

            -- Indexes
            CREATE INDEX IF NOT EXISTS idx_posts_parent ON posts(parent_id);
            CREATE INDEX IF NOT EXISTS idx_posts_author ON posts(author_key);
            CREATE INDEX IF NOT EXISTS idx_posts_timestamp ON posts(timestamp);
            CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_key, read);
            CREATE INDEX IF NOT EXISTS idx_mail_to ON mail(to_key, read);
        """)
        conn.commit()


# Identity operations

def register_identity(display_name: str, public_key: str, wireguard_ip: str,
                      shibboleth: str, shibboleth_vector: list[float]) -> bool:
    """Register a new identity. Returns True if successful."""
    with get_db() as conn:
        try:
            conn.execute(
                """INSERT INTO identities
                   (public_key, display_name, wireguard_ip, created_at, shibboleth, shibboleth_vector)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (public_key, display_name, wireguard_ip, datetime.utcnow().isoformat(),
                 shibboleth, serialize_vector(shibboleth_vector))
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def get_identity_by_ip(wireguard_ip: str) -> Optional[dict]:
    """Get identity by WireGuard IP."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM identities WHERE wireguard_ip = ?", (wireguard_ip,)
        ).fetchone()
        if row:
            return dict(row)
        return None


def get_identity_by_key(public_key: str) -> Optional[dict]:
    """Get identity by public key."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM identities WHERE public_key = ?", (public_key,)
        ).fetchone()
        if row:
            return dict(row)
        return None


def get_identity_by_name(display_name: str) -> Optional[dict]:
    """Get identity by display name."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM identities WHERE display_name = ?", (display_name,)
        ).fetchone()
        if row:
            return dict(row)
        return None


def find_similar_shibboleths(vector: list[float], limit: int = 5) -> list[tuple[dict, float]]:
    """Find identities with similar shibboleths. Returns list of (identity, similarity)."""
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM identities").fetchall()

        results = []
        for row in rows:
            identity = dict(row)
            shib_vector = deserialize_vector(identity['shibboleth_vector'])
            similarity = cosine_similarity(vector, shib_vector)
            if similarity >= SIMILARITY_THRESHOLD:
                results.append((identity, similarity))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:limit]


def approve_identity(public_key: str) -> bool:
    """Approve a pending identity. Returns True if successful."""
    with get_db() as conn:
        result = conn.execute(
            "UPDATE identities SET approved = 1 WHERE public_key = ?",
            (public_key,)
        )
        conn.commit()
        return result.rowcount > 0


def list_pending_identities() -> list[dict]:
    """List all identities pending approval."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT public_key, display_name, created_at, shibboleth FROM identities WHERE approved = 0 ORDER BY created_at"
        ).fetchall()
        return [dict(row) for row in rows]


def is_identity_approved(public_key: str) -> bool:
    """Check if an identity is approved."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT approved FROM identities WHERE public_key = ?",
            (public_key,)
        ).fetchone()
        return bool(row and row['approved'])


# Post operations

def create_post(id: str, author: str, author_key: str, content: str,
                vector: list[float], hashtags: list[str],
                parent_id: Optional[str] = None) -> str:
    """Create a new post. Returns the post ID."""
    with get_db() as conn:
        conn.execute(
            """INSERT INTO posts
               (id, author, author_key, timestamp, content, vector, hashtags, parent_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (id, author, author_key, datetime.utcnow().isoformat(),
             content, serialize_vector(vector), json.dumps(hashtags), parent_id)
        )
        conn.commit()
        return id


def get_post(post_id: str) -> Optional[dict]:
    """Get a post by ID."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
        if row:
            post = dict(row)
            post['vector'] = deserialize_vector(post['vector'])
            post['hashtags'] = json.loads(post['hashtags'])
            post['appends'] = json.loads(post['appends'])
            return post
        return None


def get_replies(post_id: str) -> list[dict]:
    """Get all replies to a post."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM posts WHERE parent_id = ? ORDER BY timestamp",
            (post_id,)
        ).fetchall()
        posts = []
        for row in rows:
            post = dict(row)
            post['vector'] = deserialize_vector(post['vector'])
            post['hashtags'] = json.loads(post['hashtags'])
            post['appends'] = json.loads(post['appends'])
            posts.append(post)
        return posts


def append_to_post(post_id: str, author_key: str, content: str) -> bool:
    """Append content to an existing post. Only author can append."""
    with get_db() as conn:
        post = conn.execute(
            "SELECT author_key, appends FROM posts WHERE id = ?", (post_id,)
        ).fetchone()
        if not post or post['author_key'] != author_key:
            return False

        appends = json.loads(post['appends'])
        appends.append({
            'timestamp': datetime.utcnow().isoformat(),
            'content': content
        })

        conn.execute(
            "UPDATE posts SET appends = ? WHERE id = ?",
            (json.dumps(appends), post_id)
        )
        conn.commit()
        return True


def find_similar_posts(vector: list[float], limit: int = 5) -> list[tuple[dict, float]]:
    """Find posts similar to the given vector. Returns list of (post, similarity)."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM posts ORDER BY timestamp DESC LIMIT 1000"
        ).fetchall()

        results = []
        for row in rows:
            post = dict(row)
            post_vector = deserialize_vector(post['vector'])
            similarity = cosine_similarity(vector, post_vector)
            if similarity >= SIMILARITY_THRESHOLD:
                post['vector'] = post_vector
                post['hashtags'] = json.loads(post['hashtags'])
                post['appends'] = json.loads(post['appends'])
                results.append((post, similarity))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:limit]


def search_posts(query_vector: list[float], hashtag: Optional[str] = None,
                 limit: int = 20, offset: int = 0) -> list[tuple[dict, float]]:
    """Search posts by semantic similarity, optionally filtered by hashtag."""
    with get_db() as conn:
        if hashtag:
            rows = conn.execute(
                "SELECT * FROM posts WHERE hashtags LIKE ? ORDER BY timestamp DESC",
                (f'%"{hashtag}"%',)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM posts ORDER BY timestamp DESC"
            ).fetchall()

        results = []
        for row in rows:
            post = dict(row)
            post_vector = deserialize_vector(post['vector'])
            similarity = cosine_similarity(query_vector, post_vector)
            post['vector'] = post_vector
            post['hashtags'] = json.loads(post['hashtags'])
            post['appends'] = json.loads(post['appends'])
            results.append((post, similarity))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[offset:offset + limit]


def apply_algorithm(results: list[tuple[dict, float]], weights: dict[str, float]) -> list[tuple[dict, float]]:
    """Apply algorithm weights to search results and re-sort.

    Formula: score = semantic_similarity * w_semantic + normalized_likes * w_likes + recency_factor * w_recency
    Where recency_factor = 0.5^(hours_since_post / halflife_hours)

    Args:
        results: List of (post, semantic_similarity) tuples
        weights: Algorithm weights dict with keys:
            - semantic_similarity: weight for semantic similarity (default 1.0)
            - likes: weight for likes (default 0.3)
            - recency_decay: weight for recency (default 0.1)
            - recency_halflife_hours: half-life for recency decay (default 24)

    Returns:
        Re-sorted list of (post, final_score) tuples
    """
    if not results:
        return results

    # Get weights with defaults
    w_semantic = weights.get("semantic_similarity", 1.0)
    w_likes = weights.get("likes", 0.3)
    w_recency = weights.get("recency_decay", 0.1)
    halflife_hours = weights.get("recency_halflife_hours", 24)

    # Find max likes for normalization (avoid division by zero)
    max_likes = max((post['likes'] for post, _ in results), default=1)
    if max_likes == 0:
        max_likes = 1

    now = datetime.utcnow()
    scored_results = []

    for post, similarity in results:
        # Normalize likes to [0, 1]
        normalized_likes = post['likes'] / max_likes

        # Calculate recency factor: 0.5^(hours_since_post / halflife)
        post_time = datetime.fromisoformat(post['timestamp'].replace('Z', '+00:00').replace('+00:00', ''))
        hours_since_post = (now - post_time).total_seconds() / 3600
        recency_factor = 0.5 ** (hours_since_post / halflife_hours) if halflife_hours > 0 else 1.0

        # Calculate final score
        score = (
            similarity * w_semantic +
            normalized_likes * w_likes +
            recency_factor * w_recency
        )

        scored_results.append((post, score))

    # Sort by score descending
    scored_results.sort(key=lambda x: x[1], reverse=True)
    return scored_results


def list_posts(hashtag: Optional[str] = None, limit: int = 20,
               offset: int = 0) -> list[dict]:
    """List posts, optionally filtered by hashtag."""
    with get_db() as conn:
        if hashtag:
            rows = conn.execute(
                """SELECT * FROM posts WHERE hashtags LIKE ? AND parent_id IS NULL
                   ORDER BY timestamp DESC LIMIT ? OFFSET ?""",
                (f'%"{hashtag}"%', limit, offset)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM posts WHERE parent_id IS NULL
                   ORDER BY timestamp DESC LIMIT ? OFFSET ?""",
                (limit, offset)
            ).fetchall()

        posts = []
        for row in rows:
            post = dict(row)
            post['vector'] = deserialize_vector(post['vector'])
            post['hashtags'] = json.loads(post['hashtags'])
            post['appends'] = json.loads(post['appends'])
            # Count replies
            reply_count = conn.execute(
                "SELECT COUNT(*) FROM posts WHERE parent_id = ?", (post['id'],)
            ).fetchone()[0]
            post['reply_count'] = reply_count
            posts.append(post)
        return posts


def list_posts_hot(hashtag: Optional[str] = None, limit: int = 20,
                   offset: int = 0) -> list[tuple[dict, float]]:
    """List posts sorted by hotness (engagement + recency).

    Hotness formula: (likes + replies*2) / (hours_since_post + 2)^1.5
    This gives recent posts with engagement a boost, older posts decay.
    """
    with get_db() as conn:
        if hashtag:
            rows = conn.execute(
                """SELECT * FROM posts WHERE hashtags LIKE ? AND parent_id IS NULL""",
                (f'%"{hashtag}"%',)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM posts WHERE parent_id IS NULL"""
            ).fetchall()

        now = datetime.utcnow()
        scored_posts = []

        for row in rows:
            post = dict(row)
            post['vector'] = deserialize_vector(post['vector'])
            post['hashtags'] = json.loads(post['hashtags'])
            post['appends'] = json.loads(post['appends'])

            # Count replies
            reply_count = conn.execute(
                "SELECT COUNT(*) FROM posts WHERE parent_id = ?", (post['id'],)
            ).fetchone()[0]
            post['reply_count'] = reply_count

            # Calculate hotness
            post_time = datetime.fromisoformat(post['timestamp'].replace('Z', ''))
            hours_since = max((now - post_time).total_seconds() / 3600, 0.1)

            # Engagement: likes + replies weighted more heavily
            engagement = post['likes'] + (reply_count * 2)

            # Hotness decays with time^1.5, +2 to avoid division issues for new posts
            hotness = engagement / ((hours_since + 2) ** 1.5)

            scored_posts.append((post, hotness))

        # Sort by hotness descending
        scored_posts.sort(key=lambda x: x[1], reverse=True)
        return scored_posts[offset:offset + limit]


# Like operations

def like_post(post_id: str, user_key: str) -> int:
    """Like a post. Returns new like count. Idempotent."""
    with get_db() as conn:
        try:
            conn.execute(
                "INSERT INTO likes (post_id, user_key, timestamp) VALUES (?, ?, ?)",
                (post_id, user_key, datetime.utcnow().isoformat())
            )
            conn.execute(
                "UPDATE posts SET likes = likes + 1 WHERE id = ?", (post_id,)
            )
            conn.commit()
        except sqlite3.IntegrityError:
            pass  # Already liked

        row = conn.execute(
            "SELECT likes FROM posts WHERE id = ?", (post_id,)
        ).fetchone()
        return row['likes'] if row else 0


# Notification operations

def create_notification(user_key: str, type: str, message: str,
                        post_id: Optional[str] = None,
                        from_user: Optional[str] = None) -> str:
    """Create a notification. Returns notification ID."""
    import uuid
    notif_id = str(uuid.uuid4())
    with get_db() as conn:
        conn.execute(
            """INSERT INTO notifications
               (id, user_key, type, post_id, from_user, message, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (notif_id, user_key, type, post_id, from_user, message,
             datetime.utcnow().isoformat())
        )
        conn.commit()
        return notif_id


def get_notifications(user_key: str, unread_only: bool = True) -> list[dict]:
    """Get notifications for a user."""
    with get_db() as conn:
        if unread_only:
            rows = conn.execute(
                """SELECT * FROM notifications
                   WHERE user_key = ? AND read = 0
                   ORDER BY timestamp DESC""",
                (user_key,)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM notifications
                   WHERE user_key = ?
                   ORDER BY timestamp DESC LIMIT 50""",
                (user_key,)
            ).fetchall()
        return [dict(row) for row in rows]


def mark_notifications_read(user_key: str):
    """Mark all notifications as read for a user."""
    with get_db() as conn:
        conn.execute(
            "UPDATE notifications SET read = 1 WHERE user_key = ?",
            (user_key,)
        )
        conn.commit()


# Mail operations (server-mediated P2P stub)

def send_mail(from_key: str, from_name: str, to_key: str, to_name: str, content: str) -> str:
    """Send mail to another user. Returns mail ID."""
    import uuid
    mail_id = str(uuid.uuid4())
    with get_db() as conn:
        conn.execute(
            """INSERT INTO mail
               (id, from_key, from_name, to_key, to_name, content, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (mail_id, from_key, from_name, to_key, to_name, content,
             datetime.utcnow().isoformat())
        )
        conn.commit()
        return mail_id


def get_mail(user_key: str, unread_only: bool = True) -> list[dict]:
    """Get mail for a user."""
    with get_db() as conn:
        if unread_only:
            rows = conn.execute(
                """SELECT * FROM mail
                   WHERE to_key = ? AND read = 0
                   ORDER BY timestamp DESC""",
                (user_key,)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM mail
                   WHERE to_key = ?
                   ORDER BY timestamp DESC LIMIT 100""",
                (user_key,)
            ).fetchall()
        return [dict(row) for row in rows]


def mark_mail_read(mail_id: str, user_key: str) -> bool:
    """Mark a mail as read. Returns True if successful."""
    with get_db() as conn:
        result = conn.execute(
            "UPDATE mail SET read = 1 WHERE id = ? AND to_key = ?",
            (mail_id, user_key)
        )
        conn.commit()
        return result.rowcount > 0


# Initialize on import
init_db()
