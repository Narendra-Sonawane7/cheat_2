"""
Question Solver — Detects questions and answers them using Groq API (Free).
Supports Interview, Exam, and Meeting modes with tailored AI prompts.
Now accepts language and resume context for personalised answers.

PERF FIXES:
  - Groq client created ONCE at module level and reused (was recreated on every call)
  - solve_streaming() added — sends tokens to UI as they arrive (no more waiting for full response)
  - solve_with_claude() kept for backward-compat screen-scan path
"""

import re
import openai
from config import load_config
from typing import Callable, Optional


# ── Module-level cached Groq client ──────────────────────────────────────────
# Creating openai.OpenAI() sets up an httpx session.  Doing it on every request
# adds ~100-200 ms of connection overhead per call.  Cache it and only rebuild
# when the API key changes.

_groq_client: Optional[openai.OpenAI] = None
_groq_key_cached: str = ""


def _get_client(api_key: str) -> openai.OpenAI:
    global _groq_client, _groq_key_cached
    if _groq_client is None or api_key != _groq_key_cached:
        _groq_client = openai.OpenAI(
            api_key=api_key,
            base_url="https://api.groq.com/openai/v1",
        )
        _groq_key_cached = api_key
    return _groq_client


# ── Mode Prompts ──────────────────────────────────────────────────────────────

MODE_PROMPTS = {
    "interview": """You are an expert interview coach and co-pilot assisting someone in a live job interview.
When you receive a question:

- BEHAVIORAL questions (Tell me about yourself / Describe a time / Give an example):
  → Give a polished STAR-format answer (Situation, Task, Action, Result). Keep each part 1-2 sentences.

- CODING questions (write a function / implement / solve / reverse / find / two sum etc.):
  → Write clean, working code with language specified or Python by default.
  → Add 2-3 line explanation below the code.

- SYSTEM DESIGN questions (design a URL shortener / architect a system):
  → Give structured answer: Components → Data Flow → Database → Scaling. Use bullet points.

- TECHNICAL CONCEPT questions (what is X / explain X / difference between X and Y):
  → Give a clear, accurate, concise explanation with a real-world example.

- GENERAL / OTHER:
  → Answer professionally and confidently in 3-5 sentences.

IMPORTANT: Be concise. Interviewer is listening. Keep total answer under 150 words unless it is a coding question.""",

    "exam": """You are a precise academic tutor helping someone during an exam or test.

- MCQ / True-False: State the correct answer immediately, then explain why in 1-2 sentences.
- Calculation / Math: Show step-by-step working clearly. Label each step.
- Theory / Definition: Give a textbook-accurate explanation in 3-5 sentences.
- Fill in the blank: Give the exact answer word/phrase first, then context.

Be accurate, direct, and brief. No filler words.""",

    "meeting": """You are a smart professional meeting assistant.

- If a question is asked: Answer it clearly and briefly.
- If a topic is discussed: Summarize the key point in 2-3 sentences.
- If a decision or action is mentioned: Extract and list action items.
- If technical jargon is used: Explain it simply.

Be professional, concise, and helpful.""",
}


# ── Question Detection ────────────────────────────────────────────────────────

QUESTION_PATTERNS = [
    r'\?',
    r'\b(?:what|who|where|when|why|how|which|whom|whose)\b',
    r'\b(?:is|are|was|were|do|does|did|can|could|will|would|shall|should|may|might)\s+\w+',
    r'\b(?:define|explain|describe|calculate|find|solve|evaluate|compute|determine|list|state|prove|derive)\b',
    r'\b(?:true or false|choose the correct|select the|pick the|fill in|which of the following)\b',
    r'\b(?:tell me|walk me|talk me)\b',
    r'\b(?:give me|give an|give your|give a)\b',
    r'\b(?:describe a|describe your|describe the|describe how)\b',
    r'\b(?:tell us|tell me about|about yourself|your background|your experience|your strength|your weakness)\b',
    r'\b(?:write|implement|code|build|create|develop|program|construct)\b',
    r'\b(?:design|architect|architecture|system for|how would you build|how would you design)\b',
    r'\b(?:difference between|compare|versus|vs\.|pros and cons|advantages of|disadvantages of)\b',
    r'\b(?:have you|have you ever|have you worked|have you used)\b',
    r'\b(?:how do you|how would you|how did you|how have you)\b',
    r'\b(?:what would you|what do you think|what is your)\b',
    r'\b(?:in your opinion|your approach|your strategy|your plan)\b',
]


def is_question(text: str) -> bool:
    if not text or len(text.strip()) < 8:
        return False
    text_lower = text.lower().strip()
    return any(re.search(p, text_lower) for p in QUESTION_PATTERNS)


def extract_questions(text: str) -> list:
    if not text:
        return []
    sentences = re.split(r'(?<=[.?!])\s+', text.strip())
    questions = [s.strip() for s in sentences if s.strip() and is_question(s)]
    if not questions and is_question(text):
        questions = [text.strip()]
    seen, unique = set(), []
    for q in questions:
        if q not in seen:
            seen.add(q); unique.append(q)
    return unique


# ── Build dynamic system prompt ───────────────────────────────────────────────

def _build_system_prompt(mode: str, language: str = None, resume: str = None) -> str:
    prompt = MODE_PROMPTS.get(mode, MODE_PROMPTS["interview"])

    ROLE_CONTEXTS = {
        "AI Engineer": (
            "\n\nROLE CONTEXT: The candidate is interviewing for an AI Engineer role. "
            "Default coding language is Python. Prioritise frameworks: PyTorch, TensorFlow, "
            "Hugging Face Transformers, LangChain, FastAPI. "
            "For coding questions use Python with type hints and docstrings. "
            "For concept questions cover: LLMs, RAG, fine-tuning, embeddings, vector databases, "
            "prompt engineering, model deployment, inference optimisation, MLOps, and AI system design. "
            "For system design questions focus on: LLM APIs, retrieval pipelines, latency/cost tradeoffs, "
            "evaluation frameworks, and serving infrastructure."
        ),
        "ML Engineer": (
            "\n\nROLE CONTEXT: The candidate is interviewing for an ML Engineer role. "
            "Default coding language is Python. Prioritise frameworks: PyTorch, scikit-learn, "
            "TensorFlow/Keras, XGBoost, pandas, numpy, MLflow, Airflow. "
            "For coding questions use Python with numpy-style operations and vectorised code. "
            "For concept questions cover: supervised/unsupervised learning, feature engineering, "
            "model evaluation metrics, bias-variance tradeoff, regularisation, ensemble methods, "
            "neural network architectures, backpropagation, and hyperparameter tuning. "
            "For system design questions focus on: ML pipelines, training infrastructure, "
            "data preprocessing, model versioning, A/B testing, monitoring, and production deployment."
        ),
    }

    if language and language not in ("Auto-Detect", "", None):
        if language in ROLE_CONTEXTS:
            prompt += ROLE_CONTEXTS[language]
        else:
            prompt += (
                f"\n\nLANGUAGE CONTEXT: The candidate is interviewing for a {language} role. "
                f"For ALL coding questions, write code in {language} by default unless the question "
                f"explicitly specifies a different language. Tailor examples and idioms to {language} "
                f"best practices."
            )

    if resume and resume.strip():
        prompt += (
            f"\n\nCANDIDATE RESUME:\n{resume.strip()}\n\n"
            "RESUME INSTRUCTIONS: You have the candidate's resume above. "
            "When asked about their experience, projects, skills, achievements, or "
            "'tell me about yourself', answer in FIRST PERSON as if you ARE the candidate. "
            "Reference specific project names, technologies, companies, and accomplishments "
            "from the resume. Be concrete and specific — don't give generic answers."
        )

    return prompt


# ── Streaming solver (PRIMARY PATH — audio + manual input) ───────────────────

def solve_streaming(
    question: str,
    mode: str = "interview",
    language: str = None,
    resume: str = None,
    on_token: Callable[[str], None] = None,
    on_done: Callable[[str], None] = None,
    on_error: Callable[[str], None] = None,
) -> None:
    """
    Stream the answer token-by-token.

    Calls on_token(token_str) for each partial chunk as it arrives,
    then on_done(full_answer_str) when complete.

    This is the fast path — the overlay starts showing text within ~300 ms
    instead of waiting for the full 700-token response (~3-5 s).

    Runs synchronously; caller is responsible for running this in a thread.
    """
    config  = load_config()
    api_key = config.get("groq_api_key", "").strip()

    if not api_key:
        if on_error:
            on_error("⚠️ No Groq API key configured. Right-click the tray icon → Change API Key.")
        return

    system_prompt = _build_system_prompt(mode, language=language, resume=resume)

    try:
        client = _get_client(api_key)
        stream = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=700,
            stream=True,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": question},
            ],
        )

        collected: list[str] = []
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                collected.append(delta)
                if on_token:
                    on_token(delta)

        if on_done:
            on_done("".join(collected))

    except openai.AuthenticationError:
        if on_error: on_error("❌ Invalid Groq API key. Right-click tray → Change API Key.")
    except openai.RateLimitError:
        if on_error: on_error("⚠️ Rate limit reached. Wait a moment and try again.")
    except openai.APIConnectionError:
        if on_error: on_error("❌ No internet connection. Check your network.")
    except Exception as e:
        if on_error: on_error(f"❌ Error: {str(e)}")


# ── Non-streaming solver (kept for screen-scan path) ─────────────────────────

def solve_with_claude(
    question: str,
    mode: str = "interview",
    language: str = None,
    resume: str = None,
) -> str:
    """
    Blocking version — used by the screen-scan path (process_text).
    Uses the same cached client so no extra connection overhead.
    """
    config  = load_config()
    api_key = config.get("groq_api_key", "").strip()

    if not api_key:
        return "⚠️ No Groq API key configured. Right-click the tray icon → Change API Key."

    system_prompt = _build_system_prompt(mode, language=language, resume=resume)

    try:
        client = _get_client(api_key)
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=700,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": question},
            ],
        )
        return response.choices[0].message.content.strip()

    except openai.AuthenticationError:
        return "❌ Invalid Groq API key. Right-click tray → Change API Key."
    except openai.RateLimitError:
        return "⚠️ Rate limit reached. Wait a moment and try again."
    except openai.APIConnectionError:
        return "❌ No internet connection. Check your network."
    except Exception as e:
        return f"❌ Error: {str(e)}"


# ── Main Entry Point (screen scan) ───────────────────────────────────────────

def process_text(
    text: str,
    mode: str = "interview",
    language: str = None,
    resume: str = None,
) -> list:
    questions = extract_questions(text)
    return [
        (q, solve_with_claude(q, mode=mode, language=language, resume=resume))
        for q in questions
    ]