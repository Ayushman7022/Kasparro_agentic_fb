# src/agents/creative_agent.py

import uuid
from typing import List, Dict, Any

from src.utils.llm import LLMClient
from src.utils.parse_utils import extract_json_from_text


class CreativeAgent:
    """
    Generates creatives using an LLM with:
    - deduplication,
    - retry for missing variations,
    - strong logging,
    - JSON-safe outputs.
    """

    def __init__(self, llm_client: LLMClient,
                 prompt_path: str = "prompts/creative.md",
                 retries: int = 2):
        self.llm = llm_client
        self.logger = getattr(llm_client, "logger", None)

        self.prompt_path = prompt_path
        self.retries = retries

        with open(prompt_path, "r", encoding="utf-8") as f:
            self.template = f.read()

        if self.logger:
            self.logger.info("CreativeAgent initialized.")

    # ---------------------------------------------------------------------
    def _build_prompt(self, campaign: str, sample_creatives: List[Dict], n: int) -> str:
        """Construct LLM prompt."""
        prompt = self.template
        prompt += f"\n\nCAMPAIGN: {campaign}"
        prompt += f"\nREQUIRED_VARIATIONS: {n}"
        prompt += "\n\nSAMPLE_CREATIVES:\n" + str(sample_creatives)
        prompt += (
            "\n\nRULES:\n"
            "- Return ONLY a JSON array.\n"
            "- NO repetition of headlines/bodies/rationales.\n"
            "- Provide diverse creative styles.\n"
        )

        if self.logger:
            self.logger.debug(f"Creative prompt built for campaign={campaign}")

        return prompt

    # ---------------------------------------------------------------------
    def _dedupe(self, creatives: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove deduplicates based on headline/body/rationale."""
        seen = set()
        unique = []

        for c in creatives:
            key = (
                (c.get("headline") or "").strip().lower(),
                (c.get("body") or "").strip().lower(),
                (c.get("rationale") or "").strip().lower(),
            )
            if key not in seen:
                seen.add(key)
                unique.append(c)

        if self.logger:
            self.logger.info(f"CreativeAgent: dedupe → {len(unique)} uniques")

        return unique

    # ---------------------------------------------------------------------
    def _fallback_creatives(self) -> List[Dict[str, Any]]:
        """Fallback creatives if LLM output fails."""
        if self.logger:
            self.logger.warning("CreativeAgent: Using fallback creatives")

        return [
            {
                "creative_type": "Image",
                "headline": "Fresh Look, New Comfort",
                "body": "Upgrade your comfort with breathable, all-day underwear.",
                "cta": "Shop Now",
                "rationale": "Fallback due to parsing failure."
            }
        ]

    # ---------------------------------------------------------------------
    def _attempt_more_variations(self, existing: List[Dict], needed: int) -> List[Dict]:
        """Ask LLM to generate exactly `needed` extra creatives."""
        if needed <= 0:
            return []

        if self.logger:
            self.logger.info(f"CreativeAgent: requesting {needed} more creatives")

        retry_prompt = (
            "Generate NEW, UNIQUE creatives that DO NOT repeat any headline/body/rationale "
            "from the following:\n\n"
            f"{existing}\n\n"
            f"Return exactly {needed} items in a JSON array."
        )

        resp = self.llm.safe_generate(retry_prompt, retries=1)
        parsed, _ = extract_json_from_text(resp.get("text", ""))

        if isinstance(parsed, list):
            return parsed

        if self.logger:
            self.logger.warning("CreativeAgent: retry also failed → returning empty list")

        return []

    # ---------------------------------------------------------------------
    def generate_for_campaign(self, campaign: str, sample_creatives: List[Dict], n: int = 4) -> List[Dict[str, Any]]:
        """Main creative generation pipeline."""

        if self.logger:
            self.logger.info(f"CreativeAgent: Generating creatives for {campaign}")

        # 1) Build prompt and call LLM
        prompt = self._build_prompt(campaign, sample_creatives, n)
        resp = self.llm.safe_generate(prompt, retries=self.retries)

        raw_text = resp.get("text", "")
        parsed, err = extract_json_from_text(raw_text)

        if self.logger:
            snippet = raw_text[:300].replace("\n", " ")
            self.logger.debug(f"LLM output snippet: {snippet}")

        # 2) Fallback if parsing fails
        if not parsed or not isinstance(parsed, list):
            if self.logger:
                self.logger.warning(f"CreativeAgent parsing failed → {err}")
            parsed = self._fallback_creatives()

        # 3) Initial dedupe
        unique = self._dedupe(parsed)

        # 4) Need more?
        if len(unique) < n:
            missing = n - len(unique)
            more = self._attempt_more_variations(unique, missing)
            unique.extend(more)
            unique = self._dedupe(unique)

        # 5) Trim to exactly n
        unique = unique[:n]

        # 6) Assign creative IDs + campaign
        final = []
        for c in unique:
            c["creative_id"] = f"cr_{uuid.uuid4().hex[:8]}"
            c["campaign"] = campaign
            final.append(c)

        if self.logger:
            self.logger.info(f"CreativeAgent: Final creatives count = {len(final)}")

        return final
