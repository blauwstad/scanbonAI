"""
Business registry enrichment service for ScanbonAI.

Provides a unified interface for fetching company profiles from:
  - KVK (Kamer van Koophandel) -- Netherlands Chamber of Commerce
  - KBO (Kruispuntbank van Ondernemingen) -- Belgium Crossroads Bank for Enterprises

Each provider validates the identifier format, fetches data from the
upstream API, caches the result in Redis for 24 hours, and normalises
the response into a common ``CompanyProfile`` dataclass.

Privacy notes
-------------
- Raw API payloads are NEVER logged.
- Identifiers are masked in log output (only last 4 digits visible).
- Full addresses are never written to log streams.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
import redis.asyncio as aioredis
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import RegistryEnrichmentEvent, User

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Cache configuration
# ---------------------------------------------------------------------------

_CACHE_TTL_SECONDS: int = 86_400  # 24 hours
_CACHE_KEY_PREFIX: str = "registry"

# ---------------------------------------------------------------------------
# Normalised output
# ---------------------------------------------------------------------------


@dataclass
class CompanyProfile:
    """Registry-agnostic company profile returned by every provider."""

    company_name: str | None = None
    legal_name: str | None = None
    address_street: str | None = None
    address_postal_code: str | None = None
    address_city: str | None = None
    address_country: str | None = None  # ISO 3166-1 alpha-2
    vat_number: str | None = None


# ---------------------------------------------------------------------------
# Abstract provider interface
# ---------------------------------------------------------------------------


class RegistryProvider(ABC):
    """Contract that every business registry integration must fulfil."""

    @abstractmethod
    async def fetch_company(self, identifier: str) -> CompanyProfile:
        """Fetch and return a normalised company profile for *identifier*."""
        ...

    @abstractmethod
    def validate_identifier(self, identifier: str) -> bool:
        """Return ``True`` when *identifier* has a valid format."""
        ...


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _mask_identifier(identifier: str) -> str:
    """Mask all but the last 4 characters of an identifier for logging."""
    if len(identifier) <= 4:
        return "****"
    return "*" * (len(identifier) - 4) + identifier[-4:]


def _cache_key(registry_type: str, identifier: str) -> str:
    """Build a deterministic Redis key for a registry lookup."""
    return f"{_CACHE_KEY_PREFIX}:{registry_type.lower()}:{identifier}"


def _get_redis_client() -> aioredis.Redis:
    """Create a Redis client from application settings."""
    return aioredis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
    )


async def _get_cached_profile(
    registry_type: str, identifier: str
) -> CompanyProfile | None:
    """Return a cached ``CompanyProfile`` or ``None`` if not found / Redis down."""
    try:
        client = _get_redis_client()
        try:
            raw = await client.get(_cache_key(registry_type, identifier))
            if raw is not None:
                data = json.loads(raw)
                logger.debug(
                    "registry.cache_hit",
                    registry_type=registry_type,
                    identifier=_mask_identifier(identifier),
                )
                return CompanyProfile(**data)
        finally:
            await client.aclose()
    except Exception:
        # Redis unavailable -- degrade gracefully.
        logger.warning(
            "registry.cache_read_error",
            registry_type=registry_type,
            identifier=_mask_identifier(identifier),
        )
    return None


async def _set_cached_profile(
    registry_type: str, identifier: str, profile: CompanyProfile
) -> None:
    """Store a ``CompanyProfile`` in Redis with a 24-hour TTL.  Silently no-ops on failure."""
    try:
        client = _get_redis_client()
        try:
            await client.setex(
                _cache_key(registry_type, identifier),
                _CACHE_TTL_SECONDS,
                json.dumps(asdict(profile)),
            )
            logger.debug(
                "registry.cache_set",
                registry_type=registry_type,
                identifier=_mask_identifier(identifier),
            )
        finally:
            await client.aclose()
    except Exception:
        logger.warning(
            "registry.cache_write_error",
            registry_type=registry_type,
            identifier=_mask_identifier(identifier),
        )


# ---------------------------------------------------------------------------
# KVK provider  (Netherlands)
# ---------------------------------------------------------------------------

# KVK numbers are exactly 8 digits (leading zeros are significant).
_KVK_PATTERN = re.compile(r"^\d{8}$")


class KvkRegistryProvider(RegistryProvider):
    """Fetch company data from the Dutch KVK (Kamer van Koophandel) API."""

    REGISTRY_TYPE = "kvk"

    # ------------------------------------------------------------------
    # Identifier validation
    # ------------------------------------------------------------------

    def validate_identifier(self, identifier: str) -> bool:
        """Return ``True`` if *identifier* is a valid 8-digit KVK number."""
        cleaned = identifier.strip()
        return bool(_KVK_PATTERN.match(cleaned))

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------

    async def fetch_company(self, identifier: str) -> CompanyProfile:
        """
        Look up a company by KVK number.

        Steps:
        1. Strip whitespace and validate format.
        2. Check Redis cache.
        3. Call the KVK ``basisprofielen`` endpoint.
        4. Map the response into a ``CompanyProfile``.
        5. Cache and return.

        Raises
        ------
        ValueError
            If the identifier format is invalid.
        httpx.HTTPStatusError
            Re-raised after logging for 404 / 429 / other HTTP errors.
        """
        kvk_number = identifier.strip()
        log = logger.bind(
            registry_type=self.REGISTRY_TYPE,
            identifier=_mask_identifier(kvk_number),
        )

        if not self.validate_identifier(kvk_number):
            raise ValueError(
                f"Invalid KVK number format: expected 8 digits, got {kvk_number!r}"
            )

        # --- cache check ---
        cached = await _get_cached_profile(self.REGISTRY_TYPE, kvk_number)
        if cached is not None:
            return cached

        # --- HTTP call ---
        url = f"{settings.KVK_BASE_URL}/basisprofielen/{kvk_number}"
        timeout = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=5.0)

        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                log.info("registry.kvk.fetch_start")
                response = await client.get(
                    url,
                    headers={"apikey": settings.KVK_API_KEY},
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status == 404:
                    log.warning("registry.kvk.not_found")
                elif status == 429:
                    log.warning("registry.kvk.rate_limited")
                else:
                    log.error("registry.kvk.http_error", status_code=status)
                raise
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                log.error("registry.kvk.connection_error", error=type(exc).__name__)
                raise

        data: dict[str, Any] = response.json()
        profile = self._map_response(data)

        # --- cache write ---
        await _set_cached_profile(self.REGISTRY_TYPE, kvk_number, profile)

        log.info("registry.kvk.fetch_success")
        return profile

    # ------------------------------------------------------------------
    # Response mapping
    # ------------------------------------------------------------------

    @staticmethod
    def _map_response(data: dict[str, Any]) -> CompanyProfile:
        """Map a KVK ``basisprofielen`` JSON response to a ``CompanyProfile``."""
        company_name = data.get("naam")

        # Address lives under _embedded.hoofdvestiging.adressen[0]
        address_street: str | None = None
        address_postal_code: str | None = None
        address_city: str | None = None

        embedded = data.get("_embedded", {})
        hoofdvestiging = embedded.get("hoofdvestiging", {})
        adressen = hoofdvestiging.get("adressen", [])

        if adressen and isinstance(adressen, list):
            addr = adressen[0]
            street_parts: list[str] = []
            if addr.get("straatnaam"):
                street_parts.append(addr["straatnaam"])
            if addr.get("huisnummer"):
                street_parts.append(str(addr["huisnummer"]))
            if addr.get("huisnummerToevoeging"):
                street_parts.append(addr["huisnummerToevoeging"])
            address_street = " ".join(street_parts) if street_parts else None
            address_postal_code = addr.get("postcode")
            address_city = addr.get("plaats")

        # KVK does not directly return a VAT number; leave it None.
        return CompanyProfile(
            company_name=company_name,
            legal_name=company_name,
            address_street=address_street,
            address_postal_code=address_postal_code,
            address_city=address_city,
            address_country="NL",
            vat_number=None,
        )


# ---------------------------------------------------------------------------
# KBO provider  (Belgium)
# ---------------------------------------------------------------------------

# KBO enterprise numbers are 10 digits, often written as 0XXX.XXX.XXX.
_KBO_PATTERN = re.compile(r"^0\d{9}$")


class KboRegistryProvider(RegistryProvider):
    """Fetch company data from the Belgian KBO open-data API."""

    REGISTRY_TYPE = "kbo"

    # ------------------------------------------------------------------
    # Identifier validation
    # ------------------------------------------------------------------

    def validate_identifier(self, identifier: str) -> bool:
        """Return ``True`` if *identifier* is a valid 10-digit KBO number (starts with 0)."""
        cleaned = re.sub(r"[\s.]", "", identifier)
        return bool(_KBO_PATTERN.match(cleaned))

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------

    async def fetch_company(self, identifier: str) -> CompanyProfile:
        """
        Look up a company by KBO enterprise number.

        Steps:
        1. Strip dots, spaces and validate.
        2. Check Redis cache.
        3. Call the KBO open-data ``enterprises`` endpoint.
        4. Map the response into a ``CompanyProfile``.
        5. Cache and return.

        Raises
        ------
        ValueError
            If the identifier format is invalid.
        httpx.HTTPStatusError
            Re-raised after logging for HTTP errors.
        """
        kbo_number = re.sub(r"[\s.]", "", identifier)
        log = logger.bind(
            registry_type=self.REGISTRY_TYPE,
            identifier=_mask_identifier(kbo_number),
        )

        if not self.validate_identifier(kbo_number):
            raise ValueError(
                f"Invalid KBO number format: expected 10 digits starting with 0, "
                f"got {kbo_number!r}"
            )

        # --- cache check ---
        cached = await _get_cached_profile(self.REGISTRY_TYPE, kbo_number)
        if cached is not None:
            return cached

        # --- HTTP call ---
        url = f"{settings.KBO_BASE_URL}/enterprises/{kbo_number}"
        timeout = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=5.0)
        headers: dict[str, str] = {"Accept": "application/json"}
        if settings.KBO_API_KEY:
            headers["Authorization"] = f"Bearer {settings.KBO_API_KEY}"

        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                log.info("registry.kbo.fetch_start")
                response = await client.get(url, headers=headers)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status == 404:
                    log.warning("registry.kbo.not_found")
                elif status == 429:
                    log.warning("registry.kbo.rate_limited")
                else:
                    log.error("registry.kbo.http_error", status_code=status)
                raise
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                log.error("registry.kbo.connection_error", error=type(exc).__name__)
                raise

        data: dict[str, Any] = response.json()
        profile = self._map_response(data, kbo_number)

        # --- cache write ---
        await _set_cached_profile(self.REGISTRY_TYPE, kbo_number, profile)

        log.info("registry.kbo.fetch_success")
        return profile

    # ------------------------------------------------------------------
    # Response mapping
    # ------------------------------------------------------------------

    @staticmethod
    def _map_response(data: dict[str, Any], kbo_number: str) -> CompanyProfile:
        """Map a KBO open-data JSON response to a ``CompanyProfile``."""
        # Denominations may be a list of language variants; pick French or Dutch.
        denominations = data.get("denominations", [])
        company_name: str | None = None
        legal_name: str | None = None
        for denom in denominations if isinstance(denominations, list) else []:
            lang = denom.get("language", "").upper()
            type_code = denom.get("typeOfDenomination", "")
            name_val = denom.get("denomination")
            # type "001" is the official company name in KBO
            if type_code == "001" and name_val:
                if legal_name is None:
                    legal_name = name_val
                # Prefer NL (Dutch) variant if available
                if lang in ("NL", "FR") and company_name is None:
                    company_name = name_val
        # Fallback: use legal_name as company_name
        if company_name is None:
            company_name = legal_name

        # Address
        address_street: str | None = None
        address_postal_code: str | None = None
        address_city: str | None = None

        addresses = data.get("addresses", [])
        for addr in addresses if isinstance(addresses, list) else []:
            addr_type = addr.get("typeOfAddress", "")
            # "REGO" is the registered office address
            if addr_type == "REGO":
                street_parts: list[str] = []
                if addr.get("streetNl") or addr.get("streetFr"):
                    street_parts.append(addr.get("streetNl") or addr.get("streetFr", ""))
                if addr.get("houseNumber"):
                    street_parts.append(str(addr["houseNumber"]))
                address_street = " ".join(street_parts) if street_parts else None
                address_postal_code = addr.get("zipcode")
                address_city = addr.get("municipalityNl") or addr.get("municipalityFr")
                break

        # Derive VAT number: Belgian VAT = "BE" + enterprise number
        vat_number = f"BE{kbo_number}" if kbo_number else None

        return CompanyProfile(
            company_name=company_name,
            legal_name=legal_name,
            address_street=address_street,
            address_postal_code=address_postal_code,
            address_city=address_city,
            address_country="BE",
            vat_number=vat_number,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_registry_provider(registry_type: str) -> RegistryProvider:
    """Return a KVK or KBO provider based on *registry_type* string."""
    upper = registry_type.upper()
    if upper == "KVK":
        return KvkRegistryProvider()
    elif upper == "KBO":
        return KboRegistryProvider()
    raise ValueError(f"Unknown registry type: {registry_type}")


# ---------------------------------------------------------------------------
# High-level enrichment helper
# ---------------------------------------------------------------------------


async def enrich_user_from_registry(
    db: AsyncSession,
    user: User,
    registry_type: str,
    identifier: str,
    admin_id: str,
) -> tuple[CompanyProfile | None, str | None]:
    """
    Fetch company data from a business registry and update the user record.

    Creates a ``RegistryEnrichmentEvent`` audit row regardless of outcome.

    Parameters
    ----------
    db:
        Active async database session (caller manages the transaction).
    user:
        The ``User`` ORM instance whose company fields will be updated.
    registry_type:
        ``"KVK"`` or ``"KBO"``.
    identifier:
        The KVK or KBO number to look up.
    admin_id:
        UUID of the admin user who triggered the enrichment.

    Returns
    -------
    tuple[CompanyProfile | None, str | None]
        ``(profile, None)`` on success, ``(None, error_message)`` on failure.
    """
    log = logger.bind(
        registry_type=registry_type,
        identifier=_mask_identifier(identifier),
        user_id=str(user.id),
    )

    profile: CompanyProfile | None = None
    error_message: str | None = None
    response_status_code: int | None = None
    applied_fields: dict[str, Any] = {}

    try:
        provider = get_registry_provider(registry_type)

        if not provider.validate_identifier(identifier):
            error_message = f"Invalid {registry_type.upper()} identifier format."
            log.warning("registry.enrich.invalid_identifier")
            # Fall through to audit record creation.
        else:
            profile = await provider.fetch_company(identifier)
            response_status_code = 200

            # Apply profile fields to the user record.
            field_map: dict[str, str] = {
                "company_name": "company_name",
                "legal_name": "legal_name",
                "address_street": "address_street",
                "address_postal_code": "address_postal_code",
                "address_city": "address_city",
                "address_country": "address_country",
                "vat_number": "vat_number",
            }
            for profile_attr, user_attr in field_map.items():
                value = getattr(profile, profile_attr)
                if value is not None:
                    setattr(user, user_attr, value)
                    applied_fields[user_attr] = value

            # Update enrichment tracking fields.
            user.enrichment_source = registry_type.upper()
            user.enrichment_last_fetched_at = datetime.now(timezone.utc)
            user.enrichment_status = "success"
            user.enrichment_error = None

            # Store the registry identifier on the user.
            if registry_type.upper() == "KVK":
                user.kvk_number = identifier.strip()
            elif registry_type.upper() == "KBO":
                user.kbo_number = re.sub(r"[\s.]", "", identifier)

            log.info("registry.enrich.success")

    except httpx.HTTPStatusError as exc:
        response_status_code = exc.response.status_code
        error_message = f"Registry API returned HTTP {response_status_code}."
        user.enrichment_status = "error"
        user.enrichment_error = error_message
        log.error("registry.enrich.http_error", status_code=response_status_code)

    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        error_message = f"Registry API connection failed: {type(exc).__name__}."
        user.enrichment_status = "error"
        user.enrichment_error = error_message
        log.error("registry.enrich.connection_error", error=type(exc).__name__)

    except ValueError as exc:
        error_message = str(exc)
        user.enrichment_status = "error"
        user.enrichment_error = error_message
        log.warning("registry.enrich.value_error", error=error_message)

    except Exception as exc:
        error_message = f"Unexpected error during registry enrichment: {type(exc).__name__}."
        user.enrichment_status = "error"
        user.enrichment_error = error_message
        log.error("registry.enrich.unexpected_error", error=type(exc).__name__)

    # --- Audit record (always written) ---
    # Build a safe response summary (no raw payload / full addresses).
    response_summary: dict[str, Any] | None = None
    if profile is not None:
        response_summary = {
            "company_name": profile.company_name,
            "address_country": profile.address_country,
            "has_vat": profile.vat_number is not None,
        }

    event = RegistryEnrichmentEvent(
        tenant_id=user.tenant_id,
        user_id=user.id,
        registry_type=registry_type.upper(),
        identifier=identifier,
        requested_by_admin_id=admin_id,
        response_status_code=response_status_code,
        response_summary=response_summary,
        applied_fields_json=applied_fields if applied_fields else None,
        success=error_message is None,
        error_message=error_message,
    )
    db.add(event)

    return profile, error_message
