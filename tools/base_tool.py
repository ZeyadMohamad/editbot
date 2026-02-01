"""
Base tool class for all EditBot tools.
Provides consistent interface and auto-registration capabilities.
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List, Type
from pathlib import Path
from core.logging import setup_logger

logger = setup_logger("base_tool")


class ToolResult:
    """Standardized result from tool execution"""
    
    def __init__(
        self, 
        success: bool, 
        data: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        artifacts: Optional[Dict[str, str]] = None
    ):
        self.success = success
        self.data = data or {}
        self.error = error
        self.artifacts = artifacts or {}  # name -> file path
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "artifacts": self.artifacts
        }
    
    @classmethod
    def ok(cls, data: Dict[str, Any] = None, artifacts: Dict[str, str] = None) -> "ToolResult":
        """Create a successful result"""
        return cls(success=True, data=data, artifacts=artifacts)
    
    @classmethod
    def fail(cls, error: str) -> "ToolResult":
        """Create a failed result"""
        return cls(success=False, error=error)


class BaseTool(ABC):
    """
    Abstract base class for all tools in EditBot.
    
    Provides:
    - Consistent interface for tool execution
    - Auto-registration with tool registry
    - Logging and error handling
    - Input validation framework
    """
    
    # Tool metadata - override in subclasses
    tool_id: str = "base_tool"
    tool_name: str = "Base Tool"
    description: str = "Base tool class"
    category: str = "general"
    version: str = "1.0.0"
    
    def __init__(self):
        self.logger = setup_logger(self.tool_id)
        self.logger.info(f"{self.tool_name} initialized")
    
    @abstractmethod
    def execute(self, **kwargs) -> ToolResult:
        """
        Execute the tool with given parameters.
        Must be implemented by subclasses.
        
        Returns:
            ToolResult with success status and data/error
        """
        pass
    
    def validate_inputs(self, inputs: Dict[str, Any], required: List[str]) -> Optional[str]:
        """
        Validate that all required inputs are present.
        
        Args:
            inputs: Input parameters
            required: List of required parameter names
        
        Returns:
            Error message if validation fails, None if valid
        """
        missing = [r for r in required if r not in inputs or inputs[r] is None]
        if missing:
            return f"Missing required inputs: {', '.join(missing)}"
        return None
    
    def validate_file_exists(self, file_path: str) -> Optional[str]:
        """Check if a file exists"""
        if not Path(file_path).exists():
            return f"File not found: {file_path}"
        return None
    
    def validate_file_extension(self, file_path: str, valid_extensions: List[str]) -> Optional[str]:
        """Check if file has a valid extension"""
        ext = Path(file_path).suffix.lower()
        if ext not in [e.lower() for e in valid_extensions]:
            return f"Invalid file extension: {ext}. Valid: {', '.join(valid_extensions)}"
        return None
    
    def ensure_output_dir(self, output_path: str) -> None:
        """Ensure the output directory exists"""
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    
    def get_info(self) -> Dict[str, Any]:
        """Get tool information"""
        return {
            "tool_id": self.tool_id,
            "name": self.tool_name,
            "description": self.description,
            "category": self.category,
            "version": self.version
        }


class ToolRegistry:
    """
    Registry for dynamically discovering and managing tools.
    Tools register themselves when imported.
    """
    
    _instance: Optional["ToolRegistry"] = None
    _tools: Dict[str, Type[BaseTool]] = {}
    _instances: Dict[str, BaseTool] = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    @classmethod
    def register(cls, tool_class: Type[BaseTool]) -> Type[BaseTool]:
        """
        Register a tool class. Can be used as a decorator.
        
        Example:
            @ToolRegistry.register
            class MyTool(BaseTool):
                ...
        """
        tool_id = tool_class.tool_id
        cls._tools[tool_id] = tool_class
        logger.info(f"Registered tool: {tool_id}")
        return tool_class
    
    @classmethod
    def get_tool_class(cls, tool_id: str) -> Optional[Type[BaseTool]]:
        """Get a tool class by ID"""
        return cls._tools.get(tool_id)
    
    @classmethod
    def get_tool_instance(cls, tool_id: str) -> Optional[BaseTool]:
        """Get or create a tool instance by ID (singleton per tool)"""
        if tool_id not in cls._instances:
            tool_class = cls._tools.get(tool_id)
            if tool_class:
                cls._instances[tool_id] = tool_class()
        return cls._instances.get(tool_id)
    
    @classmethod
    def get_all_tools(cls) -> Dict[str, Type[BaseTool]]:
        """Get all registered tool classes"""
        return cls._tools.copy()
    
    @classmethod
    def get_tools_by_category(cls, category: str) -> Dict[str, Type[BaseTool]]:
        """Get all tools in a category"""
        return {
            tid: tcls for tid, tcls in cls._tools.items() 
            if tcls.category == category
        }
    
    @classmethod
    def list_tools(cls) -> List[Dict[str, Any]]:
        """Get info about all registered tools"""
        return [
            {
                "tool_id": tool_class.tool_id,
                "name": tool_class.tool_name,
                "description": tool_class.description,
                "category": tool_class.category
            }
            for tool_class in cls._tools.values()
        ]
    
    @classmethod
    def clear(cls):
        """Clear all registered tools (useful for testing)"""
        cls._tools.clear()
        cls._instances.clear()


def register_tool(tool_class: Type[BaseTool]) -> Type[BaseTool]:
    """Decorator to register a tool"""
    return ToolRegistry.register(tool_class)
