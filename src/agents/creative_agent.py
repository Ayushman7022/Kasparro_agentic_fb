# src/agents/creative_agent.py

import uuid
from typing import List, Dict, Any

from src.utils.llm import LLMClient
from src.utils.parse_utils import extract_json_from_text


class CreativeAgent:
    """
    Generates new creatives for campaigns using LLM.
    Ensures:
    - Deduplication
    - Fresh headline/body/CTA/rationale
    - Logging of prompt, output, parsing, retries
    """

    def __init__(self, llm_client: LLMClient,
                 prompt_path: str = "prompts/creative.md",
                 retries: int = 2):
        self.logger = None  # type: ignore

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
        """Construct prompt for creative generation."""
        if self.logger:
            self.logger.info(f"CreativeAgent: building creative prompt for {campaign}")

        prompt = self.template
        prompt += f"\n\nCAMPAIGN: {campaign}"
        prompt += f"\nREQUIRED_VARIATIONS: {n}"
        prompt += "\n\nSAMPLE_CREATIVES:\n" + str(sample_creatives)
        prompt += "\n\nRULES:\n- Return ONLY a JSON array.\n- Avoid any repetition.\n- Provide diverse creative styles."
        return prompt

    # ---------------------------------------------------------------------
    def _dedupe(self, creatives: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove duplicate creatives based on headline/body/rationale."""
        seen = set()
        unique = []

        for c in creatives:
            key = (
                c.get("headline", "").strip().lower(),
                c.get("body", "").strip().lower(),
                c.get("rationale", "").strip().lower()
            )
            if key not in seen:
                seen.add(key)
                unique.append(c)

        if self.logger:
            self.logger.info(f"CreativeAgent: deduped → {len(unique)} unique creatives")

        return unique

    # ---------------------------------------------------------------------
    def _fallback_creatives(self) -> List[Dict[str, Any]]:
        """Default fallback creatives when LLM fails."""
        if self.logger:
            self.logger.warning("CreativeAgent: using fallback creatives due to parsing failure.")

        return [
            {
                "creative_type": "Image",
                "headline": "Fresh Look, New Comfort",
                "body": "Upgrade your comfort with breathable, all-day underwear.",
                "cta": "Shop Now",
                "rationale": "Fallback due to JSON parsing failure."
            }
        ]

    # ---------------------------------------------------------------------
    def _attempt_more_variations(self, unique: List[Dict], n: int):
        """Ask LLM for missing additional creative variations."""
        missing = n - len(unique)

        if missing <= 0:
            return []

        if self.logger:
            self.logger.info(f"CreativeAgent: requesting {missing} additional unique creatives.")

        retry_prompt = (
            "Generate NEW, UNIQUE creatives that DO NOT repeat any headline/body/rationale "
            "from the following:\n\n"
            + str(unique) +
            f"\n\nReturn exactly {missing} items as a JSON array."
        )

        retry_resp = self.llm.safe_generate(retry_prompt, retries=1)
        retry_list, _ = extract_json_from_text(retry_resp.get("text", ""))

        if isinstance(retry_list, list):
            return retry_list

        if self.logger:
            self.logger.warning("CreativeAgent: retry attempt also failed. No additional creatives returned.")

        return []

    # ---------------------------------------------------------------------
    def generate_for_campaign(self, campaign: str, sample_creatives: List[Dict], n: int = 4) -> List[Dict[str, Any]]:
        """
        Generate n creatives for a specific campaign.
        Ensures:
        - Deduplication
        - Retry for missing variations
        - Unique creative_id assignment
        """

        if self.logger:
            self.logger.info(f"CreativeAgent: generating creatives for campaign={campaign}")

        prompt = self._build_prompt(campaign, sample_creatives, n)
        resp = self.llm.safe_generate(prompt, retries=self.retries)

        raw_text = resp.get("text", "")
        parsed, err = extract_json_from_text(raw_text)

        if self.logger:
            snippet = raw_text[:400].replace("\n", " ")
            self.logger.debug(f"CreativeAgent LLM raw output: {snippet}")

        if not parsed or not isinstance(parsed, list):
            if self.logger:
                self.logger.warning(f"CreativeAgent: JSON parsing failed → {err}")
            parsed = self._fallback_creatives()

        # 1) Deduplicate initial list
        unique = self._dedupe(parsed)

        # 2) If less than required, ask LLM for more variations
        if len(unique) < n:
            more = self._attempt_more_variations(unique, n)
            unique.extend(more)
            unique = self._dedupe(unique)

        # 3) Trim to n
        unique = unique[:n]

        # 4) Assign creative IDs and campaign context
        final = []
        for c in unique:
            c["campaign"] = campaign
            c["creative_id"] = f"cr_{uuid.uuid4().hex[:8]}"
            final.append(c)

        if self.logger:
            self.logger.info(f"CreativeAgent: final creatives generated → {len(final)}")

        return final
