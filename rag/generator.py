"""Generator — local GGUF LLM answer generation via llama-cpp-python.

Fully offline. Looks for a .gguf model in the project's models/ directory.
If none is found (or llama-cpp-python isn't installed), llm_available() is
False and the app returns retrieval results without generating an answer.
"""
import glob
import os

from . import MODELS_DIR

SYSTEM_PROMPT = (
    "You are answering questions about a user's personal chat history with "
    "AI assistants (ChatGPT, DeepSeek, Gemini). Use ONLY the provided context "
    "chunks to answer. If the context doesn't contain enough information, say "
    "so rather than guessing. Always cite which conversation(s) and dates you "
    "are referencing."
)

PROMPT_TEMPLATE = (
    "{system}\n\n"
    "Context chunks:\n"
    "{context}\n\n"
    "User question: {question}\n\n"
    "Answer (cite conversation titles and dates):"
)


def find_model():
    """Return the path of the first .gguf model in models/, or None."""
    if not os.path.isdir(MODELS_DIR):
        return None
    matches = sorted(glob.glob(os.path.join(MODELS_DIR, "*.gguf")))
    return matches[0] if matches else None


def _get_llm():
    """Load and cache a Llama instance for the found model."""
    path = find_model()
    if path is None:
        return None
    if not hasattr(_get_llm, "_llm") or _get_llm._path != path:
        from llama_cpp import Llama
        _get_llm._llm = Llama(
            model_path=path,
            n_ctx=4096,
            n_threads=os.cpu_count() or 4,
            verbose=False,
        )
        _get_llm._path = path
    return _get_llm._llm


def _format_context(sources):
    """Render source chunks into a compact, labeled context block."""
    lines = []
    for i, c in enumerate(sources, 1):
        meta = f"[{i}] {c['service']} — '{c['conversation_title']}'"
        if c.get("role"):
            meta += f" ({c['role']})"
        lines.append(f"{meta}:\n{c['text']}")
    return "\n\n".join(lines)


def generate(question, sources):
    """Generate an answer from the retrieved sources via the local LLM."""
    llm = _get_llm()
    if llm is None:
        return None

    prompt = PROMPT_TEMPLATE.format(
        system=SYSTEM_PROMPT,
        context=_format_context(sources),
        question=question,
    )

    out = llm(
        prompt,
        max_tokens=512,
        temperature=0.2,
        stop=["User question:"],
        echo=False,
    )
    return (out.get("choices") or [{}])[0].get("text", "").strip()
