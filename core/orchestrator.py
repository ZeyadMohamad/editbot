"""
Orchestrator - Executes plans by coordinating tools and managing job state.

This is the central execution engine that:
- Takes execution plans from the planner
- Resolves job dependencies
- Executes jobs in correct order
- Manages artifacts between jobs
- Handles errors and retries
- Provides progress tracking
"""
import json
import re
import time
import uuid
from pathlib import Path
from typing import Dict, Any, Optional, List, Callable
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field

from core.schema import ExecutionPlan, Job
from core.state import WorkspaceManager, JobState
from core.config_loader import ConfigLoader, get_config_loader
from core.logging import setup_logger
from tools.base_tool import ToolRegistry, ToolResult, BaseTool

logger = setup_logger("orchestrator")


class JobStatus(Enum):
    """Status of a job in the execution pipeline"""
    PENDING = "pending"
    WAITING = "waiting"      # Waiting for dependencies
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class JobExecution:
    """Tracks the execution state of a single job"""
    job: Job
    status: JobStatus = JobStatus.PENDING
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Optional[ToolResult] = None
    error: Optional[str] = None
    retry_count: int = 0
    resolved_inputs: Dict[str, Any] = field(default_factory=dict)
    resolved_outputs: Dict[str, str] = field(default_factory=dict)


@dataclass
class PlanExecution:
    """Tracks the execution state of an entire plan"""
    plan: ExecutionPlan
    execution_id: str
    status: JobStatus = JobStatus.PENDING
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    jobs: Dict[str, JobExecution] = field(default_factory=dict)
    workspace_path: Optional[str] = None
    input_video_path: Optional[str] = None
    output_dir: Optional[str] = None


class Orchestrator:
    """
    Executes plans by coordinating tools and managing state.
    
    Features:
    - Dependency resolution and topological sorting
    - Parallel execution support (future)
    - Error handling and retry logic
    - Progress callbacks for UI
    - Workspace and artifact management
    - Extensible tool loading
    """
    
    def __init__(
        self,
        workspace_root: str = "./workspace",
        output_root: str = "./output",
        max_retries: int = 2,
        base_path: Optional[str] = None
    ):
        """
        Initialize the orchestrator.
        
        Args:
            workspace_root: Root directory for job workspaces
            output_root: Root directory for final outputs
            max_retries: Maximum retry attempts for failed jobs
            base_path: Base path to editbot directory
        """
        self.workspace_manager = WorkspaceManager(workspace_root)
        self.output_root = Path(output_root)
        self.output_root.mkdir(parents=True, exist_ok=True)
        
        self.max_retries = max_retries
        self.config_loader = get_config_loader(base_path)
        
        # Progress callback
        self._progress_callback: Optional[Callable[[str, float, str], None]] = None
        
        # Tool instances cache
        self._tools: Dict[str, BaseTool] = {}
        
        # Import and register tools
        self._load_tools()
        
        logger.info(f"Orchestrator initialized. Workspace: {workspace_root}")
    
    def _load_tools(self):
        """Load and register all available tools"""
        # Import tool modules to trigger registration
        try:
            from tools import ffmpeg_tool
            from tools import rotate_tool
            from tools import whisperx_tool
            from tools import captions_tool
            from tools import silence_cutter_tool
            from tools import stock_footage_tool
            from tools import text_overlay_tool
            from tools import background_audio_tool
            from tools import image_overlay_tool
            from tools import image_to_video_tool
            logger.info("Tools loaded successfully")
        except ImportError as e:
            logger.warning(f"Some tools failed to load: {e}")
    
    def _get_tool(self, tool_id: str) -> Optional[BaseTool]:
        """Get a tool instance by ID"""
        # First check registry
        tool = ToolRegistry.get_tool_instance(tool_id)
        if tool:
            return tool
        
        # Fallback: load from tools registry JSON and instantiate
        tool_info = self.config_loader.get_tool_details(tool_id)
        if not tool_info:
            logger.error(f"Unknown tool: {tool_id}")
            return None
        
        # Dynamic import based on registry
        module_path = tool_info.get("module", "")
        class_name = tool_info.get("class", "")
        
        try:
            import importlib
            module = importlib.import_module(module_path)
            tool_class = getattr(module, class_name)
            tool_instance = tool_class()
            self._tools[tool_id] = tool_instance
            return tool_instance
        except Exception as e:
            logger.error(f"Failed to load tool {tool_id}: {e}")
            return None
    
    def set_progress_callback(self, callback: Callable[[str, float, str], None]):
        """
        Set a callback for progress updates.
        
        Callback signature: (job_id, progress_percent, message)
        """
        self._progress_callback = callback
    
    def _report_progress(self, job_id: str, progress: float, message: str):
        """Report progress via callback if set"""
        if self._progress_callback:
            self._progress_callback(job_id, progress, message)
        logger.info(f"[{job_id}] {progress:.0f}% - {message}")
    
    def execute(
        self,
        plan: ExecutionPlan,
        input_video_path: str,
        output_dir: Optional[str] = None
    ) -> PlanExecution:
        """
        Execute a plan.
        
        Args:
            plan: The execution plan from planner
            input_video_path: Path to input video file
            output_dir: Output directory (uses default if None)
        
        Returns:
            PlanExecution with status and results
        """
        execution_id = f"exec_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        
        # Create workspace for this execution
        workspace_path = self.workspace_manager.workspace_root / execution_id
        workspace_path.mkdir(parents=True, exist_ok=True)
        
        # Set output directory
        if output_dir:
            final_output_dir = Path(output_dir)
        else:
            final_output_dir = self.output_root / execution_id
        final_output_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize plan execution
        plan_exec = PlanExecution(
            plan=plan,
            execution_id=execution_id,
            status=JobStatus.RUNNING,
            started_at=datetime.now(),
            workspace_path=str(workspace_path),
            input_video_path=input_video_path,
            output_dir=str(final_output_dir)
        )
        
        # Initialize job executions
        for job in plan.jobs:
            plan_exec.jobs[job.job_id] = JobExecution(job=job)
        
        logger.info(f"Starting execution: {execution_id}")
        logger.info(f"Input video: {input_video_path}")
        logger.info(f"Workspace: {workspace_path}")
        logger.info(f"Output: {final_output_dir}")
        
        # Get execution order (topological sort)
        execution_order = self._get_execution_order(plan.jobs)
        
        total_jobs = len(execution_order)
        completed_jobs = 0
        
        # Execute jobs in order
        for job_id in execution_order:
            job_exec = plan_exec.jobs[job_id]
            
            # Check if dependencies are satisfied
            deps_satisfied = self._check_dependencies(job_id, plan_exec)
            if not deps_satisfied:
                job_exec.status = JobStatus.FAILED
                job_exec.error = "Dependencies not satisfied"
                self._report_progress(job_id, 0, "Skipped - dependencies failed")
                continue
            
            # Execute the job
            self._execute_job(job_exec, plan_exec)
            
            completed_jobs += 1
            overall_progress = (completed_jobs / total_jobs) * 100
            self._report_progress("overall", overall_progress, f"Completed {completed_jobs}/{total_jobs} jobs")
            
            # Stop if job failed
            if job_exec.status == JobStatus.FAILED:
                logger.error(f"Job {job_id} failed. Stopping execution.")
                plan_exec.status = JobStatus.FAILED
                break
        
        # Finalize
        plan_exec.completed_at = datetime.now()
        if plan_exec.status != JobStatus.FAILED:
            plan_exec.status = JobStatus.COMPLETED
        
        # Save execution summary
        self._save_execution_summary(plan_exec)
        
        duration = (plan_exec.completed_at - plan_exec.started_at).total_seconds()
        logger.info(f"Execution {execution_id} finished in {duration:.1f}s with status: {plan_exec.status.value}")
        
        return plan_exec
    
    def _get_execution_order(self, jobs: List[Job]) -> List[str]:
        """
        Get jobs in execution order using topological sort.
        Jobs with no dependencies come first.
        """
        # Build dependency graph
        in_degree = {job.job_id: len(job.depends_on) for job in jobs}
        dependents = {job.job_id: [] for job in jobs}
        
        for job in jobs:
            for dep in job.depends_on:
                if dep in dependents:
                    dependents[dep].append(job.job_id)
        
        # Find jobs with no dependencies
        queue = [jid for jid, deg in in_degree.items() if deg == 0]
        order = []
        
        while queue:
            job_id = queue.pop(0)
            order.append(job_id)
            
            for dependent in dependents[job_id]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)
        
        # Check for cycles
        if len(order) != len(jobs):
            logger.error("Circular dependency detected in plan")
            # Return original order as fallback
            return [job.job_id for job in jobs]
        
        return order
    
    def _check_dependencies(self, job_id: str, plan_exec: PlanExecution) -> bool:
        """Check if all dependencies of a job are completed"""
        job_exec = plan_exec.jobs[job_id]
        for dep_id in job_exec.job.depends_on:
            dep_exec = plan_exec.jobs.get(dep_id)
            if not dep_exec or dep_exec.status != JobStatus.COMPLETED:
                return False
        return True
    
    def _resolve_placeholder(self, value: str, plan_exec: PlanExecution) -> str:
        """
        Resolve placeholders in input/output values.
        
        Placeholders:
        - {input_video}: Input video path
        - {workspace}: Job workspace directory
        - {output_dir}: Final output directory
        - {job_id}: Current job ID
        - {job_X.outputs.Y}: Output from another job
        """
        if not isinstance(value, str):
            return value
        
        # Simple placeholders
        value = value.replace("{input_video}", plan_exec.input_video_path or "")
        value = value.replace("{workspace}", plan_exec.workspace_path or "")
        value = value.replace("{output_dir}", plan_exec.output_dir or "")
        
        # Job output references: {job_1.outputs.audio_file}
        pattern = r'\{(job_\d+)\.outputs\.(\w+)\}'
        matches = re.findall(pattern, value)
        
        for job_ref, output_name in matches:
            ref_job_exec = plan_exec.jobs.get(job_ref)
            if ref_job_exec and output_name in ref_job_exec.resolved_outputs:
                placeholder = f"{{{job_ref}.outputs.{output_name}}}"
                value = value.replace(placeholder, ref_job_exec.resolved_outputs[output_name])
        
        return value
    
    def _resolve_inputs(self, job_exec: JobExecution, plan_exec: PlanExecution) -> Dict[str, Any]:
        """Resolve all input placeholders for a job"""
        resolved = {}
        for key, value in job_exec.job.inputs.items():
            if isinstance(value, str):
                resolved[key] = self._resolve_placeholder(value, plan_exec)
            elif isinstance(value, dict):
                # Recursively resolve nested dicts (like style config)
                resolved[key] = {
                    k: self._resolve_placeholder(v, plan_exec) if isinstance(v, str) else v
                    for k, v in value.items()
                }
            else:
                resolved[key] = value
        return resolved
    
    def _execute_job(self, job_exec: JobExecution, plan_exec: PlanExecution):
        """Execute a single job"""
        job = job_exec.job
        job_exec.status = JobStatus.RUNNING
        job_exec.started_at = datetime.now()
        
        self._report_progress(job.job_id, 0, f"Starting {job.job_type}")
        
        # Get the tool
        tool = self._get_tool(job.job_type)
        if not tool:
            job_exec.status = JobStatus.FAILED
            job_exec.error = f"Tool not found: {job.job_type}"
            return
        
        # Resolve inputs
        job_exec.resolved_inputs = self._resolve_inputs(job_exec, plan_exec)
        
        # Execute with retry
        for attempt in range(self.max_retries + 1):
            try:
                self._report_progress(job.job_id, 25, f"Executing (attempt {attempt + 1})")
                
                # Call the tool
                result = self._call_tool(tool, job.job_type, job_exec.resolved_inputs)
                
                if result.success:
                    job_exec.result = result
                    job_exec.status = JobStatus.COMPLETED
                    
                    # Store resolved outputs
                    for output_name, output_template in job.outputs.items():
                        if output_name in result.artifacts:
                            job_exec.resolved_outputs[output_name] = result.artifacts[output_name]
                        else:
                            job_exec.resolved_outputs[output_name] = self._resolve_placeholder(
                                output_template, plan_exec
                            )
                    
                    self._report_progress(job.job_id, 100, "Completed")
                    break
                else:
                    job_exec.error = result.error
                    job_exec.retry_count = attempt + 1
                    
                    if attempt < self.max_retries:
                        self._report_progress(job.job_id, 50, f"Failed, retrying... ({result.error})")
                        time.sleep(1)  # Brief delay before retry
                    else:
                        job_exec.status = JobStatus.FAILED
                        self._report_progress(job.job_id, 0, f"Failed: {result.error}")
                        
            except Exception as e:
                job_exec.error = str(e)
                job_exec.retry_count = attempt + 1
                
                if attempt < self.max_retries:
                    self._report_progress(job.job_id, 50, f"Exception, retrying... ({e})")
                    time.sleep(1)
                else:
                    job_exec.status = JobStatus.FAILED
                    self._report_progress(job.job_id, 0, f"Exception: {e}")
                    logger.exception(f"Job {job.job_id} failed with exception")
        
        job_exec.completed_at = datetime.now()
    
    def _call_tool(self, tool: BaseTool, tool_type: str, inputs: Dict[str, Any]) -> ToolResult:
        """
        Call a tool with the given inputs.
        Maps tool_type to the appropriate method.
        """
        # Get method from tool based on type
        method_map = {
            "extract_audio": "extract_audio",
            "get_video_info": "get_video_info",
            "transcribe": "transcribe",
            "align_words": "align_words",
            "transcribe_and_align": "transcribe_and_align",
            "generate_captions": "generate_ass_file",
            "render_subtitles": "render_subtitles",
            "silence_cutter": "cut_silence",
            "stock_footage": "apply_stock_footage",
            "apply_transitions": "apply_transitions",
            "rotate_media": "rotate_media",
            "text_overlay": "add_text",
            "background_audio": "add_background_audio",
            "image_overlay": "add_images",
            "image_to_video": "convert",
        }
        
        method_name = method_map.get(tool_type)
        if not method_name:
            return ToolResult.fail(f"Unknown method for tool type: {tool_type}")
        
        method = getattr(tool, method_name, None)
        if not method:
            return ToolResult.fail(f"Method {method_name} not found on tool")
        
        # Call the method
        result = method(**inputs)
        
        # Convert dict result to ToolResult if needed
        if isinstance(result, dict):
            if result.get("success", False):
                return ToolResult.ok(
                    data=result,
                    artifacts={k: v for k, v in result.items() if k.endswith("_path") or k.endswith("_file")}
                )
            else:
                return ToolResult.fail(result.get("error", "Unknown error"))
        elif isinstance(result, ToolResult):
            return result
        else:
            return ToolResult.ok(data={"result": result})
    
    def _save_execution_summary(self, plan_exec: PlanExecution):
        """Save execution summary to workspace"""
        summary = {
            "execution_id": plan_exec.execution_id,
            "plan_id": plan_exec.plan.plan_id,
            "status": plan_exec.status.value,
            "started_at": plan_exec.started_at.isoformat() if plan_exec.started_at else None,
            "completed_at": plan_exec.completed_at.isoformat() if plan_exec.completed_at else None,
            "input_video": plan_exec.input_video_path,
            "output_dir": plan_exec.output_dir,
            "jobs": {
                jid: {
                    "status": jexec.status.value,
                    "error": jexec.error,
                    "retry_count": jexec.retry_count,
                    "outputs": jexec.resolved_outputs
                }
                for jid, jexec in plan_exec.jobs.items()
            }
        }
        
        summary_path = Path(plan_exec.workspace_path) / "execution_summary.json"
        with open(summary_path, 'w') as f:
            json.dump(summary, f, indent=2)
        
        logger.info(f"Execution summary saved to {summary_path}")
    
    def get_final_output(self, plan_exec: PlanExecution) -> Optional[str]:
        """Get the path to the final output file"""
        # Look for the last job's output
        if plan_exec.status != JobStatus.COMPLETED:
            return None
        
        # Find render_subtitles job output
        for job_exec in plan_exec.jobs.values():
            if job_exec.job.job_type == "render_subtitles":
                return job_exec.resolved_outputs.get("video_file")
        
        # Fallback: silence cutter output
        for job_exec in plan_exec.jobs.values():
            if job_exec.job.job_type == "silence_cutter":
                return job_exec.resolved_outputs.get("output_video_path")

        # Fallback: transitions output
        for job_exec in plan_exec.jobs.values():
            if job_exec.job.job_type == "apply_transitions":
                return job_exec.resolved_outputs.get("video_file") or job_exec.resolved_outputs.get("output_path")

        # Fallback: rotate media output
        for job_exec in plan_exec.jobs.values():
            if job_exec.job.job_type == "rotate_media":
                return job_exec.resolved_outputs.get("media_file") or job_exec.resolved_outputs.get("output_path")

        # Fallback: text overlay output
        for job_exec in plan_exec.jobs.values():
            if job_exec.job.job_type == "text_overlay":
                return job_exec.resolved_outputs.get("video_file") or job_exec.resolved_outputs.get("output_path")

        # Fallback: background audio output
        for job_exec in plan_exec.jobs.values():
            if job_exec.job.job_type == "background_audio":
                return job_exec.resolved_outputs.get("video_file") or job_exec.resolved_outputs.get("output_path")

        # Fallback: image overlay output
        for job_exec in plan_exec.jobs.values():
            if job_exec.job.job_type == "image_overlay":
                return job_exec.resolved_outputs.get("video_file") or job_exec.resolved_outputs.get("output_path")

        # Fallback: image-to-video output
        for job_exec in plan_exec.jobs.values():
            if job_exec.job.job_type == "image_to_video":
                return job_exec.resolved_outputs.get("video_file") or job_exec.resolved_outputs.get("output_path")

        return None
