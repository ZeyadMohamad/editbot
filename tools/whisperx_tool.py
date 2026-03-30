"""
WhisperX tool for transcription and word-level alignment.
Uses faster-whisper with large-v3 for high-quality multilingual transcription.
"""
from faster_whisper import WhisperModel
from typing import Dict, Any, Optional, List
from pathlib import Path
from core.logging import setup_logger
from tools.base_tool import BaseTool, ToolResult, register_tool
import os

logger = setup_logger("whisperx_tool")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = Path(os.getenv("EDITBOT_MODELS_DIR", str(PROJECT_ROOT / ".models")))

# Supported audio formats for transcription
SUPPORTED_AUDIO_EXTENSIONS = [".mp3", ".wav", ".aac", ".flac", ".ogg", ".m4a", ".wma"]

# Model size recommendations:
# - tiny: ~1GB VRAM, fastest, lowest quality
# - base: ~1GB VRAM, fast, basic quality
# - small: ~2GB VRAM, good balance
# - medium: ~5GB VRAM, high quality
# - large-v3: ~10GB VRAM, best quality (uses CPU if not enough VRAM)
# For RTX 3050 4GB: use "small" for good quality, "medium" with int8


@register_tool
class WhisperXTool(BaseTool):
    """Handles transcription and word-level alignment using faster-whisper"""
    
    # Tool metadata
    tool_id = "whisperx"
    tool_name = "WhisperX Tool"
    description = "Speech-to-text transcription with word-level timestamps"
    category = "transcription"
    version = "2.0.0"
    
    def __init__(self, model_size: str = "small", device: str = "cuda", compute_type: str = "float16"):
        """
        Initialize faster-whisper model.
        
        Args:
            model_size: Model size (tiny, base, small, medium, large-v3)
                       For RTX 3050 4GB: use 'small' or 'medium' with int8
            device: Device to use (cuda, cpu, auto)
            compute_type: Quantization (float16, int8_float16, int8) - int8 uses less VRAM
        """
        super().__init__()
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.model = None
        self.logger.info(f"WhisperX Tool initialized (model: {model_size}, device: {device})")
    
    def execute(self, operation: str, **kwargs) -> ToolResult:
        """Generic execute method - routes to specific operations"""
        operations = {
            "transcribe": self.transcribe,
            "align_words": self.align_words,
            "transcribe_and_align": self.transcribe_and_align
        }
        
        if operation not in operations:
            return ToolResult.fail(f"Unknown operation: {operation}")
        
        result = operations[operation](**kwargs)
        
        if isinstance(result, dict):
            if result.get("success"):
                return ToolResult.ok(data=result)
            else:
                return ToolResult.fail(result.get("error", "Unknown error"))
        return result
    
    def load_model(self):
        """Lazy load faster-whisper model"""
        if self.model is None:
            self.logger.info(f"Loading faster-whisper model: {self.model_size}")
            
            # Check CUDA availability
            import torch
            cuda_available = torch.cuda.is_available()
            if cuda_available:
                gpu_name = torch.cuda.get_device_name(0)
                gpu_mem = torch.cuda.get_device_properties(0).total_memory / (1024**3)
                self.logger.info(f"CUDA available: {gpu_name} ({gpu_mem:.1f}GB)")
            else:
                self.logger.warning("CUDA not available, will use CPU")
                self.device = "cpu"
            
            try:
                # Load model on specified device
                self.model = WhisperModel(
                    self.model_size, 
                    device=self.device,
                    compute_type=self.compute_type,
                    download_root=str(MODEL_DIR),
                    num_workers=4  # Parallel processing
                )
                
                # Verify GPU is being used
                if self.device == "cuda" and cuda_available:
                    self.logger.info(f"✓ Model loaded on GPU with {self.compute_type}")
                else:
                    self.logger.info(f"Model loaded on {self.device}")
                    
            except Exception as e:
                self.logger.warning(f"Failed to load on {self.device}: {e}")
                self.logger.info("Falling back to CPU with int8...")
                try:
                    self.model = WhisperModel(
                        self.model_size,
                        device="cpu",
                        compute_type="int8",
                        download_root=str(MODEL_DIR)
                    )
                    self.device = "cpu"
                    self.logger.info("Model loaded on CPU")
                except Exception as e2:
                    self.logger.error(f"Failed to load model: {e2}")
                    raise
    
    def _validate_audio_path(self, audio_path: str) -> Optional[str]:
        """Validate audio file exists and has supported format"""
        path = Path(audio_path)
        if not path.exists():
            return f"Audio file not found: {audio_path}"
        if path.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
            return f"Unsupported audio format: {path.suffix}. Supported: {SUPPORTED_AUDIO_EXTENSIONS}"
        return None
    
    def transcribe(
        self, 
        audio_path: str, 
        language: Optional[str] = None,
        task: str = "transcribe"
    ) -> Dict[str, Any]:
        """
        Transcribe audio file using faster-whisper.
        
        Args:
            audio_path: Path to audio file
            language: Language code (e.g., 'en', 'ar') or None for auto-detect
            task: 'transcribe' or 'translate' (translate to English)
        
        Returns:
            Dictionary with transcription results including word timestamps
        """
        # Validate input
        validation_error = self._validate_audio_path(audio_path)
        if validation_error:
            self.logger.error(validation_error)
            return {"success": False, "error": validation_error}
        
        self.logger.info(f"Transcribing audio: {audio_path}")
        self.load_model()
        
        try:
            # Transcribe using faster-whisper
            # word_timestamps=True gives word-level timing
            with open("prompts/whisper_initial_prompt.txt", "r", encoding="utf-8") as f:
                initial_prompt = f.read().strip()
            segments, info = self.model.transcribe(
                audio_path,
                language=language,
                task=task,
                word_timestamps=True,
                beam_size=5,  # Higher = better quality, slower
                best_of=5,    # Number of candidates
                vad_filter=True,  # Filter out silence
                vad_parameters=dict(
                    min_silence_duration_ms=500,  # Minimum silence to split
                ),
                initial_prompt = initial_prompt
            )
            
            # Convert generator to list and extract data
            segments_list = []
            all_words = []
            full_text = ""
            
            for segment in segments:
                segment_dict = {
                    "start": segment.start,
                    "end": segment.end,
                    "text": segment.text,
                    "words": []
                }
                
                full_text += segment.text
                
                # Extract word-level timestamps
                if segment.words:
                    for word in segment.words:
                        word_dict = {
                            "word": word.word.strip(),
                            "start": word.start,
                            "end": word.end,
                            "probability": word.probability
                        }
                        segment_dict["words"].append(word_dict)
                        all_words.append({
                            "word": word.word.strip(),
                            "start": word.start,
                            "end": word.end,
                            "confidence": word.probability
                        })
                
                segments_list.append(segment_dict)
            
            detected_language = info.language
            language_probability = info.language_probability
            
            self.logger.info(f"Transcription complete. Language: {detected_language} ({language_probability:.2%})")
            self.logger.info(f"Detected {len(segments_list)} segments, {len(all_words)} words")
            
            return {
                "success": True,
                "text": full_text.strip(),
                "language": detected_language,
                "language_probability": language_probability,
                "segments": segments_list,
                "words": all_words,
                "audio_file": audio_path
            }
            
        except Exception as e:
            self.logger.error(f"Error during transcription: {str(e)}")
            import traceback
            self.logger.error(traceback.format_exc())
            return {
                "success": False,
                "error": str(e)
            }
    
    def align_words(self, transcription_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract word-level alignments from transcription result.
        
        Args:
            transcription_result: Result from transcribe()
        
        Returns:
            Dictionary with word-level timestamps
        """
        self.logger.info("Extracting word-level alignments")
        
        try:
            if not transcription_result.get("success"):
                return transcription_result
            
            # Words are already extracted in transcribe()
            words = transcription_result.get("words", [])
            
            if not words and transcription_result.get("segments"):
                # Fallback: extract from segments
                all_words = []
                for segment in transcription_result["segments"]:
                    if "words" in segment:
                        for word_info in segment["words"]:
                            all_words.append({
                                "word": word_info.get("word", "").strip(),
                                "start": word_info.get("start", 0),
                                "end": word_info.get("end", 0),
                                "confidence": word_info.get("probability", word_info.get("confidence", 1.0))
                            })
                words = all_words
            
            self.logger.info(f"Extracted {len(words)} words with timestamps")
            
            return {
                "success": True,
                "words": words,
                "total_words": len(words)
            }
            
        except Exception as e:
            self.logger.error(f"Error aligning words: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def transcribe_and_align(
        self, 
        audio_path: str, 
        language: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Complete pipeline: transcribe + word alignment.
        
        Args:
            audio_path: Path to audio file
            language: Language code or None for auto-detect
        
        Returns:
            Dictionary with words and timestamps
        """
        self.logger.info(f"Running full transcription pipeline on: {audio_path}")
        
        # Transcribe (words are already included)
        transcription = self.transcribe(audio_path, language)
        
        if not transcription.get("success"):
            return transcription
        
        # Return with all data
        return {
            "success": True,
            "words": transcription.get("words", []),
            "total_words": len(transcription.get("words", [])),
            "language": transcription.get("language"),
            "language_probability": transcription.get("language_probability"),
            "full_text": transcription.get("text", ""),
            "segments": transcription.get("segments", []),
            "audio_file": audio_path
        }
