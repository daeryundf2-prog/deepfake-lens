"""AI tool classification module.

Classifies which specific AI tool or model was used to generate
media content based on metadata signatures, spectral profiles,
and learned patterns.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ToolMatch:
    name: str
    provider: str
    category: str
    confidence: float
    evidence: list[str]

    def to_json(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ClassificationResult:
    matches: list[ToolMatch]
    primary_match: ToolMatch | None
    category: str
    confidence: str

    def to_json(self) -> dict[str, object]:
        return asdict(self)


# Image generation tools
IMAGE_TOOLS = [
    {"name": "Nano Banana 2", "provider": "Google", "markers": ["nano banana", "nanobanana", "google ai studio", "gemini", "google ai", "nano-banana"], "category": "image"},
    {"name": "Imagen 4", "provider": "Google", "markers": ["imagen", "google imagen", "imagen 4", "google ai", "imagen-4"], "category": "image"},
    {"name": "GPT Image", "provider": "OpenAI", "markers": ["dall-e", "dalle", "gpt image", "chatgpt image", "openai", "gpt", "dall-e-3"], "category": "image"},
    {"name": "DALL-E 3", "provider": "OpenAI", "markers": ["dall-e 3", "dalle3", "dall-e 3", "dall-e", "openai", "dall-e-3"], "category": "image"},
    {"name": "Stable Diffusion 3", "provider": "Stability AI", "markers": ["stable diffusion 3", "sd3", "stablediffusion3", "stability ai", "stability", "sd-3"], "category": "image"},
    {"name": "SDXL", "provider": "Stability AI", "markers": ["sdxl", "stable diffusion xl", "sd_xl", "stable diffusion", "sd-xl", "sdxl-base"], "category": "image"},
    {"name": "FLUX.1", "provider": "Black Forest Labs", "markers": ["flux.1", "flux1", "black forest labs", "bfl", "flux", "flux-dev"], "category": "image"},
    {"name": "FLUX.2", "provider": "Black Forest Labs", "markers": ["flux.2", "flux2", "flux", "flux-v2"], "category": "image"},
    {"name": "Midjourney v6", "provider": "Midjourney", "markers": ["midjourney", "mj v6", "/imagine", "mj", "midjourney-bot", "midjourney-discord"], "category": "image"},
    {"name": "Adobe Firefly 3", "provider": "Adobe", "markers": ["adobe firefly", "firefly", "adobe api", "photoshop ai"], "category": "image"},
    {"name": "SeedDream 4.0", "provider": "ByteDance", "markers": ["seeddream", "bytedance", "doubao", "tiktok", "seeddream-4"], "category": "image"},
    {"name": "Ideogram 2.0", "provider": "Ideogram", "markers": ["ideogram", "ideogram ai", "ideogram-2", "ideogram-api"], "category": "image"},
    {"name": "Leonardo.Ai", "provider": "Leonardo", "markers": ["leonardo.ai", "leonardo ai", "leonardo", "leonardo-ai", "leonardo-api"], "category": "image"},
    {"name": "Recraft V3", "provider": "Recraft", "markers": ["recraft", "recraft v3", "recraft-v3", "recraft-api"], "category": "image"},
    {"name": "Canva AI", "provider": "Canva", "markers": ["canva ai", "magic media", "canva design", "canva-ai", "canva-api", "canva magic"], "category": "image"},
    {"name": "NVIDIA Canvas", "provider": "NVIDIA", "markers": ["nvidia canvas", "nvidia ai", "canvas-v2", "rtx"], "category": "image"},
    {"name": "Grok Image", "provider": "xAI", "markers": ["grok image", "xai image", "grok", "xai", "grok-ai", "xai-api"], "category": "image"},
    {"name": "Qwen-VL", "provider": "Alibaba", "markers": ["qwen-vl", "tongyi", "alibaba", "qwen", "qwen-vl-2", "alibaba-ai"], "category": "image"},
    {"name": "GLM-Image", "provider": "Zhipu", "markers": ["glm-image", "zhipu", "chatglm", "glm", "glm-4", "zhipu-ai"], "category": "image"},
    {"name": "Meta Imagine", "provider": "Meta", "markers": ["meta imagine", "emupic", "meta ai", "meta", "meta-imagine", "meta-ai"], "category": "image"},
    {"name": "ComfyUI", "provider": "Open Source", "markers": ["comfyui", "comfy ui", "workflow", "comfy", "comfyui workflow", "comfyui-node"], "category": "image"},
    {"name": "AUTOMATIC1111", "provider": "Open Source", "markers": ["automatic1111", "a1111", "stable diffusion webui", "dpm++", "karras", "webui", "automatic", "automatic1111-webui"], "category": "image"},
    {"name": "Invoke AI", "provider": "Open Source", "markers": ["invoke ai", "invokeai", "invoke", "invoke-ai", "invoke-api"], "category": "image"},
    {"name": "Craiyon", "provider": "Craiyon", "markers": ["craiyon", "dall-e mini", "craiyon ai", "craiyon-api", "craiyon-v3"], "category": "image"},
    {"name": "Draw Things", "provider": "Draw Things", "markers": ["draw things", "draw-things", "draw things ai", "draw-things-api", "draw-things-v2"], "category": "image"},
    {"name": "Stable Horde", "provider": "Stable Horde", "markers": ["stable horde", "stablehorde", "stable horde ai", "stable-horde-api", "stable-horde-v3"], "category": "image"},
    {"name": "Wombo Dream", "provider": "Wombo", "markers": ["wombo", "wombo dream", "wombo ai", "wombo-api", "wombo-dream"], "category": "image"},
    {"name": "NightCafe", "provider": "NightCafe", "markers": ["nightcafe", "night cafe", "nightcafe ai", "nightcafe-api"], "category": "image"},
    {"name": "Deep Dream Generator", "provider": "Deep Dream", "markers": ["deep dream", "deepdream", "deep dream generator", "deepdream-api"], "category": "image"},
]

# Video generation tools
VIDEO_TOOLS = [
    {"name": "Veo 3", "provider": "Google", "markers": ["veo 3", "veo3", "google veo"], "category": "video"},
    {"name": "Sora 2", "provider": "OpenAI", "markers": ["sora 2", "sora2", "openai sora"], "category": "video"},
    {"name": "Kling 3.0", "provider": "Kuaishou", "markers": ["kling 3", "kling3", "kuaishou"], "category": "video"},
    {"name": "Hailuo 2.3", "provider": "MiniMax", "markers": ["hailuo", "minimax video"], "category": "video"},
    {"name": "Runway Gen-4", "provider": "Runway", "markers": ["runway gen", "gen-4", "gen4"], "category": "video"},
    {"name": "Pika 2.0", "provider": "Pika", "markers": ["pika 2", "pika2"], "category": "video"},
    {"name": "Luma Ray2", "provider": "Luma", "markers": ["luma ray", "ray2"], "category": "video"},
    {"name": "Seedance 2.0", "provider": "ByteDance", "markers": ["seedance", "bytedance video"], "category": "video"},
    {"name": "Wan 2.6", "provider": "Alibaba", "markers": ["wan 2", "wan2", "alibaba video"], "category": "video"},
    {"name": "PixVerse V6", "provider": "PixVerse", "markers": ["pixverse"], "category": "video"},
    {"name": "Stable Video Diffusion", "provider": "Stability AI", "markers": ["stable video", "svd", "stable video diffusion"], "category": "video"},
    {"name": "AnimateDiff", "provider": "AnimateDiff", "markers": ["animatediff", "animate diff"], "category": "video"},
    {"name": "ModelScope", "provider": "Alibaba", "markers": ["modelscope", "model scope", "damo"], "category": "video"},
    {"name": "VideoCrafter", "provider": "VideoCrafter", "markers": ["videocrafter", "video crafter"], "category": "video"},
    {"name": "Veo 2", "provider": "Google", "markers": ["veo 2", "veo2", "google veo 2"], "category": "video"},
    {"name": "Gen-3 Alpha", "provider": "Runway", "markers": ["gen-3", "gen3", "gen-3 alpha"], "category": "video"},
    {"name": "Kling 1.5", "provider": "Kuaishou", "markers": ["kling 1.5", "kling1.5", "kuaishou 1.5"], "category": "video"},
    {"name": "Pika 1.0", "provider": "Pika", "markers": ["pika 1.0", "pika1.0", "pika labs"], "category": "video"},
]

# Audio generation tools
AUDIO_TOOLS = [
    {"name": "ElevenLabs", "provider": "ElevenLabs", "markers": ["elevenlabs", "eleven labs", "eleven voice"], "category": "audio"},
    {"name": "Suno v5", "provider": "Suno", "markers": ["suno", "suno.ai", "suno music"], "category": "audio"},
    {"name": "Udio 2", "provider": "Udio", "markers": ["udio", "udio ai"], "category": "audio"},
    {"name": "OpenAI TTS", "provider": "OpenAI", "markers": ["openai tts", "chatgpt voice", "openai voice"], "category": "audio"},
    {"name": "Lyria 3", "provider": "Google", "markers": ["lyria", "google music", "google lyria"], "category": "audio"},
    {"name": "RVC", "provider": "Open Source", "markers": ["rvc", "retrieval-based voice", "rvc voice"], "category": "audio"},
    {"name": "So-VITS-SVC", "provider": "Open Source", "markers": ["so-vits", "sovits", "softvc"], "category": "audio"},
    {"name": "VALL-E 2", "provider": "Microsoft", "markers": ["vall-e", "valle", "vall-e 2"], "category": "audio"},
    {"name": "XTTS v2", "provider": "Coqui", "markers": ["xtts", "coqui", "coqui tts"], "category": "audio"},
    {"name": "Bark", "provider": "Suno", "markers": ["bark", "suno bark"], "category": "audio"},
    {"name": "Tortoise TTS", "provider": "Tortoise", "markers": ["tortoise", "tortoise tts"], "category": "audio"},
    {"name": "DDSP-SVC", "provider": "Google", "markers": ["ddsp", "ddsp-svc", "differentiable dsp"], "category": "audio"},
    {"name": "GPT-SoVITS", "provider": "GPT-SoVITS", "markers": ["gpt-sovits", "gpt sovits"], "category": "audio"},
    {"name": "OpenVoice", "provider": "MyShell", "markers": ["openvoice", "open voice", "myshell"], "category": "audio"},
    {"name": "Fish Speech", "provider": "Fish Audio", "markers": ["fish speech", "fish audio", "fishspeech"], "category": "audio"},
    {"name": "CosyVoice", "provider": "Alibaba", "markers": ["cosyvoice", "cosy voice", "cosy"], "category": "audio"},
    {"name": "MusicGen", "provider": "Meta", "markers": ["musicgen", "music gen", "meta music"], "category": "audio"},
    {"name": "Stable Audio", "provider": "Stability AI", "markers": ["stable audio", "stableaudio"], "category": "audio"},
    {"name": "Riffusion", "provider": "Riffusion", "markers": ["riffusion", "riffusion ai"], "category": "audio"},
    {"name": "AudioCraft", "provider": "Meta", "markers": ["audiocraft", "audio craft", "meta audiocraft"], "category": "audio"},
]

# Detection tools
DETECTION_TOOLS = [
    {"name": "AIDE", "provider": "Research", "markers": ["aide", "ai-generated image detector"], "category": "detection"},
    {"name": "CLIDE", "provider": "Research", "markers": ["clide", "conditional likelihood"], "category": "detection"},
    {"name": "DIFC-Net", "provider": "Research", "markers": ["difc-net", "diffusion intrinsic"], "category": "detection"},
]

# Text generation tools (for text content classification)
TEXT_TOOLS = [
    {"name": "ChatGPT", "provider": "OpenAI", "markers": ["chatgpt", "openai", "gpt-4", "gpt-3.5"], "category": "text"},
    {"name": "Claude", "provider": "Anthropic", "markers": ["claude", "anthropic"], "category": "text"},
    {"name": "Gemini", "provider": "Google", "markers": ["gemini", "bard", "google ai"], "category": "text"},
    {"name": "Llama", "provider": "Meta", "markers": ["llama", "meta ai"], "category": "text"},
    {"name": "Mistral", "provider": "Mistral", "markers": ["mistral", "mixtral"], "category": "text"},
    {"name": "Mistral", "provider": "Mistral", "markers": ["mistral", "mixtral"], "category": "text"},
]

# Additional image generation tools (regional)
ADDITIONAL_IMAGE_TOOLS = [
    {"name": "Clova Dubbing", "provider": "Naver", "markers": ["clova", "clova dubbing", "naver"], "category": "image"},
    {"name": "Getty Images AI", "provider": "Getty Images", "markers": ["getty images ai", "getty ai", "getty generative"], "category": "image"},
    {"name": "Shutterstock AI", "provider": "Shutterstock", "markers": ["shutterstock ai", "shutterstock generative"], "category": "image"},
    {"name": "Picsart AI", "provider": "Picsart", "markers": ["picsart ai", "picsart generative"], "category": "image"},
    {"name": "DeepSeek", "provider": "DeepSeek", "markers": ["deepseek", "deep seek", "deepseek-v3", "deepseek-chat"], "category": "image"},
    {"name": "Kimi", "provider": "Moonshot AI", "markers": ["kimi", "moonshot", "moonshot ai", "kimi-chat"], "category": "image"},
    {"name": "MiniMax TTS", "provider": "MiniMax", "markers": ["minimax tts", "minimax audio", "minimax voice"], "category": "audio"},
    {"name": "Baidu ERNIE", "provider": "Baidu", "markers": ["ernie", "baidu ernie", "wenxin", "wenxin yiyan"], "category": "image"},
    {"name": "Tencent Hunyuan", "provider": "Tencent", "markers": ["hunyuan", "tencent hunyuan", "混元"], "category": "image"},
    {"name": "iFlytek Spark", "provider": "iFlytek", "markers": ["spark", "iflytek", "讯飞星火", "xunfei"], "category": "image"},
    {"name": "01.AI Yi", "provider": "01.AI", "markers": ["01.ai", "yi-vl", "yi image", "零一万物"], "category": "image"},
    {"name": "SenseTime SenseNova", "provider": "SenseTime", "markers": ["sensenova", "sensetime", "商汤"], "category": "image"},
    {"name": "Huawei Pangu", "provider": "Huawei", "markers": ["pangu", "huawei pangu", "盘古"], "category": "image"},
    {"name": "Xiaomi MiLM", "provider": "Xiaomi", "markers": ["milm", "xiaomi ai", "小米AI"], "category": "image"},
    {"name": "Baichuan", "provider": "Baichuan", "markers": ["baichuan", "百川", "baichuan-4"], "category": "image"},
    {"name": "StepFun", "provider": "StepFun", "markers": ["stepfun", "step-1", "阶跃星辰"], "category": "image"},
    {"name": "MiniMax Image", "provider": "MiniMax", "markers": ["minimax image", "minimax gen", "abab", "minimax"], "category": "image"},
    {"name": "ByteDance Jimeng", "provider": "ByteDance", "markers": ["jimeng", "即梦", "jimeng ai"], "category": "image"},
    # Face Swap tools
    {"name": "DeepFaceLab", "provider": "DeepFaceLab", "markers": ["deepfacelab", "deep face lab", "dfl"], "category": "face"},
    {"name": "FaceSwap", "provider": "FaceSwap", "markers": ["faceswap", "face swap", "faceswap-py"], "category": "face"},
    {"name": "SimSwap", "provider": "SimSwap", "markers": ["simswap", "sim swap"], "category": "face"},
    {"name": "FaceShifter", "provider": "FaceShifter", "markers": ["faceshifter", "face shifter"], "category": "face"},
    {"name": "Roop", "provider": "Roop", "markers": ["roop", "roop-uno"], "category": "face"},
    {"name": "FaceFusion", "provider": "FaceFusion", "markers": ["facefusion", "face fusion"], "category": "face"},
    {"name": "Deep-Live-Cam", "provider": "Deep-Live-Cam", "markers": ["deep-live-cam", "deeplivecam"], "category": "face"},
    {"name": "Reface", "provider": "Reface", "markers": ["reface", "reface ai"], "category": "face"},
    # Face Reenactment tools
    {"name": "LivePortrait", "provider": "LivePortrait", "markers": ["liveportrait", "live portrait"], "category": "face"},
    {"name": "SadTalker", "provider": "SadTalker", "markers": ["sadtalker", "sad talker"], "category": "face"},
    {"name": "First Order Motion", "provider": "First Order", "markers": ["first order motion", "fom"], "category": "face"},
    {"name": "AniPortrait", "provider": "Tencent", "markers": ["aniportrait", "ani portrait"], "category": "face"},
    {"name": "Wav2Lip", "provider": "Wav2Lip", "markers": ["wav2lip", "wav2 lip"], "category": "face"},
    {"name": "VideoReTalking", "provider": "VideoReTalking", "markers": ["videoretalking", "video retalking"], "category": "face"},
    {"name": "MuseTalk", "provider": "MuseTalk", "markers": ["musetalk", "muse talk"], "category": "face"},
    {"name": "Hallo", "provider": "Hallo", "markers": ["hallo", "hallo2"], "category": "face"},
    {"name": "MimicMotion", "provider": "MimicMotion", "markers": ["mimicmotion", "mimic motion"], "category": "face"},
]


def classify_metadata(metadata: dict[str, str]) -> ClassificationResult:
    """Classify which AI tool was used based on metadata."""
    blob = "\n".join(f"{key}: {value}" for key, value in metadata.items()).lower()
    return _classify_text(blob)


def classify_text_content(text: str) -> ClassificationResult:
    """Classify which AI tool was used based on text content."""
    normalized = text.lower()
    return _classify_text(normalized)


def classify_spectral_profile(profile: dict[str, float]) -> ClassificationResult:
    """Classify based on spectral analysis profile."""
    # Simplified: use profile features to match known patterns
    matches = []

    # Check for known spectral signatures
    if profile.get("spectral_flatness", 0) > 0.7:
        matches.append(ToolMatch(
            name="Synthetic Audio",
            provider="Unknown",
            category="audio",
            confidence=0.6,
            evidence=["High spectral flatness"],
        ))

    if profile.get("pitch_stability", 0) > 0.9:
        matches.append(ToolMatch(
            name="TTS Engine",
            provider="Unknown",
            category="audio",
            confidence=0.5,
            evidence=["High pitch stability"],
        ))

    if not matches:
        return ClassificationResult(
            matches=[],
            primary_match=None,
            category="unknown",
            confidence="low",
        )

    primary = max(matches, key=lambda m: m.confidence)
    return ClassificationResult(
        matches=matches,
        primary_match=primary,
        category=primary.category,
        confidence=_confidence_label(primary.confidence),
    )


def _classify_text(text: str) -> ClassificationResult:
    """Classify based on text matching."""
    all_tools = IMAGE_TOOLS + VIDEO_TOOLS + AUDIO_TOOLS + DETECTION_TOOLS + TEXT_TOOLS + ADDITIONAL_IMAGE_TOOLS
    matches = []

    for tool in all_tools:
        tool_matches = []
        for marker in tool["markers"]:
            if marker in text:
                tool_matches.append(f"Marker '{marker}' found")
        
        if tool_matches:
            # More matches = higher confidence
            # Base: 0.6 for 1 match, +0.15 per additional match, max 1.0
            confidence = min(1.0, 0.6 + (len(tool_matches) - 1) * 0.15)
            matches.append(ToolMatch(
                name=tool["name"],
                provider=tool["provider"],
                category=tool["category"],
                confidence=confidence,
                evidence=tool_matches,
            ))

    if not matches:
        return ClassificationResult(
            matches=[],
            primary_match=None,
            category="unknown",
            confidence="low",
        )

    # Sort by confidence
    matches.sort(key=lambda m: m.confidence, reverse=True)
    primary = matches[0]

    return ClassificationResult(
        matches=matches[:5],  # Top 5
        primary_match=primary,
        category=primary.category,
        confidence=_confidence_label(primary.confidence),
    )


def _confidence_label(confidence: float) -> str:
    """Convert confidence score to label."""
    if confidence >= 0.8:
        return "high"
    if confidence >= 0.5:
        return "medium"
    return "low"


def list_all_tools() -> dict[str, list[dict[str, object]]]:
    """List all known AI tools by category."""
    return {
        "image": IMAGE_TOOLS,
        "video": VIDEO_TOOLS,
        "audio": AUDIO_TOOLS,
        "detection": DETECTION_TOOLS,
    }


def get_tool_info(name: str) -> dict[str, object] | None:
    """Get information about a specific tool."""
    all_tools = IMAGE_TOOLS + VIDEO_TOOLS + AUDIO_TOOLS + DETECTION_TOOLS
    for tool in all_tools:
        if tool["name"].lower() == name.lower():
            return tool
    return None
