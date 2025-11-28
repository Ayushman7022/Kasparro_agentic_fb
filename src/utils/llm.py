# src/utils/llm.py

import os
import time
from typing import Dict, Any, Optional

import google.generativeai as genai


class LLMClient:
    """
    Unified LLM wrapper for Gemini models
    - Handles API config
    - Provides safe retry logic
    - Logs prompts/responses if logger is attached
    """

    def __init__(
        self,
        provider: str = "gemini",
        model: str = "gemini-2.0-flash",
        api_key: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 1500,
    ):
        self.provider = provider
        self.model_name = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.logger = None  # Orchestrator attaches logger

        # Load API key
        api_key = (
            api_key
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GENAI_API_KEY")
        )

        if not api_key:
            raise RuntimeError("❌ Gemini API key missing. Set GEMINI_API_KEY environment variable.")

        # Configure Google SDK
        genai.configure(api_key=api_key)

        # Create model object
        self.model = genai.GenerativeModel(self.model_name)

        if self.logger:
            self.logger.info(f"LLMClient initialized with model={self.model_name}")

    # -----------------------------------------------------------
    # Core LLM call (Gemini generate_content)
    # -----------------------------------------------------------
    def generate_text(
        self,
        prompt: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:

        t = self.temperature if temperature is None else temperature
        mt = self.max_tokens if max_tokens is None else max_tokens

        # Log prompt
        if self.logger:
            self.logger.debug(f"[LLM] Prompt:\n{prompt[:1500]}")

        try:
            response = self.model.generate_content(
                contents=prompt,
                generation_config={
                    "temperature": t,
                    "max_output_tokens": mt,
                }
            )
        except Exception as e:
            if self.logger:
                self.logger.error(f"[LLM] ERROR during generate_content: {e}")
            raise e

        # Safely extract text
        text = response.text if hasattr(response, "text") else ""

        # Log response
        if self.logger:
            snippet = text[:1000].replace("\n", " ")
            self.logger.debug(f"[LLM] Response:\n{snippet}")

        # Extract usage stats
        usage = {}
        try:
            usage = {
                "input_tokens": response.usage_metadata.prompt_token_count,
                "output_tokens": response.usage_metadata.candidate_token_count,
                "total_tokens": response.usage_metadata.total_token_count,
            }
            if self.logger:
                self.logger.info(f"[LLM] Token usage: {usage}")
        except Exception:
            if self.logger:
                self.logger.warning("[LLM] Could not extract token usage metadata")

        return {
            "text": text,
            "raw": response,
            "usage": usage,
        }

    # -----------------------------------------------------------
    # Safe wrapper (Retries + backoff)
    # -----------------------------------------------------------
    def safe_generate(
        self,
        prompt: str,
        retries: int = 2,
        backoff: float = 1.0,
        **kwargs,
    ) -> Dict[str, Any]:

        last_exc = None

        for attempt in range(retries + 1):
            try:
                if self.logger:
                    self.logger.info(f"[LLM] Attempt {attempt+1}/{retries+1}")

                return self.generate_text(prompt, **kwargs)

            except Exception as e:
                last_exc = e
                if self.logger:
                    self.logger.error(f"[LLM] Attempt {attempt+1} failed: {e}")

                # exponential backoff
                time.sleep(backoff * (1 + attempt))

        # After exhausting retries, raise
        if self.logger:
            self.logger.critical(f"[LLM] FAILED after {retries+1} attempts: {last_exc}")

        raise last_exc
