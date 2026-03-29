"""
Recipe loader — discovers, loads, validates, and caches YAML recipe files.

Recipes are loaded from the ``recipes/`` directory relative to the project root.
Each ``.yaml`` file defines a single Recipe pipeline.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from core.logging import setup_logger
from core.recipe import Recipe, RecipeParameter, RecipeStep, ParamType

logger = setup_logger("recipe_loader")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RECIPES_DIR = PROJECT_ROOT / "recipes"


def _parse_parameter(name: str, raw: dict) -> RecipeParameter:
    """Parse a raw YAML parameter dict into a RecipeParameter."""
    param_type = raw.get("type", "string")
    try:
        ptype = ParamType(param_type)
    except ValueError:
        ptype = ParamType.STRING

    return RecipeParameter(
        name=name,
        type=ptype,
        required=raw.get("required", False),
        default=raw.get("default"),
        description=raw.get("description"),
        choices=raw.get("choices"),
    )


def _parse_step(raw: dict) -> RecipeStep:
    """Parse a raw YAML step dict into a RecipeStep."""
    inputs = raw.get("input", raw.get("inputs", {}))
    outputs = raw.get("output", raw.get("outputs", {}))
    depends = raw.get("depends_on", [])
    if isinstance(depends, str):
        depends = [depends]

    return RecipeStep(
        id=raw["id"],
        tool=raw["tool"],
        condition=raw.get("condition"),
        inputs=inputs,
        outputs=outputs,
        depends_on=depends,
        checkpoint=raw.get("checkpoint", False),
        description=raw.get("description"),
    )


def load_recipe_from_file(path: Path) -> Recipe:
    """Load and parse a single recipe YAML file."""
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not data:
        raise ValueError(f"Empty recipe file: {path}")

    # Parse parameters
    params_raw = data.get("parameters", {})
    parameters = []
    if isinstance(params_raw, dict):
        for pname, pdef in params_raw.items():
            if isinstance(pdef, dict):
                parameters.append(_parse_parameter(pname, pdef))
            else:
                parameters.append(RecipeParameter(
                    name=pname, type=ParamType.STRING, default=pdef,
                ))
    elif isinstance(params_raw, list):
        for item in params_raw:
            if isinstance(item, dict) and "name" in item:
                parameters.append(_parse_parameter(item["name"], item))

    # Parse steps
    steps_raw = data.get("steps", [])
    steps = [_parse_step(s) for s in steps_raw]

    recipe = Recipe(
        id=data.get("id", path.stem),
        name=data.get("name", path.stem.replace("_", " ").title()),
        description=data.get("description"),
        version=data.get("version", "1.0.0"),
        tags=data.get("tags", []),
        keywords=data.get("keywords", []),
        parameters=parameters,
        steps=steps,
    )

    # Validate DAG
    err = recipe.validate_dag()
    if err:
        raise ValueError(f"Invalid recipe '{recipe.id}': {err}")

    return recipe


class RecipeRegistry:
    """
    Singleton registry that discovers and caches recipe files.

    Usage::

        registry = RecipeRegistry.get_instance()
        recipe = registry.get_recipe("basic_captions")
        all_recipes = registry.list_recipes()
    """

    _instance: Optional[RecipeRegistry] = None

    def __init__(self, recipes_dir: Optional[Path] = None):
        self._recipes_dir = recipes_dir or DEFAULT_RECIPES_DIR
        self._recipes: Dict[str, Recipe] = {}
        self._loaded = False

    @classmethod
    def get_instance(cls, recipes_dir: Optional[Path] = None) -> RecipeRegistry:
        if cls._instance is None:
            cls._instance = cls(recipes_dir)
        return cls._instance

    @classmethod
    def reset(cls):
        """Reset singleton (useful for testing)."""
        cls._instance = None

    def _ensure_loaded(self):
        if not self._loaded:
            self.load_all()

    def load_all(self):
        """Discover and load all recipe YAML files."""
        self._recipes.clear()

        if not self._recipes_dir.exists():
            logger.warning(f"Recipes directory not found: {self._recipes_dir}")
            self._loaded = True
            return

        for yaml_file in sorted(self._recipes_dir.glob("*.yaml")):
            try:
                recipe = load_recipe_from_file(yaml_file)
                self._recipes[recipe.id] = recipe
                logger.info(f"Loaded recipe: {recipe.id} ({recipe.name})")
            except Exception as e:
                logger.error(f"Failed to load recipe {yaml_file.name}: {e}")

        # Also load .yml files
        for yml_file in sorted(self._recipes_dir.glob("*.yml")):
            if yml_file.stem not in self._recipes:
                try:
                    recipe = load_recipe_from_file(yml_file)
                    self._recipes[recipe.id] = recipe
                    logger.info(f"Loaded recipe: {recipe.id} ({recipe.name})")
                except Exception as e:
                    logger.error(f"Failed to load recipe {yml_file.name}: {e}")

        self._loaded = True
        logger.info(f"Recipe registry: {len(self._recipes)} recipe(s) loaded")

    def get_recipe(self, recipe_id: str) -> Optional[Recipe]:
        """Get a recipe by ID."""
        self._ensure_loaded()
        return self._recipes.get(recipe_id)

    def list_recipes(self) -> List[Recipe]:
        """Get all loaded recipes."""
        self._ensure_loaded()
        return list(self._recipes.values())

    def find_by_keyword(self, keyword: str) -> List[Recipe]:
        """Find recipes matching a keyword."""
        self._ensure_loaded()
        keyword_lower = keyword.lower()
        matches = []
        for recipe in self._recipes.values():
            recipe_keywords = [k.lower() for k in recipe.keywords]
            recipe_tags = [t.lower() for t in recipe.tags]
            if (
                keyword_lower in recipe_keywords
                or keyword_lower in recipe_tags
                or keyword_lower in recipe.id.lower()
                or keyword_lower in (recipe.name or "").lower()
            ):
                matches.append(recipe)
        return matches

    def find_by_prompt(self, prompt: str) -> List[Recipe]:
        """Find recipes matching words in a user prompt."""
        self._ensure_loaded()
        prompt_lower = prompt.lower()
        scored: List[tuple] = []

        for recipe in self._recipes.values():
            score = 0
            matched = []

            for kw in recipe.keywords:
                if kw.lower() in prompt_lower:
                    score += 2
                    matched.append(kw)

            for tag in recipe.tags:
                if tag.lower() in prompt_lower:
                    score += 1
                    matched.append(tag)

            if recipe.id.replace("_", " ") in prompt_lower:
                score += 3
                matched.append(recipe.id)

            if score > 0:
                scored.append((score, matched, recipe))

        # Sort by score descending
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, _, r in scored]
