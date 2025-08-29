# src/config.py
import os
from dotenv import load_dotenv

# Load environment variables from the .env file
load_dotenv()

# Get the API key from the environment variables
API_KEY = os.getenv("OPENAI_API_KEY")

# Check if the API key was found and raise an error if not
if not API_KEY:
    raise ValueError("OpenAI API key not found. Please set OPENAI_API_KEY in your .env file.")

# Define our machine and material constraints
MACHINE_CONSTRAINTS = {
   
    "max_spindle_speed_rpm": 8100,          
    "max_feed_rate_mm_min": 15000,            
}
    
TOOL_CONSTRAINTS ={

        "Setup": ["Vise", "Fixture", "Soft Jaws"],
        "Face Milling": ["Face Mill", "End Mill"],
        "Roughing": ["End Mill"],
        "Finishing": ["End Mill"],
        "Center Drilling": ["Center Drill"],
        "Drilling": ["Drill Bit"],
        "Reaming": ["Reamer"],
        "Tapping": ["Tap"],
        "Chamfering": ["Chamfer Mill", "Spot Drill"],
        "Deburring": ["Deburring Tool", "End Mill"],
        "Cleanup": [],
        "Final Inspection": []
}    

# Per-tool acceptable physical diameter ranges (mm); used for deterministic validation.
TOOL_DIAMETER_LIMITS = {
    "Drill Bit":    (1.0, 25.0),
    "Center Drill": (1.0, 6.0),
    "Face Mill":    (20.0, 100.0),
    "End Mill":     (2.0, 20.0),
    "Chamfer Mill": (3.0, 12.0),
    "Reamer":       (3.0, 20.0),
}

# Per-material, per-operation cutting speed ranges (m/min).
# Ranges are conservative "ballpark" for CARBIDE in CNC milling.
MATERIAL_CONSTRAINTS = {

    "aluminum": {
        "milling":   (150, 500),   # face/rough/finish
        "drilling":  (80, 200),
        "reaming":   (60, 150),
        "tapping":   (20, 60),
    },
    "steel": {
        "milling":   (80, 150),
        "drilling":  (25, 40),
        "reaming":   (20, 40),
        "tapping":   (10, 20),
    },
    "stainless": {
        "milling":   (60, 120),
        "drilling":  (15, 25),
        "reaming":   (10, 25),
        "tapping":   (5, 15),
    },
    "cast_iron": {
        "milling":   (60, 120),
        "drilling":  (20, 35),
        "reaming":   (15, 30),
        "tapping":   (8, 20),
    },
    "titanium": {
        "milling":   (30, 60),
        "drilling":  (10, 20),
        "reaming":   (8, 15),
        "tapping":   (4, 10),
    },
    "brass": {
        "milling":   (200, 400),
        "drilling":  (60, 150),
        "reaming":   (50, 120),
        "tapping":   (15, 40),
    },
    "plastics": {
        "milling":   (300, 800),
        "drilling":  (100, 250),
        "reaming":   (80, 200),
        "tapping":   (20, 50),
    }
}