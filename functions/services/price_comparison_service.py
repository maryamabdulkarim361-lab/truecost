"""Price Comparison Service for TrueCost.

Provides integration with the TypeScript comparePrices Cloud Function
to retrieve real-time material prices from Home Depot and Lowe's.

Architecture:
- Calls TypeScript Cloud Function via HTTP
- Polls Firestore for completion (async function writes incrementally)
- Extracts best prices from comparison results
- Falls back to hardcoded costs on failure

References:
- collabcanvas/functions/src/priceComparison.ts - TypeScript Cloud Function
- Story 4.5: Real Data Integration
"""

from config.safe_logging import safe_error_text

import asyncio
import os
import time
from uuid import uuid4
from typing import Dict, List, Optional, Any
import structlog

import httpx
from firebase_admin import firestore

from config.settings import settings
from services.cost_execution import bounded_storage_call

logger = structlog.get_logger()

# =============================================================================
# Constants
# =============================================================================

# Firebase Functions URL configuration
COMPARE_PRICES_FUNCTION = "comparePrices"
FUNCTION_TIMEOUT_SECONDS = 20  # Optional enrichment, not the Node deployment limit
POLL_INTERVAL_SECONDS = 2  # Check Firestore every 2 seconds
MAX_POLL_DURATION_SECONDS = 20  # Also bounded by the shared enrichment deadline


# =============================================================================
# Helper Functions
# =============================================================================


def _get_firestore_client():
    """Get Firestore client instance."""
    try:
        return firestore.client()
    except Exception as e:
        logger.warning("firestore_client_unavailable", error=safe_error_text(e))
        return None


def _build_function_url(function_name: str) -> str:
    """Build Firebase Cloud Function URL.
    
    Args:
        function_name: Name of the Cloud Function
        
    Returns:
        Full URL to the function endpoint
    """
    from config.production import is_production, require_https_url
    if is_production():
        if function_name != 'comparePrices':
            raise ValueError('Unsupported private pricing operation')
        return require_https_url(os.getenv('PRICING_SERVICE_URL'), 'PRICING_SERVICE_URL')
    project = settings.firebase_project_id or os.getenv("GCLOUD_PROJECT") or "collabcanvas-dev"
    local = (settings.use_firebase_emulators or
             bool(os.getenv("FIRESTORE_EMULATOR_HOST")) or
             os.getenv("FUNCTIONS_EMULATOR", "false").lower() == "true")
    base = (f"http://127.0.0.1:5001/{project}/us-central1" if local else
            f"https://us-central1-{project}.cloudfunctions.net")
    return f"{base}/{function_name}"


async def _call_cloud_function(
    function_name: str,
    data: Dict[str, Any],
    timeout: float = FUNCTION_TIMEOUT_SECONDS
) -> Dict[str, Any]:
    """Call Firebase Cloud Function via HTTP.
    
    Args:
        function_name: Name of the Cloud Function
        data: Request payload
        timeout: Request timeout in seconds
        
    Returns:
        Response data from function
        
    Raises:
        httpx.HTTPError: On HTTP errors
        httpx.TimeoutException: On timeout
    """
    url = _build_function_url(function_name)
    
    logger.info(
        "calling_cloud_function",
        function=function_name,
        url=url,
        data_keys=list(data.keys())
    )
    
    from config.production import is_production
    headers = {"Content-Type": "application/json"}
    if is_production():
        from services.service_identity import service_headers
        headers = await service_headers(url)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=False) as client:
        response = await client.post(
            url,
            json=data,
            headers=headers
        )
        response.raise_for_status()
        return response.json()


async def _poll_firestore_for_completion(
    project_id: str,
    max_duration: float = MAX_POLL_DURATION_SECONDS
) -> Optional[Dict[str, Any]]:
    """Poll Firestore for price comparison completion.
    
    The TypeScript function writes progress incrementally to:
    /projects/{projectId}/priceComparison/latest
    
    Args:
        project_id: Project ID to check
        max_duration: Maximum time to poll in seconds
        
    Returns:
        Firestore document data when complete, or None if timeout/error
    """
    db = _get_firestore_client()
    if not db:
        logger.warning("firestore_unavailable_for_polling", project_id=project_id)
        return None
    
    doc_ref = db.collection("projects").document(project_id) \
        .collection("priceComparison").document("latest")
    
    start_time = time.monotonic()
    
    while time.monotonic() - start_time < max_duration:
        try:
            remaining = max_duration - (time.monotonic() - start_time)
            doc = await bounded_storage_call(
                lambda: doc_ref.get(retry=None, timeout=min(2.0, remaining))
            )
            if not doc.exists:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue
            
            data = doc.to_dict()
            status = data.get("status")
            
            if status == "complete":
                logger.info(
                    "price_comparison_complete",
                    project_id=project_id,
                    total_products=data.get("totalProducts", 0),
                    completed_products=data.get("completedProducts", 0)
                )
                return data
            
            if status == "error":
                error_msg = data.get("error", "Unknown error")
                logger.warning(
                    "price_comparison_error",
                    project_id=project_id,
                    error=error_msg
                )
                return None
            
            # Still processing - wait and check again
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            
        except Exception as e:
            logger.warning(
                "firestore_poll_error",
                project_id=project_id,
                error=safe_error_text(e)
            )
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
    
    logger.warning(
        "price_comparison_timeout",
        project_id=project_id,
        duration=max_duration
    )
    return None


def _extract_prices_from_results(
    results: List[Dict[str, Any]]
) -> Dict[str, float]:
    """Extract best prices from comparison results.
    
    Args:
        results: List of ComparisonResult objects from Firestore
        
    Returns:
        Dict mapping product_name -> best_price (lowest of Home Depot/Lowe's)
    """
    prices = {}
    
    for result in results:
        original_name = result.get("originalProductName", "")
        if not original_name:
            continue
        
        matches = result.get("matches", {})
        best_price_data = result.get("bestPrice")
        
        if best_price_data and best_price_data.get("product"):
            # Use the best price (lowest of the two retailers)
            price = best_price_data["product"].get("price", 0)
            if price > 0:
                prices[original_name] = float(price)
                logger.debug(
                    "extracted_price",
                    product=original_name,
                    price=price,
                    retailer=best_price_data.get("retailer")
                )
        else:
            # No best price - try to get lowest from matches
            home_depot_price = None
            lowes_price = None
            
            hd_match = matches.get("homeDepot", {})
            if hd_match.get("selectedProduct"):
                home_depot_price = hd_match["selectedProduct"].get("price", 0)
            
            lowes_match = matches.get("lowes", {})
            if lowes_match.get("selectedProduct"):
                lowes_price = lowes_match["selectedProduct"].get("price", 0)
            
            # Use the lowest available price
            if home_depot_price and lowes_price:
                prices[original_name] = float(min(home_depot_price, lowes_price))
            elif home_depot_price:
                prices[original_name] = float(home_depot_price)
            elif lowes_price:
                prices[original_name] = float(lowes_price)
    
    return prices


# =============================================================================
# Main Service Functions
# =============================================================================


async def get_material_prices(
    product_names: List[str],
    project_id: str,
    zip_code: Optional[str] = None,
    force_refresh: bool = False,
) -> Dict[str, float]:
    """Bound the complete invocation/read sequence; isolate late Node results.

    Node's callable responds after processing, not on job acceptance. A timeout
    therefore falls back immediately. Its late writes stay in this invocation's
    comparison document and cannot be consumed by a newer Cost attempt.
    """
    if not product_names or not project_id:
        return {}
    comparison_id = f"{project_id}--pricing-{uuid4().hex}"
    try:
        async with asyncio.timeout(settings.price_enrichment_budget_seconds):
            return await _get_material_prices(
                product_names, comparison_id, zip_code, force_refresh
            )
    except TimeoutError:
        logger.warning("price_enrichment_budget_exhausted")
        return {}


async def _get_material_prices(
    product_names: List[str],
    project_id: str,
    zip_code: Optional[str] = None,
    force_refresh: bool = False
) -> Dict[str, float]:
    """Get material prices from price comparison service.
    
    Calls the TypeScript comparePrices Cloud Function, polls Firestore
    for completion, and extracts best prices from results.
    
    Args:
        product_names: List of product names/descriptions to price
        project_id: Project ID (used for Firestore path)
        zip_code: Optional ZIP code for location-specific pricing
        force_refresh: Force refresh even if cached results exist
        
    Returns:
        Dict mapping product_name -> best_price (lowest of Home Depot/Lowe's)
        Empty dict if function fails or no prices found
        
    Example:
        >>> prices = await get_material_prices(
        ...     ["Kitchen Cabinets", "Granite Countertops"],
        ...     "project-123",
        ...     zip_code="80202"
        ... )
        >>> prices["Kitchen Cabinets"]
        225.0
    """
    if not product_names:
        logger.warning("get_material_prices_empty_list")
        return {}
    
    if not project_id:
        logger.warning("get_material_prices_no_project_id")
        return {}
    
    start_time = time.time()
    
    try:
        # 1. Call Cloud Function to trigger price comparison
        # Firebase onCall functions expect data wrapped in "data" property
        function_data = {
            "data": {
                "request": {
                    "projectId": project_id,
                    "productNames": product_names,
                    "forceRefresh": force_refresh,
                    "zipCode": zip_code,
                }
            }
        }
        
        logger.info(
            "triggering_price_comparison",
            project_id=project_id,
            product_count=len(product_names),
            zip_code=zip_code
        )
        
        response = await _call_cloud_function(
            COMPARE_PRICES_FUNCTION,
            function_data,
            timeout=FUNCTION_TIMEOUT_SECONDS
        )
        
        # Check if cached results were returned immediately
        if response.get("cached"):
            logger.info("price_comparison_cached", project_id=project_id)
            # Still need to read from Firestore to get the cached results
        
        # 2. Poll Firestore for completion
        firestore_data = await _poll_firestore_for_completion(project_id)
        
        if not firestore_data:
            logger.warning(
                "price_comparison_failed_or_timeout",
                project_id=project_id
            )
            return {}
        
        # 3. Extract prices from results
        results = firestore_data.get("results", [])
        if not results:
            logger.warning(
                "price_comparison_no_results",
                project_id=project_id
            )
            return {}
        
        prices = _extract_prices_from_results(results)
        
        duration_ms = (time.time() - start_time) * 1000
        logger.info(
            "material_prices_retrieved",
            project_id=project_id,
            products_requested=len(product_names),
            prices_found=len(prices),
            duration_ms=round(duration_ms, 2)
        )
        
        return prices
        
    except httpx.HTTPError as e:
        duration_ms = (time.time() - start_time) * 1000
        logger.warning(
            "price_comparison_http_error",
            project_id=project_id,
            error=safe_error_text(e),
            duration_ms=round(duration_ms, 2)
        )
        return {}
    
    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        logger.error(
            "price_comparison_unexpected_error",
            project_id=project_id,
            error=safe_error_text(e),
            duration_ms=round(duration_ms, 2)
        )
        return {}

