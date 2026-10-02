"""
KARA EIM ETL Pipeline v2
Team 122 - Evaluating Child Protection Systems
ASU Capstone Fall 2026

This script:
1. EXTRACTS data from the EIM Field Inventory spreadsheet
2. TRANSFORMS it by cleaning values, handling missing data properly
3. LOADS it into a clean CSV file ready for Power BI dashboard

IMPORTANT: Missing data is NEVER treated as zero.
"""

import openpyxl
import csv
import re

print("Starting KARA ETL Pipeline v2...")
print("=" * 50)

INPUT_FILE = "EIM_Field_Inventory___Data_Tracking__1_.xlsx"
OUTPUT_FILE = "kara_clean_data_v2.csv"

try:
    wb = openpyxl.load_workbook(INPUT_FILE)
    print(f"Successfully loaded: {INPUT_FILE}")
except FileNotFoundError:
    print(f"ERROR: Could not find {INPUT_FILE}")
    print("Make sure the EIM spreadsheet is in the same folder as this script.")
    exit()

def extract_number(value):
    """
    Extract the main number from a value.
    Examples:
    - '7763(7615)' -> 7763.0
    - '67 (as of 7/1/24)' -> 67.0
    - '88(34%)' -> 88.0
    - 5865.0 -> 5865.0
    - None -> None
    """
    if value is None:
        return None
    
    # Already a number
    if isinstance(value, (int, float)):
        return float(value)
    
    val_str = str(value).strip()
    
    # Empty or placeholder
    if val_str in ['', 'TBD', 'N/A', 'n/a']:
        return None
    
    # Extract first number before any parenthesis or text
    match = re.match(r'^[\$]?([\d,]+\.?\d*)', val_str.replace(',', ''))
    if match:
        try:
            return float(match.group(1))
        except:
            return None
    
    return None

def get_missing_label(value):
    """
    Return proper missing data label per SIECHI protocol.
    Missing is NEVER zero.
    """
    if value is None:
        return "Not Available"
    
    val_str = str(value).strip()
    
    if val_str in ['', 'TBD']:
        return "Not Yet Reviewed"
    
    if val_str in ['N/A', 'n/a']:
        return "Not Applicable"
    
    return None

def get_comparability_status(field_name):
    if not field_name:
        return "Unknown"
    
    not_comparable = [
        "Foster Care Cost Per Child",
        "Adult Incarceration Cost",
        "Juvenile Justice Cost",
        "Recidivism Rate",
        "Healthcare Cost",
        "Housing Instability",
        "Emergency Services",
        "Policy Intervention",
        "Court and Legal Costs",
        "Foster Care Direct to Adult",
        "Ongoing Social Services",
    ]
    
    directly_comparable = [
        "GDP Contribution",
        "Title IV-E",
    ]
    
    for term in not_comparable:
        if term.lower() in field_name.lower():
            return "Not Comparable"
    
    for term in directly_comparable:
        if term.lower() in field_name.lower():
            return "Comparable"
    
    return "Partially Comparable"

# Read data
ws = wb['Master Observation Data']
cleaned_rows = []
skipped = 0

for i, row in enumerate(ws.iter_rows(values_only=True)):
    if i < 3:
        continue
    
    scope = row[0]
    field_id = row[1]
    field_name = row[2]
    year = row[3]
    raw_value = row[4]
    source_url = row[5]
    data_confidence = row[6]
    notes = row[7]
    
    if not scope or not field_id:
        skipped += 1
        continue
    
    if scope in ['Scope', 'Master Observation List']:
        continue
    
    # Get numeric value
    numeric_value = extract_number(raw_value)
    
    # Get missing label if no numeric value
    missing_label = get_missing_label(raw_value) if numeric_value is None else None
    
    # Store original value for reference
    original_value = str(raw_value).strip() if raw_value is not None else ""
    
    cleaned_rows.append({
        "Scope": scope,
        "Field_ID": field_id,
        "Field_Name": field_name,
        "Year": int(year) if isinstance(year, float) else year,
        "Numeric_Value": numeric_value if numeric_value is not None else "",
        "Missing_Label": missing_label if missing_label else "",
        "Original_Value": original_value,
        "Source_URL": source_url if source_url else "No Source Provided",
        "Data_Confidence": data_confidence if data_confidence else "Unknown",
        "Comparability_Status": get_comparability_status(str(field_name)),
        "Notes": notes if notes else "",
    })

print(f"Total rows extracted: {len(cleaned_rows)}")

# Stats
numeric_count = sum(1 for r in cleaned_rows if r["Numeric_Value"] != "")
missing_count = sum(1 for r in cleaned_rows if r["Missing_Label"] == "Not Available")
mn_rows = [r for r in cleaned_rows if r["Scope"] == "Minnesota"]
nat_rows = [r for r in cleaned_rows if r["Scope"] == "National"]

print(f"Minnesota rows: {len(mn_rows)}")
print(f"National rows: {len(nat_rows)}")
print(f"Rows with numeric values: {numeric_count}")
print(f"Rows marked Not Available: {missing_count}")

# Save CSV
fieldnames = ["Scope", "Field_ID", "Field_Name", "Year", "Numeric_Value", 
              "Missing_Label", "Original_Value", "Source_URL", 
              "Data_Confidence", "Comparability_Status", "Notes"]

with open(OUTPUT_FILE, 'w', newline='', encoding='utf-8') as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(cleaned_rows)

print(f"\nClean data saved to: {OUTPUT_FILE}")
print(f"\nIn Power BI use the Numeric_Value column for charts.")
print(f"Missing_Label column shows why a value is empty.")
print(f"\nETL PIPELINE v2 COMPLETE")
