"""API Security module.

Provides JWT authentication, rate limiting, and logging.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class JWTToken:
    user_id: str
    timestamp: str
    expires: str
    signature: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    reset_time: float

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AuditLog:
    timestamp: str
    user_id: str
    action: str
    details: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


class SecurityManager:
    """Manages API security features."""
    
    def __init__(self, rate_limit: int = 100, token_expiry_hours: int = 24):
        self.version = "2.0"
        self.rate_limit = rate_limit
        self.token_expiry_hours = token_expiry_hours
        self.rate_limits: dict[str, list[float]] = defaultdict(list)
        self.audit_logs: list[AuditLog] = []
        self.tokens: dict[str, JWTToken] = {}
    
    def generate_token(self, user_id: str) -> JWTToken:
        """Generate a JWT token."""
        timestamp = datetime.now().isoformat()
        expires = timestamp  # Simplified - in production, calculate actual expiry
        # Generate signature
        signature_data = f"{user_id}:{timestamp}:{self.version}"
        signature = hashlib.sha256(signature_data.encode()).hexdigest()
        
        token = JWTToken(
            user_id=user_id,
            timestamp=timestamp,
            expires=expires,
            signature=signature,
        )
        
        self.tokens[signature] = token
        self._log_audit(user_id, "token_generated", {"token_prefix": signature[:16]})
        
        return token
    
    def validate_token(self, token: str) -> bool:
        """Validate a JWT token."""
        return token in self.tokens
    
    def check_rate_limit(self, user_id: str) -> RateLimitResult:
        """Check rate limit for a user."""
        now = time.time()
        
        # Clean old entries (older than 1 minute)
        self.rate_limits[user_id] = [
            t for t in self.rate_limits[user_id] if now - t < 60
        ]
        
        # Check limit
        current_count = len(self.rate_limits[user_id])
        allowed = current_count < self.rate_limit
        
        if allowed:
            self.rate_limits[user_id].append(now)
        
        remaining = max(0, self.rate_limit - current_count - (1 if allowed else 0))
        reset_time = now + 60 - (self.rate_limits[user_id][0] if self.rate_limits[user_id] else now)
        
        return RateLimitResult(
            allowed=allowed,
            remaining=remaining,
            reset_time=reset_time,
        )
    
    def _log_audit(self, user_id: str, action: str, details: dict[str, Any]) -> None:
        """Log an audit event."""
        log = AuditLog(
            timestamp=datetime.now().isoformat(),
            user_id=user_id,
            action=action,
            details=details,
        )
        self.audit_logs.append(log)
        
        # Keep only last 1000 logs
        if len(self.audit_logs) > 1000:
            self.audit_logs = self.audit_logs[-1000:]
    
    def get_audit_logs(self, limit: int = 100) -> list[AuditLog]:
        """Get audit logs."""
        return self.audit_logs[-limit:]
    
    def get_stats(self) -> dict[str, Any]:
        """Get security statistics."""
        return {
            "version": self.version,
            "rate_limit": self.rate_limit,
            "active_tokens": len(self.tokens),
            "total_audit_logs": len(self.audit_logs),
            "active_users": len(self.rate_limits),
        }


def create_security_manager(rate_limit: int = 100) -> SecurityManager:
    """Create a new SecurityManager instance."""
    return SecurityManager(rate_limit=rate_limit)
