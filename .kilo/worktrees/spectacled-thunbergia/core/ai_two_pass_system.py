"""
Two-Pass AI Assistant System

Pass 1: Intent classification and endpoint routing (<1000 tokens)
Pass 2: Targeted data retrieval and response generation

This replaces the old approach of loading all context upfront with a dynamic
routing system that only queries relevant endpoints.
"""

import time
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict

from core.ai_intent_router import classify_intent, IntentRouting, estimate_pass1_tokens
from core.backup_manager import (
    search_backup_records,
    get_backup_records_by_type,
    count_backup_records,
    get_backup_site_names,
    get_choice_values_for_field,
    is_backup_active,
)
from core.ai_relationship_inference import (
    build_relationship_context,
    infer_vms_from_host,
    infer_cluster_from_identifier,
)


@dataclass
class TwoPassMetrics:
    """Performance metrics for two-pass system"""
    pass1_duration_ms: float
    pass1_tokens_estimated: int
    pass2_duration_ms: float
    pass2_tokens_estimated: int
    total_duration_ms: float
    endpoints_queried: int
    records_retrieved: int
    intent_confidence: str


def _build_targeted_context(intent: IntentRouting, user_query: str = "", max_rows: int = 60) -> str:
    """
    Build context from ONLY the endpoints identified in Pass 1.
    
    This is the key optimization: instead of querying all endpoints,
    we only query what the user actually asked about.
    
    NEW: Includes relationship inference for cluster-host-VM queries.
    """
    if not is_backup_active():
        return "No NetBox backup data available."
    
    context_parts = []
    context_parts.append("=== NETBOX DATA (Targeted Query Results) ===")
    
    total_records = 0
    results = []  # Store results for relationship inference
    
    # Convert endpoint paths to object_type format
    # "dcim/devices" -> "dcim_devices"
    target_object_types = [
        ep.replace('/', '_').replace('-', '_')
        for ep in intent.target_endpoints
    ]
    
    # 1. Exact identifier matching (highest priority)
    if intent.identifiers and intent.query_type == "exact_match":
        context_parts.append(f"\n--- Exact Match Results ---")
        context_parts.append(f"Searching for: {', '.join(intent.identifiers)}")
        
        results = search_backup_records(
            identifiers=intent.identifiers,
            keywords=intent.keywords,
            site=intent.site_filter or None,
            limit=max_rows
        )
        
        if results:
            for row in results:
                obj_type = row.get('object_type', 'unknown')
                name = row.get('name', 'N/A')
                site = row.get('site', '-')
                
                # Format based on object type
                if 'device' in obj_type:
                    cluster = row.get('cluster', '')
                    details = f"Site: {site}, Role: {row.get('device_role', '-')}, IP: {row.get('primary_ip', '-')}"
                    if cluster:
                        details += f", Cluster: {cluster}"
                elif 'ip_address' in obj_type:
                    details = f"Address: {row.get('address', '-')}, Interface: {row.get('assigned_object', '-')}"
                elif 'prefix' in obj_type:
                    details = f"CIDR: {row.get('prefix', '-')}, Site: {site}, Role: {row.get('role', '-')}"
                elif 'vlan' in obj_type:
                    details = f"VID: {row.get('vid', '-')}, Site: {site}, Group: {row.get('group', '-')}"
                else:
                    details = str(row)[:150]
                
                context_parts.append(f"  [{obj_type}] {name}: {details}")
                total_records += 1
        else:
            context_parts.append("  No exact matches found.")
    
    # 2. Endpoint-specific queries
    for object_type in target_object_types:
        if total_records >= max_rows:
            break
        
        # Get count first
        count = count_backup_records(
            object_type=object_type,
            site=intent.site_filter or None
        )
        
        if count == 0:
            continue
        
        context_parts.append(f"\n--- {object_type.replace('_', ' ').title()} ({count} records) ---")
        
        # Keyword search within this endpoint
        if intent.keywords:
            endpoint_results = search_backup_records(
                identifiers=[],
                keywords=intent.keywords,
                site=intent.site_filter or None,
                limit=min(20, max_rows - total_records)
            )
            
            # Filter to only this object type
            filtered_results = [r for r in endpoint_results if r.get('object_type') == object_type]
            
            if filtered_results:
                results.extend(filtered_results)  # Store for relationship inference
                context_parts.append(f"Keyword matches ({len(filtered_results)}):")
                for row in filtered_results[:10]:
                    name = row.get('name', 'N/A')
                    site = row.get('site', '-')
                    cluster = row.get('cluster', '')
                    if cluster:
                        context_parts.append(f"  - {name} (Site: {site}, Cluster: {cluster})")
                    else:
                        context_parts.append(f"  - {name} (Site: {site})")
                    total_records += 1
        else:
            # List all (up to limit)
            endpoint_results = get_backup_records_by_type(
                object_type=object_type,
                site=intent.site_filter or None,
                limit=min(20, max_rows - total_records)
            )
            
            if endpoint_results:
                results.extend(endpoint_results)  # Store for relationship inference
                context_parts.append(f"Sample records ({len(endpoint_results)}):")
                for row in endpoint_results:
                    name = row.get('name', 'N/A')
                    site = row.get('site', '-')
                    cluster = row.get('cluster', '')
                    if cluster:
                        context_parts.append(f"  - {name} (Site: {site}, Cluster: {cluster})")
                    else:
                        context_parts.append(f"  - {name} (Site: {site})")
                    total_records += 1
    
    # 3. Relationship inference (IMPORTANT: handles cluster-host-VM relationships)
    relationship_context = build_relationship_context(
        query=user_query,
        identifiers=intent.identifiers,
        keywords=intent.keywords,
        primary_results=results if intent.identifiers else []
    )
    
    if relationship_context:
        context_parts.append(relationship_context)
    
    # 4. Custom field choice sets (if relevant)
    if 'custom_field' in intent.search_query.lower() or 'choice' in intent.search_query.lower():
        choice_keywords = ['instance_type', 'resource_group', 'organization', 'owner', 'tier']
        for keyword in choice_keywords:
            if keyword in intent.search_query.lower():
                values = get_choice_values_for_field(keyword)
                if values:
                    context_parts.append(f"\n--- Custom Field: {keyword} ({len(values)} values) ---")
                    context_parts.append(", ".join(values[:50]))
    
    # 5. Site context
    if intent.site_filter:
        context_parts.append(f"\n--- Site Filter: {intent.site_filter} ---")
    else:
        sites = get_backup_site_names()
        if sites and len(sites) <= 20:
            context_parts.append(f"\n--- Available Sites ({len(sites)}) ---")
            context_parts.append(", ".join(sites))
    
    context_parts.append(f"\n--- Query Summary ---")
    context_parts.append(f"Endpoints queried: {len(target_object_types)}")
    context_parts.append(f"Records retrieved: {total_records}")
    context_parts.append(f"Query type: {intent.query_type}")
    
    return "\n".join(context_parts)


def _build_pass2_prompt(user_query: str, context: str, intent: IntentRouting) -> str:
    """
    Build Pass 2 system prompt with targeted context.
    """
    system_prompt = f"""You are a NetBox database assistant. Answer the user's question using the provided data.

**USER QUERY**: {user_query}

**DETECTED INTENT**:
- Target endpoints: {', '.join(intent.target_endpoints)}
- Query type: {intent.query_type}
- Identifiers: {', '.join(intent.identifiers) if intent.identifiers else 'None'}
- Keywords: {', '.join(intent.keywords) if intent.keywords else 'None'}
- Site filter: {intent.site_filter or 'None'}

**DATA CONTEXT**:
{context}

**INSTRUCTIONS**:
1. Answer the question directly and concisely
2. Use the data provided in the context
3. **IMPORTANT - Relationship Inference**: 
   - If user asks for VMs in a host, and the host is part of a cluster, return VMs from that cluster
   - VMs are assigned to clusters in VMware/vSphere, so VMs in a cluster are accessible via any host in that cluster
   - If relationship inference section is provided, use that information to answer the query
4. If data is missing, state what information is not available
5. Format lists and tables clearly
6. Include relevant details (IPs, sites, roles, etc.)
7. If no results found, suggest alternative queries

Provide a clear, professional response:"""
    
    return system_prompt


def query_with_two_pass(
    user_query: str,
    ai_call_func,
    max_rows: int = 60,
    enable_metrics: bool = True
) -> tuple[str, Optional[TwoPassMetrics]]:
    """
    Execute two-pass query system.
    
    Args:
        user_query: User's natural language question
        ai_call_func: AI call function with signature (prompt, model) -> response
        max_rows: Maximum records to retrieve in Pass 2
        enable_metrics: Whether to collect performance metrics
    
    Returns:
        Tuple of (final_response, metrics)
    """
    start_time = time.time()
    
    # Pass 1: Intent classification
    pass1_start = time.time()
    intent = classify_intent(user_query, ai_call_func)
    pass1_duration = (time.time() - pass1_start) * 1000
    
    # Estimate Pass 1 tokens
    pass1_tokens = estimate_pass1_tokens(user_query)
    
    # Pass 2: Targeted context retrieval and response
    pass2_start = time.time()
    
    # Build targeted context
    context = _build_targeted_context(intent, user_query, max_rows)
    
    # Build Pass 2 prompt
    pass2_prompt = _build_pass2_prompt(user_query, context, intent)
    
    # Estimate Pass 2 tokens (rough)
    pass2_tokens = len(pass2_prompt) // 3.5
    
    # Call AI for final response
    final_response = ai_call_func(pass2_prompt, model=None)
    
    pass2_duration = (time.time() - pass2_start) * 1000
    total_duration = (time.time() - start_time) * 1000
    
    # Count records retrieved
    records_count = context.count('\n  - ') + context.count('\n  [')
    
    metrics = None
    if enable_metrics:
        metrics = TwoPassMetrics(
            pass1_duration_ms=pass1_duration,
            pass1_tokens_estimated=pass1_tokens,
            pass2_duration_ms=pass2_duration,
            pass2_tokens_estimated=int(pass2_tokens),
            total_duration_ms=total_duration,
            endpoints_queried=len(intent.target_endpoints),
            records_retrieved=records_count,
            intent_confidence=intent.confidence
        )
    
    return final_response, metrics


def format_metrics_report(metrics: TwoPassMetrics) -> str:
    """Format metrics as human-readable report"""
    report = f"""
=== Two-Pass Query Metrics ===
Pass 1 (Intent Classification):
  Duration: {metrics.pass1_duration_ms:.1f}ms
  Estimated tokens: {metrics.pass1_tokens_estimated}
  Budget compliance: {'✓ PASS' if metrics.pass1_tokens_estimated < 1000 else '✗ FAIL'}

Pass 2 (Response Generation):
  Duration: {metrics.pass2_duration_ms:.1f}ms
  Estimated tokens: {metrics.pass2_tokens_estimated}
  Endpoints queried: {metrics.endpoints_queried}
  Records retrieved: {metrics.records_retrieved}

Total:
  Duration: {metrics.total_duration_ms:.1f}ms
  Intent confidence: {metrics.intent_confidence}
"""
    return report


def get_system_info() -> Dict[str, Any]:
    """Get information about the two-pass system"""
    from core.ai_intent_router import validate_pass1_token_budget, get_endpoint_statistics
    
    validation = validate_pass1_token_budget()
    stats = get_endpoint_statistics()
    
    return {
        "system": "Two-Pass AI Assistant",
        "version": "1.0.0",
        "pass1_budget_status": validation["status"],
        "pass1_estimated_tokens": validation["estimated_tokens"],
        "pass1_budget_remaining": validation["budget_remaining"],
        "endpoint_coverage": f"{stats['coverage_percentage']:.1f}%",
        "total_endpoints": stats["total_endpoints"],
        "categories": stats["categories"]
    }
