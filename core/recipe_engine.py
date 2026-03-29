"""
Recipe engine — resolves parameters, evaluates conditions, and translates
recipe steps into ExecutionPlan jobs for the existing Orchestrator.

This is the bridge between declarative YAML recipes and the runtime execution
engine. It does NOT re-implement orchestration; it produces ExecutionPlan
objects that the Orchestrator already knows how to execute.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.logging import setup_logger
from core.recipe import Recipe, RecipeMatch, RecipeParameter, RecipeStep
from core.recipe_loader import RecipeRegistry
from core.schema import ExecutionPlan, Job, JobType

logger = setup_logger("recipe_engine")


# ── Parameter resolution ────────────────────────────────────────

def _resolve_value(
    value: Any,
    params: Dict[str, Any],
    step_outputs: Dict[str, Dict[str, str]],
) -> Any:
    """
    Recursively resolve ``{placeholder}`` references in a value.

    Supports:
      - ``{param_name}``           — recipe parameter
      - ``{step_id.output_key}``   — output from a previous step
      - ``{step_id.output_key or fallback}`` — with fallback
      - ``{input_video}`` etc.     — passed through for the orchestrator
    """
    if isinstance(value, str):
        return _resolve_string(value, params, step_outputs)
    if isinstance(value, dict):
        return {
            k: _resolve_value(v, params, step_outputs)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_resolve_value(v, params, step_outputs) for v in value]
    return value


def _resolve_string(
    text: str,
    params: Dict[str, Any],
    step_outputs: Dict[str, Dict[str, str]],
) -> Any:
    """Resolve a single string that may contain {placeholder} references."""
    # If the entire string is a single placeholder, return the raw value (preserve type)
    single_match = re.fullmatch(r'\{([^}]+)\}', text.strip())
    if single_match:
        ref = single_match.group(1).strip()
        resolved = _lookup_ref(ref, params, step_outputs)
        if resolved is not None:
            return resolved

    # Otherwise do inline substitution
    def replacer(m: re.Match) -> str:
        ref = m.group(1).strip()
        val = _lookup_ref(ref, params, step_outputs)
        return str(val) if val is not None else m.group(0)

    return re.sub(r'\{([^}]+)\}', replacer, text)


def _lookup_ref(
    ref: str,
    params: Dict[str, Any],
    step_outputs: Dict[str, Dict[str, str]],
) -> Any:
    """Look up a single reference string."""
    # Handle "X or Y" fallback syntax
    if " or " in ref:
        parts = ref.split(" or ", 1)
        primary = _lookup_ref(parts[0].strip(), params, step_outputs)
        if primary is not None:
            return primary
        return _lookup_ref(parts[1].strip(), params, step_outputs)

    # Step output: step_id.output_key
    if "." in ref:
        step_id, output_key = ref.split(".", 1)
        outputs = step_outputs.get(step_id, {})
        if output_key in outputs:
            return outputs[output_key]
        # Try nested: step_id.output_key.sub_key
        if "." in output_key:
            first, rest = output_key.split(".", 1)
            val = outputs.get(first)
            if isinstance(val, dict):
                return val.get(rest)
        return None

    # Recipe parameter
    if ref in params:
        return params[ref]

    # Orchestrator built-in placeholders — pass through
    if ref in ("input_video", "workspace", "output_dir"):
        return f"{{{ref}}}"

    return None


def _evaluate_condition(
    condition: Optional[str],
    params: Dict[str, Any],
    step_outputs: Dict[str, Dict[str, str]],
) -> bool:
    """
    Evaluate a step condition expression.

    Simple truthiness check: ``{param}`` resolves and is truthy.
    """
    if condition is None:
        return True

    resolved = _resolve_value(condition, params, step_outputs)

    # Handle string booleans
    if isinstance(resolved, str):
        lower = resolved.strip().lower()
        if lower in ("false", "0", "no", "none", "null", ""):
            return False
        # If it still has unresolved placeholders, treat as false
        if re.search(r'\{[^}]+\}', resolved):
            return False
        return True

    return bool(resolved)


# ── ExecutionPlan generation ────────────────────────────────────

def _step_to_job(
    step: RecipeStep,
    recipe: Recipe,
    params: Dict[str, Any],
    step_outputs: Dict[str, Dict[str, str]],
    job_id_prefix: str = "job",
) -> Job:
    """Convert a RecipeStep to an ExecutionPlan Job."""
    # Resolve inputs
    resolved_inputs = _resolve_value(step.inputs, params, step_outputs)

    # Build depends_on list
    deps = set(step.depends_on)
    deps.update(step.get_referenced_steps())
    # Map step IDs to job IDs
    dep_job_ids = [f"{job_id_prefix}_{d}" for d in deps]

    # Map tool name to JobType
    tool_id = step.tool
    try:
        job_type = JobType(tool_id)
    except ValueError:
        job_type = tool_id  # Allow string fallback for extensibility

    return Job(
        job_id=f"{job_id_prefix}_{step.id}",
        job_type=job_type,
        inputs=resolved_inputs,
        outputs=step.outputs,
        depends_on=dep_job_ids,
    )


class RecipeEngine:
    """
    Translates recipes + user parameters into ExecutionPlans.

    The engine resolves parameters, evaluates conditions, handles checkpoints,
    and produces an ExecutionPlan that the existing Orchestrator can execute.
    """

    def __init__(self, recipe_registry: Optional[RecipeRegistry] = None):
        self.registry = recipe_registry or RecipeRegistry.get_instance()
        self._checkpoint_callback: Optional[Callable[[str, str, Dict], bool]] = None

    def set_checkpoint_callback(
        self,
        callback: Callable[[str, str, Dict[str, Any]], bool],
    ):
        """
        Set a callback for checkpoint steps.

        Callback signature: (step_id, description, step_data) -> approve: bool
        """
        self._checkpoint_callback = callback

    def build_plan(
        self,
        recipe_id: str,
        params: Dict[str, Any],
        input_video_path: Optional[str] = None,
    ) -> ExecutionPlan:
        """
        Build an ExecutionPlan from a recipe and user-provided parameters.

        Args:
            recipe_id: ID of the recipe to execute
            params: User-provided parameter values
            input_video_path: Path to input video (injected as a param)

        Returns:
            ExecutionPlan ready for the Orchestrator

        Raises:
            ValueError: If recipe not found or required params missing
        """
        recipe = self.registry.get_recipe(recipe_id)
        if not recipe:
            raise ValueError(f"Recipe not found: {recipe_id}")

        # Merge defaults with user params
        resolved_params = self._merge_params(recipe, params, input_video_path)

        # Validate required params
        missing = self._validate_params(recipe, resolved_params)
        if missing:
            raise ValueError(f"Missing required parameters: {', '.join(missing)}")

        # Build jobs from steps
        step_outputs: Dict[str, Dict[str, str]] = {}
        jobs: List[Job] = []
        skipped_steps: set = set()

        for step in recipe.steps:
            # Evaluate condition
            if not _evaluate_condition(step.condition, resolved_params, step_outputs):
                logger.info(f"Skipping step '{step.id}' (condition not met)")
                skipped_steps.add(step.id)
                continue

            # Skip if depends on a skipped step (unless there's a fallback)
            if any(dep in skipped_steps for dep in step.depends_on):
                logger.info(f"Skipping step '{step.id}' (dependency skipped)")
                skipped_steps.add(step.id)
                continue

            # Checkpoint: ask for approval
            if step.checkpoint and self._checkpoint_callback:
                approved = self._checkpoint_callback(
                    step.id,
                    step.description or f"Approve step: {step.id}",
                    step.inputs,
                )
                if not approved:
                    logger.info(f"Step '{step.id}' rejected at checkpoint")
                    skipped_steps.add(step.id)
                    continue

            # Convert to Job
            job = _step_to_job(
                step, recipe, resolved_params, step_outputs,
                job_id_prefix="job",
            )
            jobs.append(job)

            # Register placeholder outputs for dependency resolution
            step_outputs[step.id] = {
                out_name: f"{{job_{step.id}.outputs.{out_name}}}"
                for out_name in step.outputs
            }

        plan_id = f"recipe_{recipe.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

        return ExecutionPlan(
            plan_id=plan_id,
            intent=f"Recipe: {recipe.name}",
            jobs=jobs,
        )

    def _merge_params(
        self,
        recipe: Recipe,
        user_params: Dict[str, Any],
        input_video_path: Optional[str],
    ) -> Dict[str, Any]:
        """Merge recipe defaults with user-provided parameters."""
        merged: Dict[str, Any] = {}

        # Apply defaults
        for param in recipe.parameters:
            if param.default is not None:
                merged[param.name] = param.default

        # Apply user overrides
        merged.update(user_params)

        # Inject input_video if there's a video-type required param
        if input_video_path:
            for param in recipe.parameters:
                if param.type.value == "video" and param.required and param.name not in merged:
                    merged[param.name] = input_video_path
                    break
            # Also make it available as a generic key
            if "input_video" not in merged:
                merged["input_video"] = input_video_path

        return merged

    def _validate_params(
        self,
        recipe: Recipe,
        params: Dict[str, Any],
    ) -> List[str]:
        """Validate all required parameters are present."""
        missing = []
        for param in recipe.get_required_params():
            if param.name not in params or params[param.name] is None:
                missing.append(param.name)
        return missing

    def match_prompt(self, prompt: str) -> Optional[RecipeMatch]:
        """
        Match a user prompt to the best recipe.

        Returns RecipeMatch with confidence score and extracted params,
        or None if no recipe matches.
        """
        recipes = self.registry.find_by_prompt(prompt)
        if not recipes:
            return None

        best = recipes[0]
        prompt_lower = prompt.lower()

        # Calculate confidence based on keyword matches
        matched_kw = [
            kw for kw in best.keywords if kw.lower() in prompt_lower
        ]
        matched_tags = [
            t for t in best.tags if t.lower() in prompt_lower
        ]

        total_keywords = len(best.keywords) + len(best.tags)
        if total_keywords == 0:
            confidence = 0.5
        else:
            confidence = min(1.0, (len(matched_kw) * 2 + len(matched_tags)) / max(total_keywords, 1))

        # Determine missing params
        missing = [p.name for p in best.get_required_params()]

        return RecipeMatch(
            recipe_id=best.id,
            recipe_name=best.name,
            confidence=confidence,
            matched_keywords=matched_kw + matched_tags,
            missing_params=missing,
        )

    def list_recipes(self) -> List[Dict[str, Any]]:
        """List all available recipes with summary info."""
        return [
            {
                "id": r.id,
                "name": r.name,
                "description": r.description,
                "tags": r.tags,
                "required_params": [p.name for p in r.get_required_params()],
                "optional_params": [p.name for p in r.get_optional_params()],
            }
            for r in self.registry.list_recipes()
        ]
