"""
LLM planner - converts user prompts into structured execution plans.
Uses external prompt files and smart config loading.
"""
import json
import ollama
from pathlib import Path
from typing import Dict, Any, Optional, List
from core.schema import PlannerRequest, PlannerResponse, ExecutionPlan, Job, JobType, CaptionStyle
from core.config_loader import ConfigLoader, get_config_loader
from core.logging import setup_logger

logger = setup_logger("planner")


class PromptLoader:
    """Loads and manages prompt templates from files"""
    
    def __init__(self, prompts_dir: Optional[Path] = None):
        if prompts_dir:
            self.prompts_dir = prompts_dir
        else:
            self.prompts_dir = Path(__file__).parent.parent / "prompts"
        
        self._cache: Dict[str, str] = {}
    
    def load(self, prompt_name: str) -> str:
        """Load a prompt file by name (without .txt extension)"""
        if prompt_name in self._cache:
            return self._cache[prompt_name]
        
        # Try .txt first, then .md
        for ext in [".txt", ".md"]:
            prompt_path = self.prompts_dir / f"{prompt_name}{ext}"
            if prompt_path.exists():
                with open(prompt_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    self._cache[prompt_name] = content
                    return content
        
        logger.warning(f"Prompt file not found: {prompt_name}")
        return ""
    
    def clear_cache(self):
        """Clear the prompt cache"""
        self._cache.clear()


class Planner:
    """
    Uses LLM to generate execution plans from user prompts.
    
    Features:
    - External prompt templates from files
    - Smart config loading (only loads relevant configs)
    - Tool registry integration
    - Scalable design for adding new features
    """
    
    def __init__(
        self, 
        model_name: str = "llama3:latest", 
        base_path: Optional[str] = None
    ):
        """
        Initialize the planner.
        
        Args:
            model_name: Ollama model name to use
            base_path: Base path to editbot directory (auto-detected if None)
        """
        self.model_name = model_name
        
        # Initialize loaders
        if base_path:
            base = Path(base_path)
            self.config_loader = ConfigLoader(base_path)
            self.prompt_loader = PromptLoader(base / "prompts")
        else:
            self.config_loader = get_config_loader()
            self.prompt_loader = PromptLoader()
        
        logger.info(f"Initialized planner with model: {model_name}")
    
    def _build_system_prompt(self, user_prompt: str) -> str:
        """
        Build the system prompt dynamically based on user's request.
        Only includes relevant configurations.
        """
        # Load base system prompt
        system_prompt = self.prompt_loader.load("system_prompt")
        
        # Get relevant configs based on user prompt
        relevant = self.config_loader.get_relevant_configs(user_prompt)
        configs = relevant["configs"]
        
        # Get tools summary
        tools_summary = self.config_loader.get_tools_summary()
        
        # Build config context (formatted for LLM)
        config_context = self._format_config_context(configs)
        
        # Combine all parts
        full_prompt = f"""{system_prompt}

## AVAILABLE TOOLS
{tools_summary}

## RELEVANT CONFIGURATION
{config_context}
"""
        return full_prompt
    
    def _format_config_context(self, configs: Dict[str, Any]) -> str:
        """Format configs into a readable context for the LLM"""
        parts = []
        
        for config_name, config_data in configs.items():
            if not config_data:
                continue
            
            if config_name == "fonts":
                font_names = [f["name"] for f in config_data.get("fonts", [])]
                parts.append(f"Available Fonts: {', '.join(font_names)}")
            
            elif config_name == "colors":
                color_names = list(config_data.get("colors", {}).keys())
                parts.append(f"Available Colors: {', '.join(color_names)}")
            
            elif config_name == "highlight_styles":
                style_names = list(config_data.get("styles", {}).keys())
                parts.append(f"Highlight Styles: {', '.join(style_names)}")
            
            elif config_name == "text_styles":
                sizes = list(config_data.get("font_sizes", {}).keys())
                parts.append(f"Font Sizes: {', '.join(sizes)}")
            
            elif config_name == "positions":
                pos_names = list(config_data.get("positions", {}).keys())
                aliases = list(config_data.get("aliases", {}).keys())
                parts.append(f"Positions: {', '.join(pos_names + aliases)}")
            
            elif config_name == "supported_languages":
                lang_names = [v["name"] for v in config_data.get("languages", {}).values()]
                parts.append(f"Supported Languages: {', '.join(lang_names[:10])}...")
            
            elif config_name == "supported_formats":
                video_exts = config_data.get("video_extensions", {}).get("input", [])
                parts.append(f"Supported Video Formats: {', '.join(video_exts)}")
            
            elif config_name == "silence_cutter":
                defaults = config_data.get("defaults", {})
                threshold = defaults.get("threshold_db", -35)
                min_silence = defaults.get("min_silence_duration", 0.3)
                padding = defaults.get("padding", 0.05)
                filler = defaults.get("filler_detection", True)
                parts.append(
                    f"Silence Cutter: threshold {threshold} dB, min silence {min_silence}s, padding {padding}s, filler_detection {filler}"
                )
        
        return "\n".join(parts) if parts else "No specific configuration loaded."
    
    def _get_feature_instructions(self, user_prompt: str) -> str:
        """Get additional instructions based on detected features"""
        prompt_lower = user_prompt.lower()
        instructions = []
        
        # Check for caption-related keywords
        if any(kw in prompt_lower for kw in ["caption", "subtitle", "transcribe", "text"]):
            instructions.append(self.prompt_loader.load("caption_instructions"))
        
        # Check for silence/filler removal keywords
        if any(kw in prompt_lower for kw in [
            "silence", "pause", "dead air", "filler",
            "cut from", "trim from", "remove from", "delete from", "cut between",
            "timestamps", "timecode",
            "صمت", "سكتات", "ازالة", "إزالة"
        ]):
            instructions.append(self.prompt_loader.load("silence_cutter_instructions"))
        
        # Add plan schema reference
        instructions.append(self.prompt_loader.load("plan_schema"))
        
        return "\n\n".join(filter(None, instructions))
    
    def create_plan(self, request: PlannerRequest) -> PlannerResponse:
        """
        Generate execution plan from user prompt.
        
        Args:
            request: Planner request with user prompt and context
        
        Returns:
            PlannerResponse with execution plan or errors
        """
        logger.info(f"Creating plan for prompt: {request.user_prompt[:100]}...")
        
        # Build dynamic system prompt
        system_prompt = self._build_system_prompt(request.user_prompt)
        
        # Get feature-specific instructions
        feature_instructions = self._get_feature_instructions(request.user_prompt)
        
        # Build user message
        user_message = f"""
## USER REQUEST
{request.user_prompt}

## INPUT VIDEO
{request.input_video_path or "NOT PROVIDED - Ask user to provide video path"}

## INSTRUCTIONS
{feature_instructions}

Create an execution plan for this request. Validate all options against available configuration.
Output valid JSON only.
"""
        
        try:
            # Call Ollama API
            response = ollama.chat(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                format="json"
            )
            
            # Parse response
            response_text = response['message']['content']
            logger.debug(f"LLM response: {response_text}")
            
            response_data = json.loads(response_text)
            
            # Handle the plan field - could be dict or need conversion
            if response_data.get("plan"):
                plan_data = response_data["plan"]
                if isinstance(plan_data, dict):
                    # Convert jobs to Job objects
                    if "jobs" in plan_data:
                        plan_data["jobs"] = [
                            Job(**job) if isinstance(job, dict) else job 
                            for job in plan_data["jobs"]
                        ]
                    response_data["plan"] = ExecutionPlan(**plan_data)
            
            # Convert to PlannerResponse
            planner_response = PlannerResponse(**response_data)
            
            if planner_response.success:
                job_count = len(planner_response.plan.jobs) if planner_response.plan else 0
                logger.info(f"Plan created successfully with {job_count} jobs")
            else:
                logger.warning(f"Plan creation failed: {planner_response.message}")
            
            return planner_response
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {str(e)}")
            return PlannerResponse(
                success=False,
                missing_inputs=[],
                validation_errors=[f"Invalid JSON response from LLM: {str(e)}"],
                message="Failed to parse LLM response"
            )
        except Exception as e:
            logger.error(f"Error creating plan: {str(e)}")
            return PlannerResponse(
                success=False,
                missing_inputs=[],
                validation_errors=[f"Planner error: {str(e)}"],
                message=f"Failed to create plan: {str(e)}"
            )
    
    def validate_plan(self, plan: ExecutionPlan) -> tuple[bool, List[str]]:
        """
        Validate execution plan.
        
        Returns:
            (is_valid, error_messages)
        """
        errors = []
        
        # Check for empty plan
        if not plan.jobs:
            errors.append("Plan has no jobs")
            return False, errors
        
        # Get valid tools from registry
        tools_registry = self.config_loader.tools_registry
        valid_tools = set(tools_registry.get("tools", {}).keys())
        
        # Validate each job
        job_ids = set()
        for job in plan.jobs:
            # Check for duplicate job IDs
            if job.job_id in job_ids:
                errors.append(f"Duplicate job ID: {job.job_id}")
            job_ids.add(job.job_id)
            
            # Check job type is valid
            if job.job_type not in valid_tools:
                errors.append(f"Unknown tool: {job.job_type}")
            
            # Check dependencies exist
            for dep in job.depends_on:
                if dep not in job_ids:
                    errors.append(f"Job {job.job_id} depends on non-existent job {dep}")
        
        # Check for circular dependencies
        if self._has_circular_dependency(plan.jobs):
            errors.append("Plan contains circular dependencies")
        
        return len(errors) == 0, errors
    
    def _has_circular_dependency(self, jobs: list) -> bool:
        """Check for circular dependencies using DFS"""
        job_map = {job.job_id: job for job in jobs}
        visited = set()
        rec_stack = set()
        
        def dfs(job_id: str) -> bool:
            visited.add(job_id)
            rec_stack.add(job_id)
            
            job = job_map.get(job_id)
            if job:
                for dep in job.depends_on:
                    if dep not in visited:
                        if dfs(dep):
                            return True
                    elif dep in rec_stack:
                        return True
            
            rec_stack.remove(job_id)
            return False
        
        for job in jobs:
            if job.job_id not in visited:
                if dfs(job.job_id):
                    return True
        
        return False
    
    def reload(self):
        """Reload all prompts and configs"""
        self.prompt_loader.clear_cache()
        self.config_loader.reload()
        logger.info("Planner reloaded")
