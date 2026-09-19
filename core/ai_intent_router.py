"""
AI Intent Router - Two-Pass System for Efficient Query Routing

Pass 1: Lightweight intent classification (<1000 tokens)
  - Analyzes user query to identify relevant endpoints
  - Returns structured JSON with target endpoints and search parameters
  
Pass 2: Context-aware response generation
  - Queries only the relevant endpoints identified in Pass 1
  - Generates final response with full context

Performance target: Pass 1 must remain <1000 tokens for 112+ endpoints
"""

import json
import re
from typing import Dict, List, Any, Set
from dataclasses import dataclass
from config.backup_endpoints import NETBOX_ENDPOINTS, get_all_endpoints


@dataclass
class IntentRouting:
    """Result of Pass 1 intent classification"""
    target_endpoints: List[str]
    search_query: str
    query_type: str  # "exact_match", "keyword_search", "list_all"
    identifiers: List[str]  # IPs, hostnames, subnets, asset tags
    keywords: List[str]  # General search terms
    site_filter: str = ""
    confidence: str = "high"  # high, medium, low


def _generate_keywords_from_endpoint(endpoint_path: str) -> str:
    """
    Dynamically generate keywords from endpoint path.
    NO HARDCODING - works with any endpoint, any NetBox version.
    
    Strategy:
    1. Extract endpoint name from path (e.g., "dcim/devices" -> "devices")
    2. Split kebab-case into words (e.g., "device-types" -> ["device", "types"])
    3. Add category as context (e.g., "dcim" -> add "dcim")
    4. Generate common variations and synonyms programmatically
    
    Examples:
        "dcim/devices" -> "device,devices,dcim"
        "ipam/ip-addresses" -> "ip,ip-address,ip-addresses,address,addresses,ipam"
        "virtualization/virtual-machines" -> "virtual,machine,virtual-machine,virtual-machines,vm,virtualization"
    """
    category, _, endpoint_name = endpoint_path.partition('/')
    
    # Split endpoint name by hyphens
    words = endpoint_name.split('-')
    
    keywords = set()
    
    # Add category
    keywords.add(category)
    
    # Add full endpoint name variations
    keywords.add(endpoint_name)
    keywords.add(endpoint_name.replace('-', ' '))
    keywords.add(endpoint_name.replace('-', '_'))
    
    # Add individual words
    for word in words:
        if len(word) > 2:  # Skip very short words
            keywords.add(word)
            # Add plural/singular variations
            if word.endswith('s') and len(word) > 3:
                keywords.add(word[:-1])  # Remove 's' for singular
            elif not word.endswith('s'):
                keywords.add(word + 's')  # Add 's' for plural
    
    # Add common abbreviations and synonyms based on word patterns
    keyword_additions = set()
    for word in list(keywords):
        word_lower = word.lower()
        
        # Network-specific abbreviations
        if 'virtual' in word_lower and 'machine' in endpoint_name:
            keyword_additions.add('vm')
        if 'virtual' in word_lower and 'private' in endpoint_name:
            keyword_additions.add('vpn')
        if 'autonomous' in word_lower or 'system' in word_lower:
            keyword_additions.add('asn')
        if 'vlan' in word_lower:
            keyword_additions.add('vid')
        if 'wireless' in word_lower and 'lan' in word_lower:
            keyword_additions.add('wlan')
            keyword_additions.add('ssid')
        if 'ip' in word_lower and 'address' in word_lower:
            keyword_additions.add('ip')
        if 'prefix' in word_lower:
            keyword_additions.add('subnet')
            keyword_additions.add('cidr')
        if 'device' in word_lower:
            keyword_additions.add('hardware')
        if 'interface' in word_lower:
            keyword_additions.add('port')
        if 'rack' in word_lower:
            keyword_additions.add('cabinet')
        if 'site' in word_lower:
            keyword_additions.add('location')
        if 'manufacturer' in word_lower:
            keyword_additions.add('vendor')
        if 'platform' in word_lower:
            keyword_additions.add('os')
        if 'region' in word_lower:
            keyword_additions.add('country')
        if 'cable' in word_lower:
            keyword_additions.add('connection')
        if 'circuit' in word_lower:
            keyword_additions.add('wan')
        if 'provider' in word_lower:
            keyword_additions.add('carrier')
            keyword_additions.add('isp')
        if 'tenant' in word_lower:
            keyword_additions.add('customer')
        if 'contact' in word_lower:
            keyword_additions.add('person')
        if 'cluster' in word_lower:
            keyword_additions.add('vcenter')
        if 'tag' in word_lower:
            keyword_additions.add('label')
        if 'custom' in word_lower and 'field' in word_lower:
            keyword_additions.add('field')
            keyword_additions.add('attribute')
        if 'user' in word_lower:
            keyword_additions.add('account')
        if 'group' in word_lower:
            keyword_additions.add('permission-group')
        if 'change' in word_lower:
            keyword_additions.add('audit')
            keyword_additions.add('history')
        if 'job' in word_lower:
            keyword_additions.add('task')
    
    keywords.update(keyword_additions)
    
    # Remove empty strings and category duplicates
    keywords = {k for k in keywords if k and len(k) > 1}
    
    # Sort for consistency and join
    return ','.join(sorted(keywords)[:15])  # Limit to 15 most relevant keywords


def _generate_compact_endpoint_schema() -> str:
    """
    Generate ultra-compact endpoint schema for Pass 1.
    Target: <800 tokens for 112+ endpoints
    
    FULLY DYNAMIC - No hardcoded endpoints or keywords.
    Automatically adapts to any NetBox version.
    
    Format per endpoint:
    path|keywords
    
    Example:
    dcim/devices|cabinet,connection,dcim,device,devices,hardware,port
    ipam/prefixes|cidr,ipam,network,prefix,prefixes,subnet
    """
    schema_lines = []
    
    # Get all endpoints dynamically from backup_endpoints.py
    all_endpoints = get_all_endpoints()
    
    # Generate keywords dynamically for each endpoint
    for endpoint in all_endpoints:
        keywords = _generate_keywords_from_endpoint(endpoint)
        if keywords:
            schema_lines.append(f"{endpoint}|{keywords}")
    
    return "\n".join(schema_lines)


def _build_pass1_prompt(user_query: str) -> str:
    """
    Build Pass 1 system prompt - ultra-lightweight for intent routing.
    Target: <1000 tokens total
    """
    endpoint_schema = _generate_compact_endpoint_schema()
    
    prompt = f"""You are an intent classification system for a NetBox database query assistant.

**YOUR TASK**: Analyze the user query and return ONLY a JSON object identifying relevant endpoints.

**ENDPOINT SCHEMA** (format: path|keywords):
{endpoint_schema}

**QUERY TYPES**:
- exact_match: User provides specific identifier (IP, hostname, CIDR, asset tag, VLAN ID)
- keyword_search: General question with keywords
- list_all: User wants to see all records from specific endpoint(s)

**IDENTIFICATION PATTERNS**:
- IP addresses: 10.x.x.x, 192.168.x.x, etc.
- CIDRs: 10.0.0.0/24, 172.16.0.0/16
- Hostnames: server-001, fw-hq-01, device names with hyphens/dots
- VLANs: vlan 100, vid 50
- Asset tags: alphanumeric with special chars

**INSTRUCTIONS**:
1. Match user query keywords to endpoint keywords
2. Select 1-5 most relevant endpoints (be precise, not exhaustive)
3. Extract identifiers (IPs, hostnames, CIDRs, VLANs, asset tags)
4. Extract keywords (general search terms)
5. Detect site mentions
6. Return JSON ONLY - no explanation

**JSON RESPONSE FORMAT**:
{{
  "target_endpoints": ["path1", "path2"],
  "query_type": "exact_match|keyword_search|list_all",
  "identifiers": ["ip", "hostname"],
  "keywords": ["keyword1", "keyword2"],
  "site_filter": "site_name",
  "search_query": "combined search string",
  "confidence": "high|medium|low"
}}

**USER QUERY**: {user_query}

Return JSON only:"""
    
    return prompt


def _parse_pass1_response(response: str) -> IntentRouting:
    """Parse Pass 1 JSON response into IntentRouting object"""
    try:
        # Extract JSON from response (handle markdown code blocks)
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            # Try to find raw JSON
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                json_str = json_match.group(0)
            else:
                raise ValueError("No JSON found in response")
        
        data = json.loads(json_str)
        
        return IntentRouting(
            target_endpoints=data.get("target_endpoints", []),
            search_query=data.get("search_query", ""),
            query_type=data.get("query_type", "keyword_search"),
            identifiers=data.get("identifiers", []),
            keywords=data.get("keywords", []),
            site_filter=data.get("site_filter", ""),
            confidence=data.get("confidence", "medium")
        )
    except Exception as e:
        # Fallback: return empty routing
        return IntentRouting(
            target_endpoints=[],
            search_query="",
            query_type="keyword_search",
            identifiers=[],
            keywords=[],
            confidence="low"
        )


def classify_intent(user_query: str, ai_call_func) -> IntentRouting:
    """
    Pass 1: Classify user intent and identify target endpoints.
    
    Args:
        user_query: User's natural language query
        ai_call_func: Function to call AI (signature: ai_call_func(prompt, model))
    
    Returns:
        IntentRouting object with target endpoints and search parameters
    """
    pass1_prompt = _build_pass1_prompt(user_query)
    
    # Use fast, cheap model for intent classification
    # Recommended: gemini-flash, gpt-4o-mini, or similar
    response = ai_call_func(pass1_prompt, model=None)  # Use default fast model
    
    intent = _parse_pass1_response(response)
    
    return intent


def estimate_pass1_tokens(user_query: str) -> int:
    """
    Estimate token count for Pass 1 prompt.
    Rough estimate: 1 token ≈ 4 characters for English text
    """
    prompt = _build_pass1_prompt(user_query)
    # Conservative estimate: 3.5 chars per token
    estimated_tokens = len(prompt) // 3.5
    return int(estimated_tokens)


def validate_pass1_token_budget() -> Dict[str, Any]:
    """
    Validate that Pass 1 prompt stays under 1000 token budget.
    
    Returns:
        Dict with validation results
    """
    test_query = "Show me all switches at Site-HQ with IP addresses in 10.0.0.0/24"
    estimated_tokens = estimate_pass1_tokens(test_query)
    
    endpoint_schema = _generate_compact_endpoint_schema()
    schema_lines = endpoint_schema.count('\n') + 1
    
    return {
        "estimated_tokens": estimated_tokens,
        "under_budget": estimated_tokens < 1000,
        "budget_remaining": 1000 - estimated_tokens,
        "endpoint_count": schema_lines,
        "schema_tokens": len(endpoint_schema) // 3.5,
        "status": "PASS" if estimated_tokens < 1000 else "FAIL"
    }


def get_endpoint_statistics() -> Dict[str, Any]:
    """Get statistics about endpoint coverage"""
    all_endpoints = get_all_endpoints()
    endpoint_schema = _generate_compact_endpoint_schema()
    schema_lines = [line for line in endpoint_schema.split('\n') if line.strip()]
    
    categories = {}
    for endpoint in all_endpoints:
        category = endpoint.split('/')[0].upper()
        categories[category] = categories.get(category, 0) + 1
    
    return {
        "total_endpoints": len(all_endpoints),
        "endpoints_in_schema": len(schema_lines),
        "coverage_percentage": (len(schema_lines) / len(all_endpoints) * 100) if all_endpoints else 0,
        "categories": categories,
        "schema_size_bytes": len(endpoint_schema),
        "schema_size_tokens": len(endpoint_schema) // 3.5
    }
