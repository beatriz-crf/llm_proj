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

ALLOWED_TOOLS = [
    "Vise", "Fixture", "Soft Jaws", "Face Mill", "End Mill", "Center Drill", "Drill Bit",
    "Reamer", "Tap", "Chamfer Mill", "Spot Drill", "Deburring Tool", "None"
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
4) Prefer the fewest steps that satisfy requirements.
5) Respect sequencing:
   - If any holes: Center Drilling (optional) → Drilling → (Reaming or Tapping, only if required).
   - Reaming only for tight tolerance / surface finish on holes.
   - Tapping only if threads are specified.
   - Face Milling only if a planar face must be created/improved/square stock.
   - Roughing only if significant stock removal is required; otherwise skip it and go straight to Finishing.
   - Chamfering/Deburring only if edges/holes require edge break; otherwise omit.
   - Cleanup is optional; include only if chip/coolant removal is explicitly needed before inspection.
6) Tool parameters may be null if not inferable; do not guess wildly.
7) Do NOT confuse feature size (hole size, chamfer width, or plate thickness) with the physical cutter diameter ("tool_diameter_mm")

INCLUSION CRITERIA BY OPERATION
- "Face Milling": a face needs to be created, trued, or surfaced.
- "Roughing": large stock removal or heavy profiling.
- "Finishing": final dimension/finish after roughing or light single-pass profiling.
- "Center Drilling": only when drilling deeper/smaller holes where positioning matters.
- "Drilling": only when holes exist.
- "Reaming": only when tight hole tolerance / finish specified.
- "Tapping": only when threaded holes are specified (include thread size in notes if known).
- "Chamfering": only when chamfers/edge breaks are specified.
- "Deburring": when burrs likely or request mentions deburr/edge break.
- "Cleanup": only if required by process or customer request.

TOOL RULES
Acceptable tool diameter ranges:
   • Drills: 1–25 mm
   • Center Drills: 2–6 mm
   • Face Mills: 20–100 mm
   • End Mills: 2–20 mm
   • Chamfer Mills: 3–12 mm
   • Reamer: 3–20 mm
- Reject unrealistic ranges. If the required tool is outside these ranges, set tool_diameter_mm = null.

OUTPUT FORMAT (return ONLY this JSON object)
{{
  "plan": [
    {{
      "step": <Integer>,
      "operation": <One of {ALLOWED_OPS}>,
      "tool_description": <One of {ALLOWED_TOOLS}>,
      "spindle_speed_rpm": <Integer or null>,
      "feed_rate_mm_min": <Integer or null>,
      "tool_diameter_mm": <Float or null>,
      "notes": <String: MUST justify why this step is needed by citing a feature from the description>
    }}
    // ... next steps
  ]
}}
Ensure the plan is minimal yet complete. If a step can't be justified from the description, DO NOT include it.

EXAMPLE 
An aluminum billet with one tapped M6 through hole and one finished top face.
{{
  "plan": [
    {{"operation":"Setup","tool_description":"Vise","spindle_speed_rpm":null,"feed_rate_mm_min":null,"tool_diameter_mm":null,"notes":"Clamp square billet in vise; datum per 'top face datum A'."}},
    {{"operation":"Face Milling","tool_description":"Face Mill","spindle_speed_rpm":6000,"feed_rate_mm_min":1200,"tool_diameter_mm":50.0,"notes":"Create planar surface per 'top face 0.8 Ra' on aluminum billet."}},
    {{"operation":"Center Drilling","tool_description":"Center Drill","spindle_speed_rpm":4000,"feed_rate_mm_min":200,"tool_diameter_mm":6.0,"notes":"Pilot for 'M6 tapped through hole'."}},
    {{"operation":"Drilling","tool_description":"Drill Bit","spindle_speed_rpm":4500,"feed_rate_mm_min":450,"tool_diameter_mm":5.0,"notes":"Tap drill for 'M6 tapped through hole'."}},
    {{"operation":"Tapping","tool_description":"Tap","spindle_speed_rpm":1000,"feed_rate_mm_min":100,"tool_diameter_mm":null,"notes":"Cut M6x1 thread per 'M6 tapped through hole'."}},
    {{"operation":"Final Inspection","tool_description":"Deburring Tool","spindle_speed_rpm":null,"feed_rate_mm_min":null,"tool_diameter_mm":null,"notes":"Verify 'top face 0.8 Ra' and 'M6 thread gauge'."}}
  ]
}}

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