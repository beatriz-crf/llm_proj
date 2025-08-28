# src/llm_client.py
from __future__ import annotations
import json
from openai import OpenAI
from src.config import API_KEY

client = OpenAI(api_key=API_KEY)

ALLOWED_OPS = [
    "Setup","Face Milling","Roughing","Finishing","Center Drilling",
    "Drilling","Reaming","Tapping","Chamfering","Deburring","Cleanup","Final Inspection"
]

def get_process_plan_from_llm(description: str):
    """
    Returns a dict like {"plan": [...]} or None on failure.
    """
    prompt = f"""
You are an expert CNC process planner. Produce only JSON.

PART DESCRIPTION
---
{description}
---

GOAL
Create a minimal, correct machining plan.

HARD RULES
1) Always include step 1 = "Setup" and the last step = "Final Inspection".
2) Do NOT include an operation unless its need is explicitly supported by the part description.
3) No duplicate operations unless each occurrence is for a clearly different feature and the notes explain it.
4) Max total steps: 3–10 (inclusive). Prefer the fewest steps that satisfy requirements.
5) Respect sequencing:
   - If any holes: Center Drilling (optional) → Drilling → (Reaming or Tapping, only if required).
   - Reaming only for tight tolerance / surface finish on holes.
   - Tapping only if threads are specified.
   - Face Milling only if a planar face must be created/improved/square stock.
   - Roughing only if significant stock removal is required; otherwise skip it and go straight to Finishing.
   - Chamfering/Deburring only if edges/holes require edge break; otherwise omit.
   - Cleanup is optional; include only if chip/coolant removal is explicitly needed before inspection.
6) Tool parameters may be null if not inferable; do not guess wildly.

INCLUSION CRITERIA BY OPERATION
- "Face Milling": a face needs to be created, trued, or surfaced.
- "Roughing": large stock removal or heavy profiling.
- "Finishing": final dimension/finish after roughing or light single-pass profiling.
- "Center Drilling": only when drilling deeper/smaller holes where positioning matters.
- "Drilling": only when holes exist.
- "Reaming": only when tight hole tolerance / finish specified.
- "Tapping": only when threaded holes are specified (include thread size in notes if known).
- "Chamfering": only when chamfers/edge breaks are specified.
- "Deburring": only when burrs likely or request mentions deburr/edge break.
- "Cleanup": only if required by process or customer request.

OUTPUT FORMAT (return ONLY this JSON object)
{{
  "plan": [
    {{
      "step": <Integer>,
      "operation": <One of {ALLOWED_OPS}>,
      "tool_description": <String>,
      "spindle_speed_rpm": <Integer or null>,
      "feed_rate_mm_min": <Integer or null>,
      "tool_diameter_mm": <Float or null>,
      "notes": <String: MUST justify why this step is needed by citing a feature from the description>
    }}
    // ... next steps
  ]
}}
Ensure the plan is minimal yet complete. If a step can't be justified from the description, DO NOT include it.
"""

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini-2024-07-18",
            messages=[
                {"role": "system", "content": "You output only valid JSON for CNC process plans."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        content = resp.choices[0].message.content or ""
        data = json.loads(content)

        # Light post-validate to curb stray steps
        plan = data.get("plan", [])
        if not plan or plan[0]["operation"] != "Setup" or plan[-1]["operation"] != "Final Inspection":
            return None
        # enforce allowed ops and dedupe consecutive duplicates
        cleaned = []
        seen = set()
        for i, step in enumerate(plan, start=1):
            op = step.get("operation")
            if op not in ALLOWED_OPS:
                continue
            if cleaned and cleaned[-1]["operation"] == op and not step.get("notes"):
                continue
            step["step"] = len(cleaned) + 1
            cleaned.append(step)
        data["plan"] = cleaned[:10]  # cap steps
        return data
    except Exception as e:
        print("LLM call failed:", e)
        return None
