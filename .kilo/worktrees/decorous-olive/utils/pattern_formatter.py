"""
Pattern formatter utility to apply naming standards from the Standards tab.
This module replaces placeholders in naming patterns with actual values.
"""

def apply_pattern(pattern: str, variables: dict) -> str:
    """
    Apply a naming pattern by replacing variables with actual values.
    
    Args:
        pattern: The pattern string with variables like <Remote_Device>
        variables: Dictionary of variable names to values
        
    Returns:
        The formatted string with variables replaced
        
    Example:
        pattern = "Uplink_to_<Remote_Device>_<Remote_Port>"
        variables = {"Remote_Device": "SWUSNYC02-0", "Remote_Port": "Gi1/0/48"}
        result = "Uplink_to_SWUSNYC02-0_Gi1/0/48"
    """
    if not pattern:
        return ""
    
    result = pattern
    
    # Replace all variables in the pattern
    for key, value in variables.items():
        # Handle both <Variable> and <Variable_Name> formats
        placeholder = f"<{key}>"
        if placeholder in result:
            result = result.replace(placeholder, str(value) if value else f"<{key}>")
    
    return result
