"""
Job workspace, artifacts, and caching management
"""
import os
import json
import hashlib
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime


class JobState:
    """Manages state for a single job execution"""
    
    def __init__(self, job_id: str, workspace_dir: str):
        self.job_id = job_id
        self.workspace_dir = Path(workspace_dir)
        self.artifacts_dir = self.workspace_dir / "artifacts" / job_id
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        
        self.state: Dict[str, Any] = {
            "job_id": job_id,
            "status": "pending",
            "started_at": None,
            "completed_at": None,
            "artifacts": {},
            "metadata": {}
        }
    
    def set_status(self, status: str):
        """Update job status"""
        self.state["status"] = status
        if status == "running" and not self.state["started_at"]:
            self.state["started_at"] = datetime.now().isoformat()
        elif status in ["completed", "failed"]:
            self.state["completed_at"] = datetime.now().isoformat()
    
    def add_artifact(self, artifact_name: str, artifact_path: str, metadata: Optional[Dict] = None):
        """Register an artifact produced by the job"""
        self.state["artifacts"][artifact_name] = {
            "path": artifact_path,
            "created_at": datetime.now().isoformat(),
            "metadata": metadata or {}
        }
    
    def get_artifact(self, artifact_name: str) -> Optional[str]:
        """Retrieve artifact path by name"""
        artifact = self.state["artifacts"].get(artifact_name)
        return artifact["path"] if artifact else None
    
    def save(self):
        """Persist state to disk"""
        state_file = self.artifacts_dir / "state.json"
        with open(state_file, 'w') as f:
            json.dump(self.state, f, indent=2)
    
    def load(self):
        """Load state from disk"""
        state_file = self.artifacts_dir / "state.json"
        if state_file.exists():
            with open(state_file, 'r') as f:
                self.state = json.load(f)


class WorkspaceManager:
    """Manages workspace for entire execution plan"""
    
    def __init__(self, workspace_root: str = "./workspace"):
        self.workspace_root = Path(workspace_root)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.cache_dir = self.workspace_root / "cache"
        self.cache_dir.mkdir(exist_ok=True)
        
    def create_job_state(self, job_id: str) -> JobState:
        """Create a new job state manager"""
        return JobState(job_id, str(self.workspace_root))
    
    def get_cache_key(self, data: str) -> str:
        """Generate cache key from data"""
        return hashlib.sha256(data.encode()).hexdigest()
    
    def get_cached(self, cache_key: str) -> Optional[Any]:
        """Retrieve cached result"""
        cache_file = self.cache_dir / f"{cache_key}.json"
        if cache_file.exists():
            with open(cache_file, 'r') as f:
                return json.load(f)
        return None
    
    def set_cached(self, cache_key: str, data: Any):
        """Store result in cache"""
        cache_file = self.cache_dir / f"{cache_key}.json"
        with open(cache_file, 'w') as f:
            json.dump(data, f, indent=2)
    
    def cleanup(self, keep_artifacts: bool = True):
        """Clean up workspace"""
        if not keep_artifacts:
            import shutil
            if self.workspace_root.exists():
                shutil.rmtree(self.workspace_root)
