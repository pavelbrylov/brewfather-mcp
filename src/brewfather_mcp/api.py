from enum import StrEnum
from typing import Any
import json
import logging
import os
import httpx
import urllib.parse

logger = logging.getLogger(__name__)
from .types import (
    FermentableDetail,
    FermentableList,
    HopDetail,
    HopList,
    InventoryCategory,
    MiscDetail,
    MiscList,
    RecipeDetail,
    RecipeList,
    YeastDetail,
    YeastList,
    BatchDetail,
    BatchList,
)
from .types.brewtracker import BrewTrackerStatus, BatchReadingsList, LastReading

BASE_URL: str = "https://api.brewfather.app/v2"

class OrderByDirection(StrEnum):
    ASCENDING = "asc"
    DESCENDING = "desc"

class ListQueryParams:
    inventory_negative: bool | None = None
    complete: bool | None = None
    inventory_exists: bool | None = None
    limit: int | None = None
    start_after: str | None = None
    order_by: str | None = None
    order_by_direction: OrderByDirection | None = None

    def as_query_param_str(self) -> str | None:
        params = []

        if self.inventory_negative is not None:
            params.append(f"inventory_negative={'true' if self.inventory_negative else 'false'}")

        if self.complete is not None:
            params.append(f"complete={'true' if self.complete else 'false'}")

        if self.inventory_exists is not None:
            params.append(f"inventory_exists={'true' if self.inventory_exists else 'false'}")

        if self.limit:
            params.append(f"limit={self.limit}")

        if self.start_after:
            params.append(f"start_after={urllib.parse.quote_plus(self.start_after)}")

        if self.order_by:
            params.append(f"order_by={urllib.parse.quote_plus(self.order_by)}")

        if self.order_by_direction:
            params.append(f"order_by_direction={self.order_by_direction}")

        if params:
            return "&".join(params)
        else:
            return None

class BrewfatherClient:
    """Client for interacting with the Brewfather API."""

    def __init__(self):
        user_id = os.getenv("BREWFATHER_API_USER_ID")
        api_key = os.getenv("BREWFATHER_API_KEY")

        if not user_id or not api_key:
            raise ValueError(
                "Missing Brewfather credentials in the environment variables: BREWFATHER_API_USER_ID or BREWFATHER_API_KEY"
            )

        self.auth = httpx.BasicAuth(user_id, api_key)
        self.max_pages = 10  # Safety limit to prevent infinite loops

    async def _make_request(self, url: str) -> str:
        async with httpx.AsyncClient(auth=self.auth) as client:
            response = await client.get(url)
            response.raise_for_status()
            # Write response to a file for debugging when debug mode is enabled
            if os.getenv("BREWFATHER_MCP_DEBUG"):
                debug_dir = os.path.join(os.path.dirname(__file__), "..", "..", "debug")
                os.makedirs(debug_dir, exist_ok=True)
                debug_filename = url[len(BASE_URL) + 1:].split('?')[0].replace("/", "_").replace(":", "_") + ".json"
                debug_path = os.path.join(debug_dir, debug_filename)
                with open(debug_path, "w") as debug_file:
                    debug_file.write(response.text)
            return response.text

    async def _make_patch_request(self, url: str, data: dict) -> None:
        async with httpx.AsyncClient(auth=self.auth) as client:
            response = await client.patch(url, json=data)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                detail = response.text.strip()
                if detail:
                    raise httpx.HTTPStatusError(
                        f"{exc} Response body: {detail}",
                        request=exc.request,
                        response=exc.response,
                    ) from exc
                raise

    async def _make_post_request(self, url: str, data: dict) -> str:
        async with httpx.AsyncClient(auth=self.auth) as client:
            response = await client.post(url, json=data)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                detail = response.text.strip()
                if detail:
                    raise httpx.HTTPStatusError(
                        f"{exc} Response body: {detail}",
                        request=exc.request,
                        response=exc.response,
                    ) from exc
                raise
            return response.text

    def _build_url(
        self,
        endpoint: str,
        id: str | None = None,
        query_params: ListQueryParams | None = None,
    ) -> str:
        """Build a URL for the Brewfather API.

        Args:
            endpoint: The API endpoint (e.g., 'recipes', 'batches', 'inventory/fermentables')
            id: Optional ID for detail endpoints
            query_params: Optional query parameters
        """
        url = f"{BASE_URL}/{endpoint}"
        if id:
            url = f"{url}/{id}"
        if query_params:
            url += f"?{query_params.as_query_param_str()}"
        return url

    async def _get_paginated_list(
        self,
        endpoint: str,
        model_class,
        query_params: ListQueryParams | None = None,
    ):
        """Fetch all pages of a list endpoint using cursor pagination.

        Args:
            endpoint: The API endpoint to query
            model_class: The Pydantic model class to validate responses
            query_params: Query parameters including filters and limit

        Returns:
            A model instance with all results from all pages
        """
        all_items = []
        current_params = query_params or ListQueryParams()

        # Set a reasonable limit per page if not specified
        if not current_params.limit:
            current_params.limit = 50

        page_count = 0
        while page_count < self.max_pages:
            url = self._build_url(endpoint, query_params=current_params)
            json_response = await self._make_request(url)
            page_result = model_class.model_validate_json(json_response)

            # Add items from this page
            all_items.extend(page_result.root)

            # Check if there are more pages
            # If we got fewer items than the limit, we've reached the end
            if len(page_result.root) < current_params.limit:
                break

            # Set start_after to the ID of the last item for next page
            if page_result.root:
                current_params.start_after = page_result.root[-1].id
            else:
                break

            page_count += 1

        if page_count >= self.max_pages:
            logger.warning(
                f"Reached max page limit ({self.max_pages}) for endpoint '{endpoint}'. "
                f"Total items fetched: {len(all_items)}. There may be more items available."
            )

        logger.info(f"Fetched {len(all_items)} total items from '{endpoint}' across {page_count + 1} page(s)")

        # Return a new model instance with all collected items
        return model_class(root=all_items)

    # Inventory endpoints
    async def get_fermentables_list(
        self, query_params: ListQueryParams | None = None
    ) -> FermentableList:
        return await self._get_paginated_list(
            f"inventory/{InventoryCategory.FERMENTABLES}",
            FermentableList,
            query_params
        )

    async def get_fermentable_detail(self, id: str) -> FermentableDetail:
        url = self._build_url(
            f"inventory/{InventoryCategory.FERMENTABLES}", id=id
        )
        json_response = await self._make_request(url)
        return FermentableDetail.model_validate_json(json_response)

    async def update_fermentable_inventory(self, id: str, inventory: float) -> None:
        url = self._build_url(
            f"inventory/{InventoryCategory.FERMENTABLES}", id=id
        )
        await self._make_patch_request(url, {"inventory": inventory})

    # Batch endpoints
    async def get_batches_list(
        self, query_params: ListQueryParams | None = None
    ) -> BatchList:
        return await self._get_paginated_list("batches", BatchList, query_params)

    async def get_batch_detail(self, id: str) -> BatchDetail:
        url = self._build_url("batches", id=id)
        json_response = await self._make_request(url)
        return BatchDetail.model_validate_json(json_response)

    async def update_batch_detail(self, id: str, data: dict) -> None:
        url = self._build_url("batches", id=id)
        await self._make_patch_request(url, data)

    # Recipe endpoints
    async def get_recipes_list(
        self, query_params: ListQueryParams | None = None
    ) -> RecipeList:
        return await self._get_paginated_list("recipes", RecipeList, query_params)

    async def get_recipe_detail(self, id: str) -> RecipeDetail:
        url = self._build_url("recipes", id=id)
        json_response = await self._make_request(url)
        return RecipeDetail.model_validate_json(json_response)

    async def _resolve_equipment_profile(self, name: str) -> dict[str, Any] | None:
        """Find a complete equipment profile from an existing recipe."""
        query_params = ListQueryParams()
        query_params.limit = 50
        query_params.complete = True
        recipes = await self.get_recipes_list(query_params)
        wanted = self._recipe_name_key(name)
        for recipe in recipes.root:
            if not recipe.equipment or self._recipe_name_key(recipe.equipment.name) != wanted:
                continue
            raw = json.loads(await self._make_request(self._build_url("recipes", id=recipe.id)))
            equipment = raw.get("equipment")
            if isinstance(equipment, dict) and equipment.get("mashWaterFormula"):
                return equipment
        return None

    @staticmethod
    def _normalise_water_settings(
        water: dict[str, Any], miscs: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Expand concise water input into Brewfather's complete water object.

        The recipe API does not calculate missing water fields.  In particular,
        sending only ``source``, ``dilution`` and ``dilutionPercentage`` saves a
        recipe for which the UI cannot select or calculate dilution.  Accept the
        ergonomic input used by MCP callers and emit the state Brewfather stores
        after selecting a dilution in its UI.
        """
        result = dict(water)

        source = result.get("sourceProfile", result.get("source"))
        dilution = result.get("dilution")
        if not isinstance(source, dict) or not isinstance(dilution, dict):
            return result

        minerals = (
            "calcium", "magnesium", "sodium", "chloride", "sulfate",
            "bicarbonate",
        )

        def profile(value: dict[str, Any] | None, *, name: str, kind: str,
                    defaults: dict[str, Any] | None = None) -> dict[str, Any]:
            item = dict(value or {})
            item.pop("volume", None)
            item["name"] = item.get("name", name)
            item["type"] = str(item.get("type", kind)).lower()
            if item["type"] not in {"source", "target"}:
                item["type"] = kind
            for mineral in minerals:
                item.setdefault(mineral, (defaults or {}).get(mineral, 0))
            return item

        source = profile(source, name="Source Water", kind="source")
        dilution = profile(dilution, name="Distilled Water", kind="source")
        dilution_percentage = float(result.get("dilutionPercentage", 0))
        source_fraction = max(0, min(100, 100 - dilution_percentage)) / 100
        diluted = {
            mineral: source[mineral] * source_fraction
            + dilution[mineral] * (1 - source_fraction)
            for mineral in minerals
        }

        target_input = result.get("targetProfile")
        target = profile(
            target_input if isinstance(target_input, dict) else result.get("total"),
            name="Total Water", kind="target", defaults=diluted,
        )

        def water_amount(key: str, fallback: float) -> float:
            value = result.get(key)
            if isinstance(value, (int, float)):
                return float(value)
            return fallback

        mash_amount = water_amount(
            "mashWaterAmount",
            water_amount("mash", water_amount("total", 0)),
        )
        sparge_amount = water_amount("spargeWaterAmount", water_amount("sparge", 0))
        total_amount = mash_amount + sparge_amount

        adjustments_input = result.get("adjustments", {})
        mash_adjustments_input = result.get("mashAdjustments", adjustments_input)
        total_adjustments_input = result.get("totalAdjustments", adjustments_input)

        acid = result.get("acid")
        if not isinstance(acid, dict):
            for misc in miscs or []:
                if (
                    isinstance(misc, dict)
                    and BrewfatherClient._recipe_name_key(misc.get("name")) == "lactic acid"
                    and BrewfatherClient._recipe_name_key(misc.get("use")) == "mash"
                ):
                    acid = {
                        "type": "lactic",
                        "amount": misc.get("amount", 0),
                        "concentration": misc.get("concentration", 80),
                        "unit": misc.get("unit", "ml"),
                    }
                    break

        def adjustments(value: Any, volume: float) -> dict[str, Any]:
            adjustment = dict(value) if isinstance(value, dict) else {}
            adjustment["volume"] = volume
            for mineral in minerals:
                adjustment.setdefault(mineral, 0)
            return adjustment

        mash_adjustments = adjustments(mash_adjustments_input, mash_amount)
        total_adjustments = adjustments(total_adjustments_input, total_amount)
        if isinstance(acid, dict):
            acid_adjustment = {
                "type": acid.get("type", "lactic"),
                "amount": acid.get("amount", 0),
                "concentration": acid.get("concentration", 80),
                "unit": acid.get("unit", "ml"),
            }
            mash_adjustments["acids"] = [acid_adjustment]
            total_adjustments["acids"] = [acid_adjustment]

        result = {
            key: value for key, value in result.items()
            if key not in {"sourceProfile", "targetProfile", "adjustments"}
        }
        result.update({
            "source": source,
            "dilution": dilution,
            "diluted": profile(diluted, name="Diluted Water", kind="target"),
            "mash": profile(result.get("mash") if isinstance(result.get("mash"), dict) else target,
                            name="Mash Water", kind="target", defaults=target),
            "sparge": profile(result.get("sparge") if isinstance(result.get("sparge"), dict) else target,
                              name="Sparge Water", kind="target", defaults=target),
            "total": profile(result.get("total") if isinstance(result.get("total"), dict) else target,
                             name="Total Water", kind="target", defaults=target),
            "mashWaterAmount": mash_amount,
            "spargeWaterAmount": sparge_amount,
            "dilutionPercentage": dilution_percentage,
            "dilutionAmount": total_amount * dilution_percentage / 100,
            "mashAdjustments": mash_adjustments,
            "spargeAdjustments": adjustments(result.get("spargeAdjustments"), sparge_amount),
            "totalAdjustments": total_adjustments,
            "enableSpargeAdjustments": result.get("enableSpargeAdjustments", sparge_amount > 0),
            "enableSpargeAcidAdjustments": result.get("enableSpargeAcidAdjustments", sparge_amount > 0),
            "enableAcidAdjustments": result.get("enableAcidAdjustments", True),
            "mashPh": result.get("mashPh", 5.4),
            "acidPhAdjustment": result.get("acidPhAdjustment", 0),
            "spargeAcidPhAdjustment": result.get("spargeAcidPhAdjustment", 5.4),
            "meta": {**result.get("meta", {}), "equalSourceTotal": False},
        })
        return result

    async def _enrich_recipe_profile(self, data: dict[str, Any]) -> dict[str, Any]:
        """Enrich caller-friendly recipe input with Brewfather profile data."""
        enriched_data = dict(data)
        equipment = enriched_data.get("equipment")
        if isinstance(equipment, dict):
            name = equipment.get("name")
            if name and not equipment.get("mashWaterFormula"):
                resolved = await self._resolve_equipment_profile(name)
                if resolved:
                    enriched_data["equipment"] = resolved
        water = enriched_data.get("water")
        if isinstance(water, dict):
            miscs = enriched_data.get("miscs")
            enriched_data["water"] = self._normalise_water_settings(
                water, miscs if isinstance(miscs, list) else None,
            )
        return enriched_data

    @staticmethod
    def _recipe_name_key(name: Any) -> str:
        return " ".join(str(name or "").casefold().split())

    async def _link_recipe_inventory(self, data: dict[str, Any]) -> dict[str, Any]:
        """Attach inventory IDs to exact-name recipe ingredient matches."""
        linked_data = await self._enrich_recipe_profile(data)
        ingredient_sources = (
            ("fermentables", self.get_fermentables_list),
            ("hops", self.get_hops_list),
            ("yeasts", self.get_yeasts_list),
            ("miscs", self.get_miscs_list),
        )

        for field, getter in ingredient_sources:
            ingredients = linked_data.get(field)
            if not isinstance(ingredients, list) or not ingredients:
                continue

            if field == "fermentables":
                total_amount = sum(
                    ingredient.get("amount", 0)
                    for ingredient in ingredients
                    if isinstance(ingredient, dict)
                    and isinstance(ingredient.get("amount"), (int, float))
                )
                if total_amount > 0:
                    ingredients = [
                        {
                            **ingredient,
                            "percentage": ingredient.get("percentage")
                            if ingredient.get("percentage") is not None
                            else round(ingredient.get("amount", 0) / total_amount * 100, 4),
                        }
                        if isinstance(ingredient, dict)
                        else ingredient
                        for ingredient in ingredients
                    ]
                    linked_data[field] = ingredients

            inventory = await getter()
            by_name: dict[str, list[Any]] = {}
            for item in inventory.root:
                if item.name:
                    by_name.setdefault(self._recipe_name_key(item.name), []).append(item)
            enriched = []
            for ingredient in ingredients:
                ingredient = dict(ingredient)
                candidates = by_name.get(self._recipe_name_key(ingredient.get("name")), [])
                requested_supplier = self._recipe_name_key(ingredient.get("supplier"))
                if requested_supplier:
                    match = next(
                        (
                            item for item in candidates
                            if self._recipe_name_key(getattr(item, "supplier", None))
                            == requested_supplier
                        ),
                        None,
                    )
                else:
                    match = candidates[0] if candidates else None
                if match:
                    ingredient["_id"] = match.id
                    ingredient["name"] = match.name
                    ingredient["type"] = match.type
                    match_supplier = getattr(match, "supplier", None)
                    if match_supplier is not None:
                        ingredient["supplier"] = match_supplier
                    if field == "hops":
                        ingredient.setdefault("alpha", match.alpha)
                    elif field == "yeasts":
                        ingredient.setdefault("attenuation", match.attenuation)
                        if match.form:
                            ingredient.setdefault("form", match.form)
                    elif field == "miscs":
                        detail = await self.get_misc_detail(match.id)
                        detail_values = detail.model_dump(by_alias=True, exclude_none=True)
                        for key in ("unit", "concentration", "amountPerL", "waterAdjustment"):
                            value = detail_values.get(key)
                            if value is not None:
                                ingredient[key] = value
                    elif field == "fermentables":
                        # List responses omit technical values such as EBC/color.
                        # Hydrate them when the caller supplied an existing/generic
                        # ingredient or an explicit zero/empty color.
                        if ingredient.get("color") in (None, 0, 0.0) or ingredient.get("potential") is None:
                            detail = await self.get_fermentable_detail(match.id)
                            detail_values = detail.model_dump(by_alias=True, exclude_none=True)
                            for key in (
                                "color", "potential", "potentialPercentage",
                                "grainCategory", "attenuation", "origin",
                            ):
                                value = detail_values.get(key)
                                if value is not None:
                                    ingredient[key] = value
                enriched.append(ingredient)
            linked_data[field] = enriched

        return linked_data

    async def create_recipe(self, data: dict[str, Any]) -> dict[str, Any]:
        """Create a recipe and return Brewfather's generated recipe ID."""
        url = self._build_url("recipes")
        response = await self._make_post_request(url, await self._link_recipe_inventory(data))
        return json.loads(response) if response else {}

    async def update_recipe(self, id: str, data: dict[str, Any]) -> None:
        """Update an existing recipe through Brewfather's API."""
        url = self._build_url("recipes", id=id)
        await self._make_patch_request(url, await self._link_recipe_inventory(data))

    async def delete_recipe(self, id: str) -> None:
        """Delete an existing recipe through Brewfather's API."""
        url = self._build_url("recipes", id=id)
        async with httpx.AsyncClient(auth=self.auth) as client:
            response = await client.delete(url)
            response.raise_for_status()

    # Add similar patterns for other inventory types (hops, yeasts, miscs)...
    async def get_hops_list(
        self, query_params: ListQueryParams | None = None
    ) -> HopList:
        return await self._get_paginated_list(
            f"inventory/{InventoryCategory.HOPS}",
            HopList,
            query_params
        )
    
    async def get_hop_detail(self, id: str) -> HopDetail:
        url = self._build_url(
            f"inventory/{InventoryCategory.HOPS}", id=id
        )
        json_response = await self._make_request(url)
        return HopDetail.model_validate_json(json_response)
    
    async def update_hop_inventory(self, id: str, inventory: float) -> None:
        url = self._build_url(
            f"inventory/{InventoryCategory.HOPS}", id=id
        )
        await self._make_patch_request(url, {"inventory": inventory})

    async def get_yeasts_list(
        self, query_params: ListQueryParams | None = None
    ) -> YeastList:
        return await self._get_paginated_list(
            f"inventory/{InventoryCategory.YEASTS}",
            YeastList,
            query_params
        )
    
    async def get_yeast_detail(self, id: str) -> YeastDetail:
        url = self._build_url(
            f"inventory/{InventoryCategory.YEASTS}", id=id
        )
        json_response = await self._make_request(url)
        return YeastDetail.model_validate_json(json_response)
    
    async def update_yeast_inventory(self, id: str, inventory: float) -> None:
        url = self._build_url(
            f"inventory/{InventoryCategory.YEASTS}", id=id
        )
        await self._make_patch_request(url, {"inventory": inventory})

    async def get_miscs_list(
        self, query_params: ListQueryParams | None = None
    ) -> MiscList:
        return await self._get_paginated_list(
            f"inventory/{InventoryCategory.MISCS}",
            MiscList,
            query_params
        )

    async def get_misc_detail(self, id: str) -> MiscDetail:
        url = self._build_url(
            f"inventory/{InventoryCategory.MISCS}", id=id
        )
        json_response = await self._make_request(url)
        return MiscDetail.model_validate_json(json_response)
    
    async def update_misc_inventory(self, id: str, inventory: float) -> None:
        url = self._build_url(
            f"inventory/{InventoryCategory.MISCS}", id=id
        )
        await self._make_patch_request(url, {"inventory": inventory})

    # Brewtracker endpoints
    async def get_batch_brewtracker(self, batch_id: str) -> BrewTrackerStatus:
        """Get brewtracker status for a batch"""
        url = self._build_url("batches", id=f"{batch_id}/brewtracker")
        json_response = await self._make_request(url)
        return BrewTrackerStatus.model_validate_json(json_response)
    
    async def get_batch_readings(self, batch_id: str) -> BatchReadingsList:
        """Get all readings for a batch"""
        url = self._build_url("batches", id=f"{batch_id}/readings")
        json_response = await self._make_request(url)
        return BatchReadingsList.model_validate_json(json_response)
    
    async def get_batch_last_reading(self, batch_id: str) -> LastReading:
        """Get last reading for a batch"""
        url = self._build_url("batches", id=f"{batch_id}/readings/last")
        json_response = await self._make_request(url)
        return LastReading.model_validate_json(json_response)
