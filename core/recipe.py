"""
Recipe data models for declarative pipeline definitions.

A Recipe is a YAML-defined pipeline with typed parameters, conditional steps,
dependency ordering, and checkpoint support. Recipes bridge user intent to
the existing Orchestrator by generating ExecutionPlan objects.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator


class ParamType(str, Enum):
    """Supported recipe parameter types."""
    STRING = "string"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    VIDEO = "video"
    AUDIO = "audio"
    IMAGE = "image"
    FILE = "file"
    TIME = "time"
    SIZE = "size"
    POSITION = "position"
    STYLE_PRESET = "style_preset"
    LIST = "list"
    OBJECT = "object"


class RecipeParameter(BaseModel):
    """A single typed parameter for a recipe."""
    name: str = Field(description="Parameter name (used as reference key)")
    type: ParamType = Field(description="Parameter data type")
    required: bool = Field(default=False, description="Whether the parameter must be provided")
    default: Optional[Any] = Field(default=None, description="Default value if not provided")
    description: Optional[str] = Field(default=None, description="Human-readable description")
    choices: Optional[List[Any]] = Field(default=None, description="Valid choices for this parameter")

    @model_validator(mode="after")
    def check_required_has_no_default(self):
        if self.required and self.default is not None:
            # Allow it but it's unusual — default is ignored when required
            pass
        return self


class RecipeStepInput(BaseModel):
    """An input mapping for a recipe step."""
    key: str = Field(description="Input parameter name for the tool")
    value: Any = Field(description="Value, which may contain {param} references")


class RecipeStep(BaseModel):
    """A single step in a recipe pipeline."""
    id: str = Field(description="Unique step identifier within the recipe")
    tool: str = Field(description="Tool ID to execute (must match registry)")
    condition: Optional[str] = Field(
        default=None,
        description="Expression that must evaluate truthy for step to run (e.g. '{add_captions}')",
    )
    inputs: Dict[str, Any] = Field(
        default_factory=dict,
        description="Input parameters for the tool, may contain {param} and {step.output} references",
    )
    outputs: Dict[str, str] = Field(
        default_factory=dict,
        description="Map of output names to artifact keys",
    )
    depends_on: List[str] = Field(
        default_factory=list,
        description="Step IDs this step depends on (auto-inferred from input references too)",
    )
    checkpoint: bool = Field(
        default=False,
        description="If True, pause for user approval before continuing",
    )
    description: Optional[str] = Field(
        default=None,
        description="Human-readable description of what this step does",
    )

    def get_referenced_steps(self) -> List[str]:
        """Extract step IDs referenced in input values (e.g. '{step_id.output_name}')."""
        import re
        refs = set()
        for val in self.inputs.values():
            if isinstance(val, str):
                # Match {step_id.output_key} or {step_id.output_key or fallback}
                matches = re.findall(r'\{(\w+)\.(\w+)(?:\s+or\s+[^}]*)?\}', val)
                for step_ref, _ in matches:
                    if step_ref not in ("input_video", "workspace", "output_dir"):
                        refs.add(step_ref)
            elif isinstance(val, dict):
                for v in val.values():
                    if isinstance(v, str):
                        matches = re.findall(r'\{(\w+)\.(\w+)(?:\s+or\s+[^}]*)?\}', v)
                        for step_ref, _ in matches:
                            if step_ref not in ("input_video", "workspace", "output_dir"):
                                refs.add(step_ref)
        return list(refs)


class Recipe(BaseModel):
    """A complete recipe definition — a declarative pipeline."""
    id: str = Field(description="Unique recipe identifier")
    name: str = Field(description="Human-readable recipe name")
    description: Optional[str] = Field(default=None, description="What this recipe does")
    version: str = Field(default="1.0.0", description="Recipe version")
    tags: List[str] = Field(
        default_factory=list,
        description="Tags for discovery (e.g. 'captions', 'reaction', 'music')",
    )
    keywords: List[str] = Field(
        default_factory=list,
        description="Keywords that trigger this recipe from user prompts",
    )
    parameters: List[RecipeParameter] = Field(
        default_factory=list,
        description="Recipe parameters with types and defaults",
    )
    steps: List[RecipeStep] = Field(
        description="Ordered list of steps in the pipeline",
    )

    def get_required_params(self) -> List[RecipeParameter]:
        """Get all required parameters."""
        return [p for p in self.parameters if p.required]

    def get_optional_params(self) -> List[RecipeParameter]:
        """Get all optional parameters with defaults."""
        return [p for p in self.parameters if not p.required]

    def get_param(self, name: str) -> Optional[RecipeParameter]:
        """Get a parameter definition by name."""
        return next((p for p in self.parameters if p.name == name), None)

    def get_step(self, step_id: str) -> Optional[RecipeStep]:
        """Get a step by ID."""
        return next((s for s in self.steps if s.id == step_id), None)

    def build_dependency_graph(self) -> Dict[str, List[str]]:
        """Build a full dependency graph including implicit dependencies from input refs."""
        graph: Dict[str, List[str]] = {}
        for step in self.steps:
            deps = set(step.depends_on)
            deps.update(step.get_referenced_steps())
            graph[step.id] = list(deps)
        return graph

    def validate_dag(self) -> Optional[str]:
        """Validate the recipe forms a valid DAG (no cycles). Returns error or None."""
        graph = self.build_dependency_graph()
        visited: set = set()
        path: set = set()

        def dfs(node: str) -> Optional[str]:
            if node in path:
                return f"Circular dependency detected involving step '{node}'"
            if node in visited:
                return None
            path.add(node)
            for dep in graph.get(node, []):
                err = dfs(dep)
                if err:
                    return err
            path.remove(node)
            visited.add(node)
            return None

        for step_id in graph:
            err = dfs(step_id)
            if err:
                return err
        return None


class RecipeMatch(BaseModel):
    """Result of matching a user prompt to a recipe."""
    recipe_id: str
    recipe_name: str
    confidence: float = Field(ge=0.0, le=1.0)
    matched_keywords: List[str] = Field(default_factory=list)
    missing_params: List[str] = Field(default_factory=list)
    extracted_params: Dict[str, Any] = Field(default_factory=dict)
