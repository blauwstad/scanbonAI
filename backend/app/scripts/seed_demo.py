"""
Seed demo data for ScanbonAI.

Run inside the API container:
    python -m app.scripts.seed_demo
"""

import asyncio
import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.database import async_session, connect_db


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
        admin_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())

        # 1. Create tenant
        await db.execute(text("""
            INSERT INTO tenants (id, name, settings_json, created_at, updated_at)
            VALUES (:id, :name, :settings, :now, :now)
        """), {
            "id": tenant_id, "name": "Demo BV",
            "settings": json.dumps({"tax_id": "NL123456789B01", "plan": "professional"}),
            "now": now,
        })

        # 2. Create admin user
        admin_token = secrets.token_urlsafe(48)
        await db.execute(text("""
            INSERT INTO users (id, tenant_id, whatsapp_phone, display_name, role,
                               auth_token, created_at, updated_at)
            VALUES (:id, :tid, :phone, :name, 'admin', :token, :now, :now)
        """), {
            "id": admin_id, "tid": tenant_id, "phone": "+31600000001",
            "name": "Admin Demo", "token": admin_token, "now": now,
        })

        # 3. Create regular user
        user_token = secrets.token_urlsafe(48)
        await db.execute(text("""
            INSERT INTO users (id, tenant_id, whatsapp_phone, display_name, role,
                               auth_token, created_at, updated_at)
            VALUES (:id, :tid, :phone, :name, 'user', :token, :now, :now)
        """), {
            "id": user_id, "tid": tenant_id, "phone": "+31600000002",
            "name": "Jan de Vries", "token": user_token, "now": now,
        })

        # 4. Create demo invoices
        # Valid statuses: uploaded, quality_failed, processing, extracted, reviewed, approved, exported
        invoices = [
            {
                "id": str(uuid.uuid4()),
                "status": "extracted",
                "supplier": "Albert Heijn BV",
                "invoice_number": "AH-2026-001",
                "total": 156.43,
                "vat": 27.15,
                "category": "office_supplies",
                "confidence": 0.94,
                "date": "2026-01-15",
            },
            {
                "id": str(uuid.uuid4()),
                "status": "reviewed",
                "supplier": "Bol.com BV",
                "invoice_number": "BOL-2026-0042",
                "total": 89.99,
                "vat": 15.62,
                "category": "equipment",
                "confidence": 0.97,
                "date": "2026-01-22",
            },
            {
                "id": str(uuid.uuid4()),
                "status": "approved",
                "supplier": "KPN Telecom",
                "invoice_number": "KPN-2026-FEB-001",
                "total": 45.50,
                "vat": 7.89,
                "category": "telecommunications",
                "confidence": 0.99,
                "date": "2026-02-01",
            },
            {
                "id": str(uuid.uuid4()),
                "status": "reviewed",
                "supplier": "Shell Nederland",
                "invoice_number": "SH-88721",
                "total": 72.30,
                "vat": 12.55,
                "category": "vehicle",
                "confidence": 0.78,
                "date": "2026-02-10",
                "has_correction": True,
            },
            {
                "id": str(uuid.uuid4()),
                "status": "extracted",
                "supplier": "Coolblue BV",
                "invoice_number": "CB-2026-1192",
                "total": 299.00,
                "vat": 51.89,
                "category": "equipment",
                "confidence": 0.91,
                "date": "2026-02-18",
            },
            {
                "id": str(uuid.uuid4()),
                "status": "quality_failed",
                "supplier": "Unknown",
                "invoice_number": "???",
                "total": 0,
                "vat": 0,
                "category": "other",
                "confidence": 0.22,
                "date": "2026-02-20",
            },
        ]

        for inv in invoices:
            month = inv["date"][:7]

            # Insert invoice
            await db.execute(text("""
                INSERT INTO invoices (id, tenant_id, user_id, file_path, file_hash,
                                     status, upload_source, whatsapp_message_id,
                                     month_partition, created_at, updated_at)
                VALUES (:id, :tid, :uid, :path, :hash,
                        :status, 'whatsapp', :msg_id,
                        :month, :now, :now)
            """), {
                "id": inv["id"], "tid": tenant_id, "uid": user_id,
                "path": f"{tenant_id}/{user_id}/{month}/{inv['id']}.jpg",
                "hash": hashlib.sha256(inv["id"].encode()).hexdigest(),
                "status": inv["status"], "msg_id": f"wamid.demo.{inv['id'][:8]}",
                "month": month, "now": now,
            })

            # Insert quality check
            passed = inv["status"] != "quality_failed"
            await db.execute(text("""
                INSERT INTO quality_checks (id, invoice_id, blur_score, resolution_ok,
                                           skew_angle, overall_pass, failure_reasons, checked_at)
                VALUES (:id, :iid, :blur, :res_ok, :skew, :passed, :reasons, :now)
            """), {
                "id": str(uuid.uuid4()), "iid": inv["id"],
                "blur": 150.0 if passed else 45.0,
                "res_ok": passed,
                "skew": 1.2 if passed else 15.0,
                "passed": passed,
                "reasons": json.dumps([]) if passed else json.dumps(["BLUR", "LOW_RESOLUTION"]),
                "now": now,
            })

            if not passed:
                continue

            # Insert OCR result
            ocr_id = str(uuid.uuid4())
            await db.execute(text("""
                INSERT INTO ocr_results (id, invoice_id, raw_text, model_name,
                                        model_version, processing_time_ms, created_at)
                VALUES (:id, :iid, :text, :model, :version, :time, :now)
            """), {
                "id": ocr_id, "iid": inv["id"],
                "text": f"Demo OCR output for {inv['supplier']} invoice {inv['invoice_number']}",
                "model": "deepseek-ocr-v2", "version": "2.0",
                "time": 2340, "now": now,
            })

            # Insert extracted data
            extracted_json = {
                "invoice_number": inv["invoice_number"],
                "invoice_date": inv["date"],
                "due_date": str((datetime.strptime(inv["date"], "%Y-%m-%d") + timedelta(days=30)).date()),
                "supplier_name": inv["supplier"],
                "supplier_address": "Demo Straat 1, Amsterdam",
                "supplier_tax_id": "NL123456789B01",
                "supplier_iban": "NL91ABNA0417164300",
                "supplier_kvk": "12345678",
                "subtotal": round(inv["total"] - inv["vat"], 2),
                "vat_amount": inv["vat"],
                "total_amount": inv["total"],
                "currency": "EUR",
                "category": inv["category"],
                "line_items": [
                    {
                        "description": f"Product/Service from {inv['supplier']}",
                        "quantity": 1,
                        "unit_price": round(inv["total"] - inv["vat"], 2),
                        "total": round(inv["total"] - inv["vat"], 2),
                        "vat_rate": 21.0,
                        "vat_amount": inv["vat"],
                    }
                ],
            }

            confidence_scores = {
                "overall": inv["confidence"],
                "invoice_number": min(inv["confidence"] + 0.03, 1.0),
                "invoice_date": min(inv["confidence"] + 0.02, 1.0),
                "supplier_name": inv["confidence"],
                "total_amount": min(inv["confidence"] + 0.01, 1.0),
                "vat_amount": round(inv["confidence"] - 0.05, 2),
                "subtotal": round(inv["confidence"] - 0.03, 2),
                "currency": 0.99,
                "category": round(inv["confidence"] - 0.10, 2),
            }

            extracted_data_id = str(uuid.uuid4())
            await db.execute(text("""
                INSERT INTO extracted_data (id, invoice_id, ocr_result_id, extracted_json,
                                           confidence_scores, extraction_version, created_at)
                VALUES (:id, :iid, :ocr_id, :json, :conf, :version, :now)
            """), {
                "id": extracted_data_id, "iid": inv["id"], "ocr_id": ocr_id,
                "json": json.dumps(extracted_json),
                "conf": json.dumps(confidence_scores),
                "version": "v1.0", "now": now,
            })

            # Add user correction for corrected invoice
            if inv.get("has_correction"):
                corrected_json = dict(extracted_json)
                corrected_json["total_amount"] = 73.20
                diff_json = {"total_amount": {"original": 72.30, "corrected": 73.20}}
                await db.execute(text("""
                    INSERT INTO user_corrections (id, invoice_id, extracted_data_id,
                                                  corrected_json, diff_json,
                                                  corrected_by_user_id, created_at)
                    VALUES (:id, :iid, :eid, :corr, :diff, :uid, :now)
                """), {
                    "id": str(uuid.uuid4()), "iid": inv["id"], "eid": extracted_data_id,
                    "corr": json.dumps(corrected_json), "diff": json.dumps(diff_json),
                    "uid": user_id, "now": now,
                })

            # Add admin review for approved invoice
            if inv["status"] == "approved":
                await db.execute(text("""
                    INSERT INTO admin_reviews (id, invoice_id, reviewer_id, action,
                                              notes, created_at)
                    VALUES (:id, :iid, :aid, 'approved', :notes, :now)
                """), {
                    "id": str(uuid.uuid4()), "iid": inv["id"], "aid": admin_id,
                    "notes": "Looks correct, approved.", "now": now,
                })

        await db.commit()

        print("=" * 60)
        print("  Demo data seeded successfully!")
        print("=" * 60)
        print()
        print(f"  Tenant:   Demo BV ({tenant_id})")
        print(f"  Admin:    Admin Demo (+31600000001)")
        print(f"  User:     Jan de Vries (+31600000002)")
        print(f"  Invoices: {len(invoices)} demo invoices")
        print()
        print("  Auth tokens (use as Bearer token):")
        print(f"    Admin: {admin_token}")
        print(f"    User:  {user_token}")
        print()
        print(f"  Tenant ID: {tenant_id}")
        print()
        print("  Test with:")
        print(f"    curl -H 'Authorization: Bearer {{user_token}}' \\")
        print(f"         -H 'X-Tenant-ID: {tenant_id}' \\")
        print(f"         http://localhost:8000/api/v1/invoices")


if __name__ == "__main__":
    asyncio.run(seed())
