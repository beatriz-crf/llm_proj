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
    Validates the process plan using advanced features:
    - Uses helper functions for clear logical separation.
    - Provides detailed, contextual warning messages.
    - Auto-corrects parameters that exceed the machine's physical limits.
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
        # Create a copy to modify safely
        step = dict(step_data)
        warnings = []
        
        operation = step.get("operation")
        speed_rpm = step.get("spindle_speed_rpm")
        feed_rate = step.get("feed_rate_mm_min")
        tool_diam_mm = step.get("tool_diameter_mm")
        
        # --- 1. Machine limit validation and auto-correction ---
        if speed_rpm and speed_rpm > machine_constraints["max_spindle_speed_rpm"]:
            warnings.append(
                f"RPM {speed_rpm} exceeds machine max ({machine_constraints['max_spindle_speed_rpm']}). "
                f"Auto-corrected to max."
            )
            # Auto-correction
            step["spindle_speed_rpm"] = machine_constraints["max_spindle_speed_rpm"]
            speed_rpm = step["spindle_speed_rpm"] # Update local variable for subsequent checks
            
        if feed_rate and feed_rate > machine_constraints["max_feed_rate_mm_min"]:
            warnings.append(
                f"Feed rate {feed_rate} mm/min exceeds machine max ({machine_constraints['max_feed_rate_mm_min']}). "
                f"Auto-corrected to max."
            )
            # Auto-correction
            step["feed_rate_mm_min"] = machine_constraints["max_feed_rate_mm_min"]
            feed_rate = step["feed_rate_mm_min"]

        # --- 2. Tool and operation compatibility validation ---
        tool = step.get("tool_description")
        if operation in tool_constraints:
            valid_tools = tool_constraints[operation]
            if valid_tools and tool and not any(vt.lower() in tool.lower() for vt in valid_tools):
                warnings.append(f"Tool '{tool}' may be inappropriate for '{operation}'. Recommended: {', '.join(valid_tools)}.")

        # --- 3. Mechanical sanity check for material and parameters (with detailed warnings) ---
        op_category = _get_operation_category(operation)
        if op_category and material_properties and tool_diam_mm and speed_rpm and tool_diam_mm > 0:
            speed_range_m_min = material_properties.get(op_category)
            
            if speed_range_m_min:
                min_rpm, max_rpm = _calculate_rpm_range(speed_range_m_min, tool_diam_mm)
                
                if not (min_rpm <= speed_rpm <= max_rpm):
                    actual_vc = _calculate_vc_from_rpm(speed_rpm, tool_diam_mm)
                    warnings.append(
                        f"RPM {speed_rpm} (Vc≈{actual_vc:.0f} m/min) is outside the recommended range "
                        f"({int(min_rpm)}-{int(max_rpm)}) for {material} using a {tool_diam_mm}mm tool for {op_category}."
                    )
        elif speed_rpm and not material_key:
            warnings.append(f"Could not validate RPM for material '{material}'. Not found in knowledge base.")

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