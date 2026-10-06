CORE_SHEETS = {
    "Master Data Sources": {
        "header_row": 4,
        "required_columns": ["Source ID", "Source Name", "Data Type", "Data Structure"],
        "required_values": ["Source ID", "Source Name"],
        "key_columns": ["Source ID"],
    },
    "Source Assets": {
        "header_row": 4,
        "required_columns": ["Report Asset ID", "Source ID", "Report Asset URL", "File Format", "Asset Status"],
        "required_values": ["Report Asset ID", "Source ID", "Report Asset URL"],
        "key_columns": ["Report Asset ID"],
    },
    "Master Observation Data": {
        "header_row": 4,
        "required_columns": ["Scope", "Field ID", "Field Name", "Year", "Value", "Data Confidence"],
        "required_values": ["Scope", "Field ID", "Field Name", "Year"],
        "key_columns": ["Scope", "Field ID", "Year"],
    },
    "Metric Comparability": {
        "header_row": 4,
        "required_columns": ["Field ID", "Field Name", "Scope", "Availability Status", "Comparability Status"],
        "required_values": ["Field ID", "Field Name", "Scope"],
        "key_columns": ["Field ID", "Scope"],
    },
    "Rubric Variable Map": {
        "header_row": 4,
        "required_columns": ["Variable ID", "Rubric Layer", "Candidate Variable", "Working Definition", "EIM Coverage"],
        "required_values": ["Variable ID", "Rubric Layer", "Candidate Variable", "Working Definition"],
        "key_columns": ["Variable ID"],
    },
}


OPTIONAL_SHEETS = {
    "Minnesota Policy Tracking": {
        "header_row": 4,
        "required_columns": ["Policy ID", "Policy/Law Name", "Geographic Scope", "Current Status"],
        "required_values": ["Policy ID", "Policy/Law Name", "Geographic Scope"],
        "key_columns": ["Policy ID"],
    },
    "National Policy Tracking": {
        "header_row": 4,
        "required_columns": ["Policy ID", "Policy/Law Name", "Geographic Scope", "Current Status"],
        "required_values": ["Policy ID", "Policy/Law Name", "Geographic Scope"],
        "key_columns": ["Policy ID"],
    },
    "Observation Data Schema": {
        "header_row": 4,
        "required_columns": ["Metric_ID", "Baseline Field", "Metric Name", "Unit"],
        "required_values": ["Metric_ID", "Baseline Field", "Metric Name"],
        "key_columns": ["Metric_ID"],
    },
    "Policy Metrics Schema": {
        "header_row": 4,
        "required_columns": ["Policy_ID", "Category", "Policy Name", "Unit"],
        "required_values": ["Policy_ID", "Category", "Policy Name"],
        "key_columns": ["Policy_ID"],
    },
}


SUPPORTED_SHEETS = {**CORE_SHEETS, **OPTIONAL_SHEETS}


PICKLIST_COLUMNS = {
    "Data Type": "Data Type",
    "Data Structure": "Data Structure",
    "Source Level": "Source Level",
    "Publish Cadence": "Publish Cadence",
    "File Format": "File Format",
    "Asset Status": "Asset Status",
    "Evidence Level": "Evidence Level",
    "Rubric Layer": "Rubric Layer",
    "Scope": "Scope",
    "Data Confidence": "Data Confidence",
    "Policy Category": "Policy Category",
    "Current Status": "Current Status",
    "Impact Confidence": "Impact Confidence",
    "Availability Status": "Availability Status",
    "Comparability Status": "Comparability Status",
    "Evidence Confidence": "Evidence Confidence",
    "Definition Change Flag": "Definition Change Flag",
    "EIM Coverage": "EIM Coverage",
}


CLUSTER_COLUMNS = [
    "Data Type",
    "Data Structure",
    "Source Level",
    "Scope",
    "Comparability Status",
    "Rubric Layer",
    "Variable Group",
    "Policy Category",
]


ID_PATTERNS = {
    "Source ID": r"^DS-\d{3}$",
    "Report Asset ID": r"^RA-\d{3}$",
    "Field ID": r"^BF-\d{3}$",
    "Baseline Field": r"^BF-\d{3}$",
    "Variable ID": r"^RV-\d{3}$",
}
