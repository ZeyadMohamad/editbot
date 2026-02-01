"""
EditBot System Test - Verify all components are working.
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

# Try colorama
try:
    from colorama import init, Fore, Style
    init()
except ImportError:
    class Fore:
        CYAN = YELLOW = GREEN = RED = GRAY = ""
    class Style:
        RESET_ALL = ""


def test_imports():
    """Test all imports work"""
    print(f"{Fore.YELLOW}Testing imports...{Style.RESET_ALL}")
    success = True
    
    tests = [
        ("core.schema", lambda: __import__("core.schema")),
        ("core.config_loader", lambda: __import__("core.config_loader")),
        ("core.orchestrator", lambda: __import__("core.orchestrator")),
        ("core.planner", lambda: __import__("core.planner")),
        ("core.state", lambda: __import__("core.state")),
    ]
    
    for name, test_fn in tests:
        try:
            test_fn()
            print(f"  {Fore.GREEN}✓{Style.RESET_ALL} {name}")
        except Exception as e:
            print(f"  {Fore.RED}✗{Style.RESET_ALL} {name}: {e}")
            success = False
    
    # Test tools
    try:
        from tools import get_all_tools
        tools = get_all_tools()
        print(f"  {Fore.GREEN}✓{Style.RESET_ALL} tools ({len(tools)} registered)")
    except Exception as e:
        print(f"  {Fore.RED}✗{Style.RESET_ALL} tools: {e}")
        success = False
    
    return success


def test_configs():
    """Test config loading"""
    print(f"\n{Fore.YELLOW}Testing config system...{Style.RESET_ALL}")
    
    try:
        from core.config_loader import ConfigLoader
        loader = ConfigLoader()
        
        configs_to_test = ["fonts", "colors", "highlight_styles", "positions", "supported_formats"]
        
        for config_name in configs_to_test:
            config = loader.get_config(config_name)
            if config:
                print(f"  {Fore.GREEN}✓{Style.RESET_ALL} {config_name}.json loaded")
            else:
                print(f"  {Fore.RED}✗{Style.RESET_ALL} {config_name}.json not found")
                return False
        
        return True
    except Exception as e:
        print(f"  {Fore.RED}✗{Style.RESET_ALL} Config loading failed: {e}")
        return False


def test_ollama():
    """Test Ollama connection"""
    print(f"\n{Fore.YELLOW}Testing Ollama (Llama 3)...{Style.RESET_ALL}")
    
    try:
        import ollama
        
        models = ollama.list()
        model_names = [m.get('name', m.get('model', '')) for m in models.get('models', [])]
        
        # Check for llama3 variants
        llama3_found = any('llama3' in name.lower() for name in model_names)
        
        if llama3_found:
            print(f"  {Fore.GREEN}✓{Style.RESET_ALL} Ollama running, llama3 available")
            return True
        else:
            print(f"  {Fore.YELLOW}⚠{Style.RESET_ALL} Ollama running, but llama3 not found")
            print(f"    Available: {model_names[:5]}...")
            print(f"    Run: ollama pull llama3")
            return False
            
    except Exception as e:
        print(f"  {Fore.RED}✗{Style.RESET_ALL} Ollama connection failed: {e}")
        print(f"    Make sure Ollama is running: ollama serve")
        return False


def test_ffmpeg():
    """Test FFmpeg installation"""
    print(f"\n{Fore.YELLOW}Testing FFmpeg...{Style.RESET_ALL}")
    
    try:
        import subprocess
        result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True)
        if result.returncode == 0:
            version = result.stdout.split('\n')[0]
            print(f"  {Fore.GREEN}✓{Style.RESET_ALL} {version[:60]}...")
            return True
        else:
            print(f"  {Fore.RED}✗{Style.RESET_ALL} FFmpeg not working")
            return False
    except FileNotFoundError:
        print(f"  {Fore.RED}✗{Style.RESET_ALL} FFmpeg not found. Please install FFmpeg.")
        return False


def test_whisper():
    """Test Whisper installation"""
    print(f"\n{Fore.YELLOW}Testing Whisper...{Style.RESET_ALL}")
    
    # Test openai-whisper
    try:
        import whisper
        print(f"  {Fore.GREEN}✓{Style.RESET_ALL} openai-whisper installed")
        whisper_ok = True
    except ImportError:
        print(f"  {Fore.RED}✗{Style.RESET_ALL} openai-whisper not installed")
        whisper_ok = False
    
    # Test faster-whisper (optional but better)
    try:
        from faster_whisper import WhisperModel
        print(f"  {Fore.GREEN}✓{Style.RESET_ALL} faster-whisper installed (recommended)")
    except ImportError:
        print(f"  {Fore.YELLOW}⚠{Style.RESET_ALL} faster-whisper not installed (optional)")
    
    return whisper_ok


def test_torch():
    """Test PyTorch and CUDA"""
    print(f"\n{Fore.YELLOW}Testing PyTorch/CUDA...{Style.RESET_ALL}")
    
    try:
        import torch
        print(f"  {Fore.GREEN}✓{Style.RESET_ALL} PyTorch {torch.__version__}")
        
        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0)
            print(f"  {Fore.GREEN}✓{Style.RESET_ALL} CUDA available: {gpu_name}")
            return True
        else:
            print(f"  {Fore.YELLOW}⚠{Style.RESET_ALL} CUDA not available (will use CPU)")
            return True
            
    except ImportError:
        print(f"  {Fore.RED}✗{Style.RESET_ALL} PyTorch not installed")
        return False


def test_tools():
    """Test individual tools"""
    print(f"\n{Fore.YELLOW}Testing tools...{Style.RESET_ALL}")
    
    try:
        from tools.ffmpeg_tool import FFmpegTool
        ffmpeg = FFmpegTool()
        print(f"  {Fore.GREEN}✓{Style.RESET_ALL} FFmpegTool initialized")
    except Exception as e:
        print(f"  {Fore.RED}✗{Style.RESET_ALL} FFmpegTool: {e}")
        return False
    
    try:
        from tools.whisperx_tool import WhisperXTool
        # Don't load model yet, just test import
        print(f"  {Fore.GREEN}✓{Style.RESET_ALL} WhisperXTool available")
    except Exception as e:
        print(f"  {Fore.RED}✗{Style.RESET_ALL} WhisperXTool: {e}")
        return False
    
    try:
        from tools.captions_tool import CaptionsTool
        captions = CaptionsTool()
        print(f"  {Fore.GREEN}✓{Style.RESET_ALL} CaptionsTool initialized")
    except Exception as e:
        print(f"  {Fore.RED}✗{Style.RESET_ALL} CaptionsTool: {e}")
        return False
    
    return True


def main():
    print(f"\n{Fore.CYAN}{'='*50}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}EditBot System Test{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{'='*50}{Style.RESET_ALL}\n")
    
    results = []
    
    results.append(("Imports", test_imports()))
    results.append(("Configs", test_configs()))
    results.append(("PyTorch/CUDA", test_torch()))
    results.append(("FFmpeg", test_ffmpeg()))
    results.append(("Whisper", test_whisper()))
    results.append(("Ollama", test_ollama()))
    results.append(("Tools", test_tools()))
    
    # Summary
    print(f"\n{Fore.CYAN}{'='*50}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Summary{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{'='*50}{Style.RESET_ALL}\n")
    
    passed = sum(1 for _, r in results if r)
    total = len(results)
    
    for name, result in results:
        status = f"{Fore.GREEN}PASS{Style.RESET_ALL}" if result else f"{Fore.RED}FAIL{Style.RESET_ALL}"
        print(f"  {name}: {status}")
    
    print(f"\n  Total: {passed}/{total} passed")
    
    if passed == total:
        print(f"\n{Fore.GREEN}🎉 All systems ready!{Style.RESET_ALL}")
        print(f"{Fore.CYAN}Run: python -m app.main --interactive{Style.RESET_ALL}")
    else:
        print(f"\n{Fore.YELLOW}⚠️  Some components need attention.{Style.RESET_ALL}")
    
    return 0 if passed >= 5 else 1


if __name__ == "__main__":
    sys.exit(main())
