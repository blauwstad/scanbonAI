"""
Seed demo data for ScanbonAI.

Run inside the API container:
    python -m app.scripts.seed_demo
"""

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
from sqlalchemy import text

from app.database import async_session, connect_db


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


async def seed() -> None:
    await connect_db()

    async with async_session() as db:
        # Check if already seeded
        result = await db.execute(text("SELECT count(*) FROM tenants"))
        if result.scalar() > 0:
            print("Database already seeded. Skipping.")
            return

        now = datetime.now(timezone.utc)
        tenant_id = str(uuid.uuid4())

        # 1. Create tenant
        await db.execute(text("""
            INSERT INTO tenants (id, name, settings_json, created_at, updated_at)
            VALUES (:id, :name, :settings, :now, :now)
        """), {
            "id": tenant_id, "name": "Demo BV",
            "settings": json.dumps({"tax_id": "NL123456789B01", "plan": "professional"}),
            "now": now,
        })

        await db.commit()

        print("=" * 60)
        print("  Demo tenant seeded successfully!")
        print("=" * 60)
        print()
        print(f"  Tenant:   Demo BV ({tenant_id})")
        print()
        print("  Register your account at the login page.")
        print("  Admin invite code: scanbon2026")
        print()


if __name__ == "__main__":
    asyncio.run(seed())
