"""
Smart configuration loader with keyword-based retrieval.
Loads only relevant configs based on user prompt analysis.
"""
import json
import re
from pathlib import Path
from typing import Dict, List, Any, Set, Optional
from core.logging import setup_logger

logger = setup_logger("config_loader")


class ConfigLoader:
    """
    Intelligent configuration loader that retrieves only relevant configs
    based on user prompt keywords and detected intents.
    
    This is a lightweight alternative to RAG that works well for structured
    configuration files in a local environment.
    """
    
    def __init__(self, base_path: Optional[str] = None):
        """
        Initialize the config loader.
        
        Args:
            base_path: Base path to editbot directory. Auto-detected if None.
        """
        if base_path:
            self.base_path = Path(base_path)
        else:
            # Auto-detect base path
            self.base_path = Path(__file__).parent.parent
        
        self.configs_dir = self.base_path / "configs"
        self.registry_dir = self.base_path / "registry"
        
        # Load the config mapping
        self.config_map = self._load_config_map()
        self.tools_registry = self._load_tools_registry()
        
        # Cache loaded configs
        self._config_cache: Dict[str, Dict] = {}
        
        logger.info(f"ConfigLoader initialized with base path: {self.base_path}")
    
    def _load_config_map(self) -> Dict[str, Any]:
        """Load the keyword-to-config mapping"""
        map_file = self.registry_dir / "config_map.json"
        if map_file.exists():
            with open(map_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        logger.warning("config_map.json not found, using empty mapping")
        return {"keyword_mapping": {}, "intent_to_configs": {}, "default_configs": []}
    
    def _load_tools_registry(self) -> Dict[str, Any]:
        """Load the tools registry"""
        tools_file = self.registry_dir / "tools.json"
        if tools_file.exists():
            with open(tools_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        logger.warning("tools.json not found, using empty registry")
        return {"tools": {}, "categories": {}, "pipelines": {}}
    
    def _load_config_file(self, filename: str) -> Dict[str, Any]:
        """Load a single config file with caching"""
        if filename in self._config_cache:
            return self._config_cache[filename]
        
        config_path = self.configs_dir / filename
        if config_path.exists():
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                self._config_cache[filename] = config
                return config
        
        logger.warning(f"Config file not found: {filename}")
        return {}
    
    def analyze_prompt(self, prompt: str) -> Dict[str, Any]:
        """
        Analyze user prompt to determine relevant configs.
        
        Args:
            prompt: User's natural language prompt
        
        Returns:
            Dictionary with detected keywords, intents, and relevant config files
        """
        prompt_lower = prompt.lower()
        detected_keywords: Set[str] = set()
        relevant_configs: Set[str] = set()
        detected_intents: Set[str] = set()
        
        keyword_mapping = self.config_map.get("keyword_mapping", {})
        
        # Scan for keywords
        for config_file, mapping in keyword_mapping.items():
            keywords = mapping.get("keywords", [])
            intents = mapping.get("intents", [])
            
            for keyword in keywords:
                if keyword.lower() in prompt_lower:
                    detected_keywords.add(keyword)
                    relevant_configs.add(config_file)
        
        # Check for high-level intents
        intent_mapping = self.config_map.get("intent_to_configs", {})
        
        # Simple intent detection based on common phrases
        intent_phrases = {
            "add_captions": ["caption", "subtitle", "transcribe", "add text", "add captions"],
            "transcribe": ["transcribe", "speech to text", "convert speech"],
            "export_video": ["export", "save", "output", "render"],
            "style_captions": ["style", "font", "color", "highlight", "format"],
            "remove_silence": [
                "silence", "remove silence", "cut silence", "trim silence",
                "pause", "remove pauses", "dead air", "filler", "filler words",
                "um", "uh", "you know",
                "cut from", "trim from", "remove from", "delete from", "cut between",
                "timestamps", "timecode",
                "يعني", "اممم", "ممم", "ازالة الصمت", "إزالة الصمت"
            ]
        }
        
        for intent, phrases in intent_phrases.items():
            for phrase in phrases:
                if phrase in prompt_lower:
                    detected_intents.add(intent)
                    if intent in intent_mapping:
                        relevant_configs.update(intent_mapping[intent])
        
        # Always include default configs
        relevant_configs.update(self.config_map.get("default_configs", []))
        relevant_configs.update(self.config_map.get("always_load", []))
        
        return {
            "keywords": list(detected_keywords),
            "intents": list(detected_intents),
            "config_files": list(relevant_configs)
        }
    
    def get_relevant_configs(self, prompt: str) -> Dict[str, Any]:
        """
        Get only the configs relevant to the user's prompt.
        
        Args:
            prompt: User's natural language prompt
        
        Returns:
            Dictionary containing only relevant configuration data
        """
        analysis = self.analyze_prompt(prompt)
        config_files = analysis["config_files"]
        
        logger.info(f"Loading {len(config_files)} config files based on prompt analysis")
        logger.debug(f"Detected keywords: {analysis['keywords']}")
        logger.debug(f"Detected intents: {analysis['intents']}")
        
        configs = {}
        for filename in config_files:
            config_name = filename.replace(".json", "")
            configs[config_name] = self._load_config_file(filename)
        
        return {
            "analysis": analysis,
            "configs": configs
        }
    
    def get_all_configs(self) -> Dict[str, Any]:
        """Load all configuration files (use sparingly)"""
        configs = {}
        if self.configs_dir.exists():
            for config_file in self.configs_dir.glob("*.json"):
                config_name = config_file.stem
                configs[config_name] = self._load_config_file(config_file.name)
        return configs
    
    def get_config(self, config_name: str) -> Dict[str, Any]:
        """Get a specific config by name"""
        filename = f"{config_name}.json" if not config_name.endswith(".json") else config_name
        return self._load_config_file(filename)
    
    def get_tools_summary(self) -> str:
        """
        Get a summary of available tools for the LLM prompt.
        This provides enough info for the LLM without overloading the context.
        """
        tools = self.tools_registry.get("tools", {})
        summary_lines = ["Available tools:"]
        
        for tool_id, tool_info in tools.items():
            name = tool_info.get("name", tool_id)
            description = tool_info.get("description", "No description")
            category = tool_info.get("category", "general")
            summary_lines.append(f"- {tool_id}: {description} (category: {category})")
        
        return "\n".join(summary_lines)
    
    def get_tool_details(self, tool_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a specific tool"""
        return self.tools_registry.get("tools", {}).get(tool_id)
    
    def get_pipeline(self, pipeline_name: str) -> Optional[Dict[str, Any]]:
        """Get a predefined pipeline"""
        return self.tools_registry.get("pipelines", {}).get(pipeline_name)
    
    def get_tools_for_intent(self, intent: str) -> List[str]:
        """Get list of tools relevant to an intent"""
        pipeline = self.get_pipeline(f"{intent}_pipeline")
        if pipeline:
            return pipeline.get("steps", [])
        return []
    
    def format_configs_for_prompt(self, configs: Dict[str, Any], max_length: int = 2000) -> str:
        """
        Format configs into a string suitable for LLM prompt.
        Truncates if too long.
        
        Args:
            configs: Dictionary of config data
            max_length: Maximum character length
        
        Returns:
            Formatted string for prompt
        """
        formatted_parts = []
        
        for config_name, config_data in configs.items():
            if isinstance(config_data, dict):
                # Extract just the essential keys (skip description, version, etc.)
                essential_keys = [k for k in config_data.keys() 
                                 if k not in ["description", "version"]]
                
                essential_data = {k: config_data[k] for k in essential_keys[:5]}  # Limit keys
                formatted_parts.append(f"[{config_name}]")
                formatted_parts.append(json.dumps(essential_data, indent=2)[:500])  # Limit each config
        
        result = "\n".join(formatted_parts)
        
        if len(result) > max_length:
            result = result[:max_length] + "\n... (truncated)"
        
        return result
    
    def validate_user_request(self, prompt: str) -> Dict[str, Any]:
        """
        Validate user request against available configs.
        
        Returns dict with:
        - valid: bool
        - warnings: list of potential issues
        - suggestions: list of alternatives if something isn't supported
        """
        relevant = self.get_relevant_configs(prompt)
        configs = relevant["configs"]
        prompt_lower = prompt.lower()
        
        warnings = []
        suggestions = []
        
        # Check fonts
        if "fonts" in configs:
            font_names = [f["name"].lower() for f in configs["fonts"].get("fonts", [])]
            # Simple font check (can be enhanced)
            for word in prompt_lower.split():
                if "font" in prompt_lower and word not in font_names and len(word) > 3:
                    # Might be requesting unknown font
                    pass
        
        # Check colors
        if "colors" in configs:
            available_colors = list(configs["colors"].get("colors", {}).keys())
            # Color validation could go here
        
        return {
            "valid": len(warnings) == 0,
            "warnings": warnings,
            "suggestions": suggestions,
            "loaded_configs": list(configs.keys())
        }
    
    def clear_cache(self):
        """Clear the config cache"""
        self._config_cache.clear()
        logger.info("Config cache cleared")
    
    def reload(self):
        """Reload all registry files and clear cache"""
        self.clear_cache()
        self.config_map = self._load_config_map()
        self.tools_registry = self._load_tools_registry()
        logger.info("ConfigLoader reloaded")


# Singleton instance for easy access
_config_loader: Optional[ConfigLoader] = None


def get_config_loader(base_path: Optional[str] = None) -> ConfigLoader:
    """Get or create the singleton ConfigLoader instance"""
    global _config_loader
    if _config_loader is None:
        _config_loader = ConfigLoader(base_path)
    return _config_loader
