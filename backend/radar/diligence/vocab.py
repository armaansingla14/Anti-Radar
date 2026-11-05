"""Named technologies the extractor and the evidence matchers recognise.

Each term carries the path tokens under which its support would show up in a repository (``H100`` →
``sm90``/``hopper``; ``ROCm`` → ``hip``/``gfx``), so "supports X" can be checked against the code tree
rather than against the company's own prose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Term:
    name: str
    pattern: re.Pattern[str]
    category: str  # hardware | os | framework | model | scale | language | cloud | compliance
    path_aliases: tuple[str, ...] = ()
    text_aliases: bool = True  # also look for the path aliases inside fetched config/manifest text


def _t(name: str, pattern: str, category: str, *aliases: str, text_aliases: bool = True) -> Term:
    return Term(name, re.compile(pattern, re.IGNORECASE), category, aliases, text_aliases)


TERMS: tuple[Term, ...] = (
    # hardware
    _t("H100", r"\bH100s?\b", "hardware", "h100", "sm90", "sm_90", "hopper"),
    _t("H200", r"\bH200s?\b", "hardware", "h200", "sm90", "hopper"),
    _t("A100", r"\bA100s?\b", "hardware", "a100", "sm80", "sm_80", "ampere"),
    _t("B200", r"\b(?:GB|B)200s?\b|\bBlackwell\b", "hardware", "b200", "gb200", "sm100", "blackwell"),
    _t("L40S", r"\bL40S?\b", "hardware", "l40", "l40s", "sm89", "ada"),
    _t("RTX 4090", r"\bRTX\s?(?:4090|5090|3090)\b", "hardware", "rtx", "sm89", "sm86"),
    _t("MI300X", r"\bMI3(?:00|25|50|55)[AX]?\b", "hardware", "mi300", "mi300x", "mi325", "gfx942"),
    _t("MI250", r"\bMI2(?:10|50)X?\b", "hardware", "mi250", "gfx90a"),
    _t("ROCm", r"\bROCm\b", "hardware", "rocm", "hip", "gfx"),
    _t("AMD GPUs", r"\bAMD\b", "hardware", "amd", "rocm", "hip", "gfx"),
    _t("CUDA", r"\bCUDA\b", "hardware", "cuda", "cu"),
    _t("NVIDIA GPUs", r"\bNVIDIA\b", "hardware", "nvidia", "cuda", "cu"),
    _t("Intel Gaudi", r"\bGaudi\s?\d?\b|\bHabana\b", "hardware", "gaudi", "habana", "hpu"),
    _t("Intel GPUs", r"\bIntel\b(?!\s+Gaudi)|\bXPUs?\b|\boneAPI\b", "hardware", "intel", "xpu", "oneapi", "ipex", "sycl"),
    _t("CPU", r"\bCPUs?\b", "hardware", "cpu"),
    _t("TPU", r"\bTPUs?\b", "hardware", "tpu", "xla"),
    _t("AWS Trainium/Inferentia", r"\bTrainium\b|\bInferentia\b|\bNeuron SDK\b", "hardware", "neuron", "trainium",
       "inferentia"),
    _t("Apple Silicon", r"\bApple Silicon\b|\bM[1-4] (?:Max|Pro|Ultra)\b|\bMetal\b|\bMLX\b", "hardware", "metal",
       "mps", "mlx", "apple"),
    _t("ARM", r"\bARM64\b|\baarch64\b|\bGraviton\b", "hardware", "arm64", "aarch64", "neon", "graviton"),
    # operating systems
    _t("Windows", r"\bWindows\b", "os", "windows", "win32", "win64", "msvc", "ps1"),
    _t("macOS", r"\bmacOS\b|\bOS X\b|\bMac\b", "os", "macos", "darwin", "osx"),
    _t("Linux", r"\bLinux\b", "os", "linux"),
    _t("Android", r"\bAndroid\b", "os", "android"),
    _t("iOS", r"\biOS\b", "os", "ios"),
    # frameworks / runtimes
    _t("PyTorch", r"\bPyTorch\b", "framework", "torch", "pytorch"),
    _t("JAX", r"\bJAX\b", "framework", "jax"),
    _t("TensorFlow", r"\bTensorFlow\b", "framework", "tensorflow"),
    _t("Triton", r"\bTriton\b", "framework", "triton"),
    _t("vLLM", r"\bvLLM\b", "framework", "vllm"),
    _t("SGLang", r"\bSGLang\b", "framework", "sglang"),
    _t("TensorRT", r"\bTensorRT(?:-LLM)?\b", "framework", "tensorrt", "trt"),
    _t("ONNX", r"\bONNX\b", "framework", "onnx"),
    _t("Hugging Face", r"\bHugging ?Face\b|\bTransformers library\b", "framework", "huggingface", "transformers",
       "hf"),
    _t("Kubernetes", r"\bKubernetes\b|\bK8s\b|\bHelm\b", "framework", "k8s", "kubernetes", "helm", "charts"),
    _t("Docker", r"\bDocker\b", "framework", "docker", "dockerfile"),
    _t("Ray", r"\bRay\b(?! ?tracing)", "framework", "ray"),
    _t("llama.cpp", r"\bllama\.cpp\b|\bGGUF\b", "framework", "llama_cpp", "llamacpp", "gguf"),
    # An OpenAI *client* dependency or OPENAI_API_KEY is not evidence of serving an OpenAI-compatible API.
    _t("OpenAI-compatible API", r"\bOpenAI[- ]compatible\b", "framework", "openai", text_aliases=False),
    _t("MCP", r"\bModel Context Protocol\b|\bMCP\b", "framework", "mcp"),
    # model families
    _t("Llama", r"\bLlama[- ]?\d*(?:\.\d+)?\b", "model", "llama"),
    _t("Mistral", r"\bMistral\b", "model", "mistral"),
    _t("Mixtral", r"\bMixtral\b", "model", "mixtral"),
    _t("Qwen", r"\bQwen\d*(?:\.\d+)?\b", "model", "qwen"),
    _t("DeepSeek", r"\bDeepSeek(?:-[A-Z0-9]+)?\b", "model", "deepseek"),
    _t("Gemma", r"\bGemma\s?\d*\b", "model", "gemma"),
    _t("Phi", r"\bPhi-\d\b", "model", "phi"),
    _t("GPT", r"\bGPT-?\d(?:\.\d)?o?\b", "model", "gpt"),
    _t("Claude", r"\bClaude\b", "model", "claude"),
    _t("Stable Diffusion", r"\bStable Diffusion\b|\bSDXL\b", "model", "diffusion", "sdxl"),
    _t("Whisper", r"\bWhisper\b", "model", "whisper"),
    # scale
    _t("multi-GPU", r"\bmulti[- ]?GPUs?\b|\bmultiple GPUs\b|\btensor[- ]parallel(?:ism)?\b", "scale",
       "distributed", "tensor_parallel", "nccl", "rccl", "multi_gpu", "parallel"),
    _t("multi-node", r"\bmulti[- ]?node\b|\bacross nodes\b|\bclusters?\b", "scale", "distributed", "multinode",
       "multi_node", "nccl", "rccl", "ray"),
    # languages (checked against the GitHub languages breakdown, not paths)
    _t("Rust", r"\bRust\b", "language", "rs"),
    _t("C++", r"\bC\+\+", "language", "cpp", "cc"),
    _t("Go", r"\bGo(?:lang)?\b(?= (?:code|binary|service|backend|implementation))|\bGolang\b", "language", "go"),
    _t("Python", r"\bPython\b", "language", "py"),
    _t("TypeScript", r"\bTypeScript\b", "language", "ts"),
    # clouds
    _t("AWS", r"\bAWS\b|\bAmazon Web Services\b", "cloud"),
    _t("Google Cloud", r"\bGCP\b|\bGoogle Cloud\b", "cloud"),
    _t("Azure", r"\bAzure\b", "cloud"),
    # compliance
    _t("SOC 2", r"\bSOC ?2\b", "compliance"),
    _t("HIPAA", r"\bHIPAA\b", "compliance"),
    _t("GDPR", r"\bGDPR\b", "compliance"),
    _t("ISO 27001", r"\bISO ?27001\b", "compliance"),
)

LANGUAGE_NAMES = {"Rust": "Rust", "C++": "C++", "Go": "Go", "Python": "Python", "TypeScript": "TypeScript"}


def find_terms(text: str, categories: tuple[str, ...] | None = None) -> list[Term]:
    out: list[Term] = []
    for t in TERMS:
        if categories and t.category not in categories:
            continue
        if t.pattern.search(text or ""):
            out.append(t)
    # "AMD GPUs" is implied by MI300X/ROCm; "NVIDIA GPUs" by H100/CUDA — keep the most specific only.
    names = {t.name for t in out}
    if names & {"MI300X", "MI250", "ROCm"}:
        out = [t for t in out if t.name != "AMD GPUs"]
    if names & {"H100", "H200", "A100", "B200", "L40S", "RTX 4090", "CUDA"}:
        out = [t for t in out if t.name != "NVIDIA GPUs"]
    return out


_SPLIT = re.compile(r"[/._\-\s]+")


def path_matches(path: str, aliases: tuple[str, ...]) -> bool:
    """True if a path segment names one of ``aliases`` (short aliases must match a whole token)."""
    toks = [t for t in _SPLIT.split(path.lower()) if t]
    for a in aliases:
        if a in toks:
            return True
        if len(a) >= 4 and any(a in t for t in toks):
            return True
    return False


CODE_EXTENSIONS = frozenset(
    "py pyx pxd cu cuh hip cpp cc cxx c h hpp hh rs go ts tsx js mjs java kt swift metal mm m cl zig scala jl".split()
)
NON_SOURCE_DIRS = ("docs/", "doc/", "documentation/", ".github/", "assets/", "images/", "website/", "site/")


def is_source_path(path: str) -> bool:
    """A code file outside documentation folders (where support for a target is a direct code artifact)."""
    p = path.lower()
    if any(p.startswith(d) or f"/{d}" in p for d in NON_SOURCE_DIRS):
        return False
    ext = p.rsplit(".", 1)[-1] if "." in p.rsplit("/", 1)[-1] else ""
    return ext in CODE_EXTENSIONS


# Benchmark baselines: display name -> (pattern, PyPI distribution or None when not on PyPI).
BASELINES: dict[str, tuple[re.Pattern[str], str | None]] = {
    "vLLM": (re.compile(r"\bvLLM\b", re.I), "vllm"),
    "SGLang": (re.compile(r"\bSGLang\b", re.I), "sglang"),
    "TensorRT-LLM": (re.compile(r"\bTensorRT-LLM\b|\bTRT-LLM\b", re.I), "tensorrt-llm"),
    "TGI": (re.compile(r"\bTGI\b|\btext-generation-inference\b", re.I), None),
    "llama.cpp": (re.compile(r"\bllama\.cpp\b", re.I), None),
    "PyTorch": (re.compile(r"\bPyTorch\b", re.I), "torch"),
    "Transformers": (re.compile(r"\b(?:HF |Hugging ?Face )?Transformers\b"), "transformers"),
    "ONNX Runtime": (re.compile(r"\bONNX ?Runtime\b", re.I), "onnxruntime"),
    "DeepSpeed": (re.compile(r"\bDeepSpeed\b", re.I), "deepspeed"),
    "FlashAttention": (re.compile(r"\bFlash-?Attention(?:-?\d)?\b", re.I), "flash-attn"),
    "LMDeploy": (re.compile(r"\bLMDeploy\b", re.I), "lmdeploy"),
    "Ollama": (re.compile(r"\bOllama\b", re.I), None),
}

_VER = r"(?:\s+|\s*v|\s+version\s+|\s+v\.?\s*)(\d+\.\d+(?:\.\d+)?)\b"


def baseline_mentions(text: str) -> list[tuple[str, str | None, str]]:
    """[(baseline name, version or None, matched span)] in order of appearance (deduplicated by name+version)."""
    out: list[tuple[int, str, str | None, str]] = []
    seen: set[tuple[str, str | None]] = set()
    for name, (pat, _pkg) in BASELINES.items():
        for m in pat.finditer(text or ""):
            vm = re.match(_VER, text[m.end():m.end() + 24], re.I)
            ver = vm.group(1) if vm else None
            key = (name, ver)
            if key in seen:
                continue
            seen.add(key)
            span = text[m.start():m.end() + (vm.end() if vm else 0)]
            out.append((m.start(), name, ver, span))
    return [(n, v, s) for _, n, v, s in sorted(out)]
