"""Static lookup table of known model context sizes.

Used as a fallback when provider APIs do not expose context_length
in their ``/v1/models`` response.  Models not listed here (and not
returned by the provider) get ``context_size = None`` and are treated
as having unknown context — the proxy will not cap ``max_tokens``
for them and will not skip them during failover.

Sources
-------
* OpenRouter model list (https://openrouter.ai/models)
* OpenAI model docs
* Anthropic model docs
* Provider model cards for Llama, Mistral, Gemma, Qwen, DeepSeek, etc.
"""

# Maps a lower-cased model identifier to its context window in tokens.
# Suffix-only entries (e.g. "llama-3.1-70b") match any model whose
# lower-cased name *ends with* the key (so "meta-llama/llama-3.1-70b-instruct:free"
# matches "llama-3.1-70b").
_CONTEXT_SIZES: dict[str, int] = {
    # ── OpenAI ──────────────────────────────────────────────────────
    "gpt-4o": 128000,
    "gpt-4o-2024-08-06": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4o-mini-2024-07-18": 128000,
    "gpt-4-turbo": 128000,
    "gpt-4-turbo-preview": 128000,
    "gpt-4-0125-preview": 128000,
    "gpt-4": 8192,
    "gpt-4-32k": 32768,
    "gpt-3.5-turbo": 16385,
    "gpt-3.5-turbo-0125": 16385,
    "gpt-3.5-turbo-1106": 16385,
    "gpt-3.5-turbo-16k": 16385,
    # ── Anthropic ────────────────────────────────────────────────────
    "claude-3-5-sonnet": 200000,
    "claude-3-5-haiku": 200000,
    "claude-3-opus": 200000,
    "claude-3-sonnet": 200000,
    "claude-3-haiku": 200000,
    "claude-2": 100000,
    "claude-2.1": 200000,
    "claude-instant-1": 100000,
    # ── Meta / Llama ─────────────────────────────────────────────────
    "llama-3.1-405b": 131072,
    "llama-3.1-70b": 131072,
    "llama-3.1-8b": 131072,
    "llama-3-70b": 8192,
    "llama-3-8b": 8192,
    "llama-3.2-90b": 131072,
    "llama-3.2-11b": 131072,
    "llama-3.2-3b": 131072,
    "llama-3.2-1b": 131072,
    "llama-2-70b": 4096,
    "llama-2-13b": 4096,
    "llama-2-7b": 4096,
    # ── Mistral ──────────────────────────────────────────────────────
    "mistral-large": 131072,
    "mistral-large-2407": 131072,
    "mistral-medium": 32768,
    "mistral-small": 32768,
    "mistral-7b": 32768,
    "mixtral-8x22b": 65536,
    "mixtral-8x7b": 32768,
    "codestral": 256000,
    # ── Google / Gemma ───────────────────────────────────────────────
    "gemini-2.0-flash": 1048576,
    "gemini-1.5-pro": 1048576,
    "gemini-1.5-flash": 1048576,
    "gemini-1.5-flash-8b": 1048576,
    "gemma-2-27b": 8192,
    "gemma-2-9b": 8192,
    "gemma-2-2b": 8192,
    "gemma-7b": 8192,
    # ── Qwen ─────────────────────────────────────────────────────────
    "qwen-2.5-72b": 131072,
    "qwen-2.5-32b": 32768,
    "qwen-2.5-14b": 32768,
    "qwen-2.5-7b": 32768,
    "qwen-2.5-coder-32b": 32768,
    "qwen-2.5-coder-7b": 32768,
    "qwen-2-72b": 32768,
    "qwen-2-7b": 32768,
    "qwen-1.5-110b": 32768,
    "qwen-1.5-72b": 32768,
    "qwen-1.5-32b": 32768,
    "qwen-1.5-14b": 32768,
    "qwen-1.5-7b": 32768,
    # ── DeepSeek ─────────────────────────────────────────────────────
    "deepseek-chat": 65536,
    "deepseek-v3": 65536,
    "deepseek-coder": 65536,
    "deepseek-r1": 65536,
    "deepseek-v2": 131072,
    # ── Phi / Microsoft ──────────────────────────────────────────────
    "phi-3.5-mini-128k": 128000,
    "phi-3.5-moe": 128000,
    "phi-3-mini-128k": 128000,
    "phi-3-mini-4k": 4096,
    "phi-3-medium-128k": 128000,
    "phi-3-medium-4k": 4096,
    "phi-3-small-128k": 128000,
    "phi-3-small-8k": 8192,
    "phi-2": 2048,
    # ── Nvidia ───────────────────────────────────────────────────────
    "nemotron-3-ultra-550b-a55b": 4096,
    "nemotron-4-340b": 4096,
    "llama-3.1-nemotron-70b": 131072,
    # ── Cohere / Command ─────────────────────────────────────────────
    "command-r-plus": 128000,
    "command-r": 128000,
    "command": 4096,
    # ── AI21 / Jamba ─────────────────────────────────────────────────
    "jamba-1.5-large": 256000,
    "jamba-1.5-mini": 256000,
    # ── Databricks / DBRX ────────────────────────────────────────────
    "dbrx-instruct": 32768,
    # ── xAI / Grok ───────────────────────────────────────────────────
    "grok-2": 131072,
    "grok-2-mini": 131072,
    "grok-1": 8192,
    # ── Perplexity ───────────────────────────────────────────────────
    "sonar-pro": 200000,
    "sonar-small": 200000,
    # ── Reka ─────────────────────────────────────────────────────────
    "reka-core": 131072,
    "reka-flash": 131072,
    "reka-edge": 131072,
}


def lookup_context_size(model_name: str) -> int | None:
    """Return the known context size (in tokens) for *model_name*, or ``None``.

    Matching is case-insensitive.  The function tries several strategies
    in order:

    1. Exact match against the lookup table.
    2. Strip common suffixes (``:free``, ``-instruct``, etc.) and retry the
       exact match.
    3. Take the last segment after ``/`` (strips provider prefixes like
       ``"openai/"`` or ``"meta-llama/"``) and try strategies 1-2 on it.
    4. Substring match — check if any known key is a substring of the
       model name, picking the longest match to avoid false positives.
    """
    key = model_name.lower().strip()

    # Strategy 1: exact match
    if key in _CONTEXT_SIZES:
        return _CONTEXT_SIZES[key]

    # Strategy 2: strip common suffixes
    stripped = _strip_suffixes(key)
    if stripped != key and stripped in _CONTEXT_SIZES:
        return _CONTEXT_SIZES[stripped]

    # Strategy 3: take the last segment after '/' (provider prefix)
    if "/" in stripped:
        last_seg = stripped.split("/")[-1]
        if last_seg in _CONTEXT_SIZES:
            return _CONTEXT_SIZES[last_seg]
        # Try stripping suffixes from the last segment too
        last_stripped = _strip_suffixes(last_seg)
        if last_stripped != last_seg and last_stripped in _CONTEXT_SIZES:
            return _CONTEXT_SIZES[last_stripped]

    # Strategy 4: substring match — find the longest known key contained
    # in the model name (avoids "gpt-4" matching "gpt-4o-mini" for 8K)
    best_size = None
    best_len = 0
    for known, size in _CONTEXT_SIZES.items():
        if known in key and len(known) > best_len:
            best_size = size
            best_len = len(known)
    if best_size is not None:
        return best_size

    return None


def _strip_suffixes(name: str) -> str:
    """Remove common suffixes from a model name, working from right to left."""
    suffixes = (
        ":free", "-instruct", "-chat", "-it", "-turbo", "-preview",
        "-snapshot", "-vision", "-hf",
        # Parameter-size suffixes (only strip if preceded by a non-digit separator)
        "-8b", "-70b", "-32b", "-7b", "-3b", "-1b", "-110b", "-90b",
        "-27b", "-14b", "-9b", "-2b", "-72b", "-405b",
    )
    result = name
    changed = True
    while changed:
        changed = False
        for suffix in suffixes:
            if result.endswith(suffix):
                result = result[: -len(suffix)]
                changed = True
                break  # restart from longest after stripping
    return result
