# src/planner.py
import json
import math
from . import config  # Use relative import to access config
import re # Import the regular expression library to parse dimensions

# ==============================================================================
# 1. Core Business Logic
# ==============================================================================

def post_process_response(response_data: dict) -> list:
    """
    Extracts the 'plan' list from the dictionary already parsed by the LLM client.
    """
    if not isinstance(response_data, dict):
        print(f"Error: Expected a dictionary, but received {type(response_data)}.")
        return []
    
    return response_data.get("plan", [])
                             
def validate_plan(plan: list, part_dimensions: dict | None, material: str) -> list:
    """
    Validates and *auto-corrects* the process plan:
    - Tool/operation sanity checks
    - Feed clamped to machine max (if exceeded)
    - Spindle RPM auto-filled/clamped from Vc ranges (material/op/tool Ø), then capped to machine limit
    - Clear, contextual log notes in `validation_warnings`
    """
    validated_plan = []

    # --- Get constraints from config ---
    machine_constraints = config.MACHINE_CONSTRAINTS
    tool_constraints = config.TOOL_CONSTRAINTS
    material_constraints = config.MATERIAL_CONSTRAINTS

    # --- Infer material key ---
    material_key = _infer_material_key(material, material_constraints)
    material_properties = material_constraints.get(material_key, {})

    # === Step-Level Validation ===
    for step_data in plan:
        step = dict(step_data)  # copy
        warnings = []
        flags = []

        operation = step.get("operation")
        tool = step.get("tool_description")

        # Normalize numeric types (avoid truthiness pitfalls with 0)
        def _to_float(x):
            try:
                return float(x) if x is not None else None
            except (TypeError, ValueError):
                return None

        speed_rpm = _to_float(step.get("spindle_speed_rpm"))
        feed_rate = _to_float(step.get("feed_rate_mm_min"))
        tool_diam_mm = _to_float(step.get("tool_diameter_mm"))

        # --- 1) Tool vs operation compatibility (yours) ---
        if operation in tool_constraints:
            valid_tools = tool_constraints[operation]
            if valid_tools and tool and not any(vt.lower() in tool.lower() for vt in valid_tools):
                warnings.append(
                    f"Tool '{tool}' may be inappropriate for '{operation}'. "
                    f"Recommended: {', '.join(valid_tools)}."
                )
                flags.append("tool_op_mismatch")

        # --- 2) Spindle RPM from Vc (auto-fill/clamp), then cap to machine ---
        op_category = _get_operation_category(operation)

        if op_category and material_properties and tool_diam_mm and tool_diam_mm > 0:
            rpm_min, rpm_mid, rpm_max = _recommend_rpm(op_category, material_properties, tool_diam_mm)

            if rpm_min is not None:  # we have a constraint range
                range_str = f"{int(rpm_min)}–{int(rpm_max)}"
                machine_max = machine_constraints["max_spindle_speed_rpm"]
                original = speed_rpm

                # No-intersection: machine cannot reach the recommended minimum
                if machine_max < rpm_min:
                    step["spindle_speed_rpm"] = int(machine_max)
                    warnings.append(
                        f"Machine limit {machine_max} is below the recommended range {range_str}. "
                        f"Set RPM to machine limit {machine_max} (underspeed)."
                    )
                    flags.append("rpm_machine_below_recommended")
                else:
                    if speed_rpm is None:
                        # Fill with midpoint, then cap to machine
                        rec = rpm_mid
                        if rec > machine_max:
                            warnings.append(
                                f"RPM was null; recommended ≈{int(rpm_mid)} but capped to machine limit {machine_max}."
                            )
                            flags.append("rpm_filled_recommended_then_machine_cap")
                            rec = machine_max
                        else:
                            warnings.append(f"RPM was null; set to recommended ≈{int(rpm_mid)}.")
                            flags.append("rpm_filled_from_recommendation")
                        step["spindle_speed_rpm"] = int(rec)

                    else:
                        corrected = speed_rpm
                        # Clamp to recommended range first
                        if speed_rpm < rpm_min:
                            corrected = rpm_min
                            warnings.append(
                                f"RPM {int(speed_rpm)} below the recommended range {range_str}. "
                                f"Clamped to recommended minimum {int(rpm_min)}."
                            )
                            flags.append("rpm_below_recommended_clamped")
                        elif speed_rpm > rpm_max:
                            corrected = rpm_max
                            warnings.append(
                                f"RPM {int(speed_rpm)} above the recommended range {range_str}. "
                                f"Clamped to recommended maximum {int(rpm_max)}."
                            )
                            flags.append("rpm_above_recommended_clamped")

                        # Cap to machine limit if still too high
                        if corrected > machine_max:
                            warnings.append(
                                f"RPM {int(corrected)} exceeds the machine limit {machine_max}. Capped to {machine_max}."
                            )
                            flags.append("rpm_capped_to_machine")
                            corrected = machine_max

                        # Store if changed
                        if original != corrected:
                            step["spindle_speed_rpm"] = int(corrected)

        elif speed_rpm is not None and not material_key:
            warnings.append(
                f"Material '{material}' unknown; skipped Vc-based RPM validation."
            )
            flags.append("material_unknown_skip_rpm")
        elif speed_rpm is None and op_category:
            warnings.append(
                "RPM is null and Vc-based recommendation unavailable (missing material or tool diameter)."
            )
            flags.append("rpm_null_no_basis")


        # --- 3) Feed: cap to machine max (keep your original behavior) ---
        if feed_rate is not None and feed_rate > machine_constraints["max_feed_rate_mm_min"]:
            warnings.append(
                f"Feed rate {int(feed_rate)} mm/min exceeds machine max "
                f"({machine_constraints['max_feed_rate_mm_min']}). Auto-corrected to max."
            )
            flags.append("feed_capped_to_machine")
            step["feed_rate_mm_min"] = machine_constraints["max_feed_rate_mm_min"]

        # --- 4) Optional: basic non-negative checks ---
        if speed_rpm is not None and speed_rpm < 0:
            warnings.append("Negative RPM replaced with 0.")
            flags.append("rpm_negative_to_zero")
            step["spindle_speed_rpm"] = 0
        if feed_rate is not None and feed_rate < 0:
            warnings.append("Negative feed rate replaced with 0.")
            flags.append("feed_negative_to_zero")
            step["feed_rate_mm_min"] = 0

        step["validation_flags"] = flags
        step["validation_warnings"] = "; ".join(warnings) if warnings else "OK"
        validated_plan.append(step)

    return validated_plan

# ==============================================================================
# 2. Helper Functions
# ==============================================================================

def _get_operation_category(operation_name: str) -> str | None:
    """Maps a detailed operation name to a general category used in material constraints."""
    if not operation_name: return None
    op_lower = operation_name.lower()
    
    if any(kw in op_lower for kw in ["milling", "face", "facing", "roughing", "finishing", "contouring", "pocketing", "chamfering"]):
        return "milling"
    if "drilling" in op_lower: return "drilling"
    if "reaming" in op_lower: return "reaming"
    if "tapping" in op_lower: return "tapping"
    return None

def _recommend_rpm(op_category: str, material_props: dict, d_mm: float) -> tuple[float | None, float | None, float | None]:
    """
    Compute recommended RPM bounds (min, mid, max) from cutting-speed (Vc) ranges
    and tool diameter. Returns (None, None, None) if not available.

    RPM = (Vc[m/min] * 1000) / (π * D[mm])
    """
    vc_range = material_props.get(op_category)
    if not vc_range or d_mm <= 0:
        return None, None, None

    vc_min, vc_max = vc_range
    rpm_min = (vc_min * 1000.0) / (math.pi * d_mm)
    rpm_max = (vc_max * 1000.0) / (math.pi * d_mm)
    rpm_mid = 0.5 * (rpm_min + rpm_max)
    return rpm_min, rpm_mid, rpm_max

def _infer_material_key(material_text: str, material_db: dict) -> str | None:
    """Infers the standard knowledge base key from the user-provided material text."""
    if not material_text: return None
    normalized_text = material_text.lower()
    
    # Prioritize longer, more specific matches
    found_keys = [key for key in material_db if key in normalized_text]
    if not found_keys: return None
    
    return max(found_keys, key=len)

def _calculate_rpm_range(vc_range_m_min: tuple, d_mm: float) -> tuple[int, int]:
    """Calculates the recommended RPM range based on cutting speed and tool diameter."""
    min_vc, max_vc = vc_range_m_min
    # Formula: RPM = (Cutting_Speed_m/min * 1000) / (π * Tool_Diameter_mm)
    min_rpm = (min_vc * 1000) / (math.pi * d_mm)
    max_rpm = (max_vc * 1000) / (math.pi * d_mm)
    return int(min_rpm), int(max_rpm)

def _calculate_vc_from_rpm(rpm: float, d_mm: float) -> float:
    """Calculates the actual cutting speed (Vc) from RPM and tool diameter."""
    # Formula: Vc (m/min) = (π * D_mm * rpm) / 1000
    return (math.pi * d_mm * rpm) / 1000.0

def extract_dimensions_from_text(text: str) -> dict | None:
    """
    Tries to parse L, W, H dimensions from the user's description text.
    It looks for patterns like 'L=100', 'W=50mm', 'H=15.5'.
    Returns a dictionary {'L', 'W', 'H'} or None if not found.
    """
    dims = {}
    # Use regular expressions to find numbers following 'L', 'W', 'H'
    # \s* matches any whitespace, (\d+\.?\d*) captures integers or decimals
    l_match = re.search(r'L\s*=\s*(\d+\.?\d*)', text, re.IGNORECASE)
    w_match = re.search(r'W\s*=\s*(\d+\.?\d*)', text, re.IGNORECASE)
    h_match = re.search(r'H\s*=\s*(\d+\.?\d*)', text, re.IGNORECASE)
    
    if l_match: dims['L'] = float(l_match.group(1))
    if w_match: dims['W'] = float(w_match.group(1))
    if h_match: dims['H'] = float(h_match.group(1))
        
    # Returns the dictionary only if at least one dimension is found
    return dims if dims else None