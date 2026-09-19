"""
IP Address Calculation Utilities
Replicates Excel IPAM provisioning formulas
"""

import ipaddress
import math
import re
from typing import Optional, Dict, List, Tuple, Union


def ip_to_int(ip: str) -> int:
    """
    Convert IP address string to integer
    
    Args:
        ip: IP address string (e.g., "10.113.252.0")
    
    Returns:
        Integer representation of IP
    """
    try:
        return int(ipaddress.IPv4Address(ip))
    except Exception as e:
        raise ValueError(f"Invalid IP address: {ip}") from e


def int_to_ip(ip_int: int) -> str:
    """
    Convert integer to IP address string
    
    Args:
        ip_int: Integer representation of IP
    
    Returns:
        IP address string
    """
    try:
        return str(ipaddress.IPv4Address(ip_int))
    except Exception as e:
        raise ValueError(f"Invalid IP integer: {ip_int}") from e


def cidr_to_block_size(cidr: int) -> int:
    """
    Calculate block size from CIDR notation
    
    Args:
        cidr: CIDR value (e.g., 24 for /24)
    
    Returns:
        Number of IPs in the block
    """
    if not 0 <= cidr <= 32:
        raise ValueError(f"Invalid CIDR: {cidr}. Must be between 0 and 32")
    return 2 ** (32 - cidr)


def calculate_next_subnet(
    previous_ip: str,
    prev_cidr: int,
    req_cidr: int,
    align: bool = True
) -> str:
    """
    Replicates Excel LET formula for next subnet calculation
    
    This is the core formula from the Excel sheet:
    =IF(H5="", "", _xlfn.LET(
        _xlpm.v_PrevIP, F4,
        _xlpm.v_PrevCIDR, VALUE(SUBSTITUTE(H4, "/", "")),
        _xlpm.v_ReqCIDR, VALUE(SUBSTITUTE(H5, "/", "")),
        _xlpm.v_O1, VALUE(TRIM(MID(SUBSTITUTE(_xlpm.v_PrevIP, ".", REPT(" ", 100)), 1, 100))),
        _xlpm.v_O2, VALUE(TRIM(MID(SUBSTITUTE(_xlpm.v_PrevIP, ".", REPT(" ", 100)), 101, 100))),
        _xlpm.v_O3, VALUE(TRIM(MID(SUBSTITUTE(_xlpm.v_PrevIP, ".", REPT(" ", 100)), 201, 100))),
        _xlpm.v_O4, VALUE(TRIM(MID(SUBSTITUTE(_xlpm.v_PrevIP, ".", REPT(" ", 100)), 301, 100))),
        _xlpm.v_PrevDecIP, (_xlpm.v_O1 * 16777216) + (_xlpm.v_O2 * 65536) + (_xlpm.v_O3 * 256) + _xlpm.v_O4,
        _xlpm.v_RawNextIP, _xlpm.v_PrevDecIP + 2^(32 - _xlpm.v_PrevCIDR),
        _xlpm.v_ReqBlockSize, 2^(32 - _xlpm.v_ReqCIDR),
        _xlpm.v_AlignedNextIP, _xlfn.CEILING.MATH(_xlpm.v_RawNextIP, _xlpm.v_ReqBlockSize),
        _xlpm.v_N1, INT(_xlpm.v_AlignedNextIP / 16777216),
        _xlpm.v_N2, MOD(INT(_xlpm.v_AlignedNextIP / 65536), 256),
        _xlpm.v_N3, MOD(INT(_xlpm.v_AlignedNextIP / 256), 256),
        _xlpm.v_N4, MOD(_xlpm.v_AlignedNextIP, 256),
        _xlpm.v_N1 & "." & _xlpm.v_N2 & "." & _xlpm.v_N3 & "." & _xlpm.v_N4
    ))
    
    Args:
        previous_ip: Previous IP address (e.g., "10.113.252.0")
        prev_cidr: Previous CIDR (e.g., 23)
        req_cidr: Requested CIDR (e.g., 24)
        align: Whether to align to block size (default: True)
    
    Returns:
        Next subnet IP address
    """
    if not previous_ip or prev_cidr is None or req_cidr is None:
        return ""

    # Guard: a request for a larger block (smaller CIDR number) than the
    # previous block can never fit. Without this check, a /22 requested
    # after a /23 would silently overflow the parent container.
    if req_cidr < prev_cidr:
        raise ValueError(
            f"Invalid allocation: requested /{req_cidr} (block={cidr_to_block_size(req_cidr)}) "
            f"is larger than the previous /{prev_cidr} (block={cidr_to_block_size(prev_cidr)}). "
            f"req_cidr ({req_cidr}) must be >= prev_cidr ({prev_cidr})."
        )

    try:
        # Convert previous IP to integer
        prev_ip_int = ip_to_int(previous_ip)

        # Calculate previous block size
        prev_block_size = cidr_to_block_size(prev_cidr)

        # Calculate raw next IP (previous IP + previous block size)
        raw_next_ip_int = prev_ip_int + prev_block_size

        if align:
            # Calculate requested block size
            req_block_size = cidr_to_block_size(req_cidr)

            # Align to block size (CEILING.MATH equivalent)
            aligned_next_ip_int = math.ceil(raw_next_ip_int / req_block_size) * req_block_size
            return int_to_ip(aligned_next_ip_int)
        else:
            return int_to_ip(raw_next_ip_int)

    except ValueError:
        raise  # re-raise our own ValueError above
    except Exception as e:
        return f"Error: {str(e)}"


def calculate_usable_range(ip: str, cidr: int) -> str:
    """
    Replicates Excel formula for usable IP range
    
    Excel formula:
    =IF(H13="", "", _xlfn.LET(
        _xlpm.v_IP, G13,
        _xlpm.v_CIDR, VALUE(SUBSTITUTE(H13, "/", "")),
        _xlpm.v_O1, VALUE(TRIM(MID(SUBSTITUTE(_xlpm.v_IP, ".", REPT(" ", 100)), 1, 100))),
        _xlpm.v_O2, VALUE(TRIM(MID(SUBSTITUTE(_xlpm.v_IP, ".", REPT(" ", 100)), 101, 100))),
        _xlpm.v_O3, VALUE(TRIM(MID(SUBSTITUTE(_xlpm.v_IP, ".", REPT(" ", 100)), 201, 100))),
        _xlpm.v_O4, VALUE(TRIM(MID(SUBSTITUTE(_xlpm.v_IP, ".", REPT(" ", 100)), 301, 100))),
        _xlpm.v_DecIP, (_xlpm.v_O1 * 16777216) + (_xlpm.v_O2 * 65536) + (_xlpm.v_O3 * 256) + _xlpm.v_O4,
        _xlpm.v_Block, 2^(32 - _xlpm.v_CIDR),
        _xlpm.v_NetDec, _xlfn.FLOOR.MATH(_xlpm.v_DecIP, _xlpm.v_Block),
        _xlpm.v_BcastDec, _xlpm.v_NetDec + _xlpm.v_Block - 1,
        _xlpm.v_N1, INT(_xlpm.v_NetDec / 16777216),
        _xlpm.v_N2, MOD(INT(_xlpm.v_NetDec / 65536), 256),
        _xlpm.v_N3, MOD(INT(_xlpm.v_NetDec / 256), 256),
        _xlpm.v_N4, MOD(_xlpm.v_NetDec, 256),
        _xlpm.v_B1, INT(_xlpm.v_BcastDec / 16777216),
        _xlpm.v_B2, MOD(INT(_xlpm.v_BcastDec / 65536), 256),
        _xlpm.v_B3, MOD(INT(_xlpm.v_BcastDec / 256), 256),
        _xlpm.v_B4, MOD(_xlpm.v_BcastDec, 256),
        _xlpm.v_N1 & "." & _xlpm.v_N2 & "." & _xlpm.v_N3 & "." & _xlpm.v_N4 & " - " & 
        _xlpm.v_B1 & "." & _xlpm.v_B2 & "." & _xlpm.v_B3 & "." & _xlpm.v_B4
    ))
    
    Args:
        ip: IP address
        cidr: CIDR value
    
    Returns:
        Usable range string (e.g., "10.113.252.0 - 10.113.253.255")
    """
    if not ip or cidr is None:
        return ""
    
    try:
        network = ipaddress.IPv4Network(f"{ip}/{cidr}", strict=False)
        return f"{network.network_address} - {network.broadcast_address}"
    except Exception as e:
        return f"Error: {str(e)}"


def xlookup(
    lookup_value: str,
    lookup_array: List[Dict],
    return_key: str,
    default: str = "Not Found",
    key: str = "name"
) -> Union[str, int]:
    """
    Replicates Excel XLOOKUP function
    
    Args:
        lookup_value: Value to look up
        lookup_array: List of dictionaries to search
        return_key: Key to return from matched dictionary
        default: Default value if not found
        key: Key to match against (default: "name")
    
    Returns:
        Value from matched dictionary or default
    """
    for item in lookup_array:
        if item.get(key, "").lower() == lookup_value.lower():
            return item.get(return_key, default)
    return default


def slugify(text: str) -> str:
    """
    Replicates Excel slug generation:
    =LOWER(SUBSTITUTE(SUBSTITUTE(B1, " - ", "-"), " ", "-"))
    
    Args:
        text: Text to slugify
    
    Returns:
        Slugified string
    """
    if not text:
        return ""
    # Replace " - " with "-"
    text = text.replace(" - ", "-")
    # Replace spaces with "-"
    text = text.replace(" ", "-")
    # Convert to lowercase
    text = text.lower()
    # Remove any non-alphanumeric characters (except hyphens)
    text = re.sub(r'[^a-z0-9-]', '', text)
    return text


def generate_vlan_description(site_name: str, vlan_name: str, vlan_id: int) -> str:
    """
    Replicates Excel VLAN description generation:
    =$B$1&" "&D4&" -- VLAN "&A4
    
    Args:
        site_name: Site name
        vlan_name: VLAN name
        vlan_id: VLAN ID
    
    Returns:
        Formatted description
    """
    return f"{site_name} {vlan_name} -- VLAN {vlan_id}"


def generate_prefix_description(site_name: str, vlan_name: str, vlan_id: int) -> str:
    """
    Replicates Excel prefix description generation:
    =$B$1&" "&D4&" -- VLAN "&A4
    
    Args:
        site_name: Site name
        vlan_name: VLAN name
        vlan_id: VLAN ID
    
    Returns:
        Formatted description
    """
    return f"{site_name} {vlan_name} -- VLAN {vlan_id}"


def generate_site_import_line(site_name: str, slug: str) -> str:
    """
    Replicates Excel site import:
    =$B$1 & "," & $D$1 & ",active"
    
    Args:
        site_name: Site name
        slug: Site slug
    
    Returns:
        CSV line for site import
    """
    return f"{site_name},{slug},active"


def generate_vlan_group_import_line(site_name: str, slug: str, scope_id: int) -> str:
    """
    Replicates Excel VLAN group import:
    =B1&" VLAN Group, "&D1&"-vlan-group,dcim.site,"&$E$1
    
    Args:
        site_name: Site name
        slug: Site slug
        scope_id: Site scope ID
    
    Returns:
        CSV line for VLAN group import
    """
    return f"{site_name} VLAN Group,{slug}-vlan-group,dcim.site,{scope_id}"


def generate_vlan_import_line(
    vlan_id: int,
    vlan_name: str,
    site_name: str,
    description: str,
    role: str
) -> str:
    """
    Replicates Excel VLAN import:
    =A4 & "," & B4 & ",active," & $B$1 & "," & $B$1 & " VLAN Group," & C4 & "," & D4
    
    Args:
        vlan_id: VLAN ID
        vlan_name: VLAN name
        site_name: Site name
        description: VLAN description
        role: VLAN role
    
    Returns:
        CSV line for VLAN import
    """
    return f"{vlan_id},{vlan_name},active,{site_name},{site_name} VLAN Group,{description},{role}"


def generate_prefix_import_line(
    prefix: str,
    site_name: str,
    scope_id: int,
    vlan_id: int,
    role: str,
    description: str
) -> str:
    '''
    Replicates Excel prefix import:
    =G1&H1&",active,dcim.site,"&$E$1&","""&$B$1&" VLAN Group"",,,""Site Subnet - "&I1&""""
    
    Args:
        prefix: Prefix (e.g., "10.113.252.0/23")
        site_name: Site name
        scope_id: Site scope ID
        vlan_id: VLAN ID (or empty for site subnet)
        role: Prefix role
        description: Prefix description
    
    Returns:
        CSV line for prefix import
    '''
    vlan_group = f"{site_name} VLAN Group"
    role_str = f'"{role}"' if role else ""
    desc_str = f'"{description}"' if description else ""
    
    if vlan_id:
        return f"{prefix},active,dcim.site,{scope_id},{vlan_group},{vlan_id},{role_str},{desc_str}"
    else:
        return f"{prefix},active,dcim.site,{scope_id},{vlan_group},,{role_str},{desc_str}"


class IPCalculator:
    """
    Main IP calculation class that replicates all Excel formulas
    """
    
    def __init__(self):
        self.variables = {}
    
    def calculate_next_ip_sequence(
        self,
        start_ip: str,
        start_cidr: int,
        vlan_configs: List[Dict[str, Union[int, str]]]
    ) -> List[Dict]:
        """
        Calculate a sequence of subnets for multiple VLANs
        
        Args:
            start_ip: Starting IP address
            start_cidr: Starting CIDR (site subnet)
            vlan_configs: List of VLAN configs with id, name, cidr, role
        
        Returns:
            List of prefix configurations
        """
        results = []
        prev_ip = start_ip
        prev_cidr = start_cidr
        
        for vlan in vlan_configs:
            vlan_id = vlan.get('id')
            vlan_name = vlan.get('name', f'VLAN {vlan_id}')
            req_cidr = vlan.get('cidr')
            role = vlan.get('role', '')
            description = vlan.get('description', f'{vlan_name}')
            
            # Calculate next subnet
            if prev_ip and prev_cidr and req_cidr:
                next_ip = calculate_next_subnet(prev_ip, prev_cidr, req_cidr)
            else:
                next_ip = prev_ip
            
            # Build prefix config
            prefix_config = {
                'vlan_id': vlan_id,
                'vlan_name': vlan_name,
                'cidr': req_cidr,
                'ip': next_ip,
                'prefix': f"{next_ip}/{req_cidr}" if next_ip and req_cidr else "",
                'role': role,
                'description': description,
                'full_description': generate_vlan_description(
                    vlan.get('site_name', ''),
                    vlan_name,
                    vlan_id
                ) if vlan.get('site_name') else description,
                'usable_range': calculate_usable_range(next_ip, req_cidr) if next_ip and req_cidr else ""
            }
            
            results.append(prefix_config)
            
            # Update for next iteration
            if next_ip and req_cidr:
                prev_ip = next_ip
                prev_cidr = req_cidr
        
        return results
    
    def generate_all_import_data(
        self,
        site_name: str,
        site_slug: str,
        scope_id: int,
        site_subnet: str,
        site_cidr: int,
        vlan_prefixes: List[Dict]
    ) -> Dict[str, List[str]]:
        """
        Generate all NetBox import data
        
        Args:
            site_name: Site name
            site_slug: Site slug
            scope_id: Site scope ID
            site_subnet: Site subnet
            site_cidr: Site CIDR
            vlan_prefixes: List of VLAN prefix configs from calculate_next_ip_sequence
        
        Returns:
            Dictionary with import data
        """
        import_data = {
            'site': [],
            'vlan_group': [],
            'vlan': [],
            'prefix': []
        }
        
        # Site import
        import_data['site'].append(generate_site_import_line(site_name, site_slug))
        
        # VLAN Group import
        import_data['vlan_group'].append(
            generate_vlan_group_import_line(site_name, site_slug, scope_id)
        )
        
        # Site subnet prefix
        import_data['prefix'].append(
            generate_prefix_import_line(
                f"{site_subnet}/{site_cidr}",
                site_name,
                scope_id,
                None,
                "",
                f"Site Subnet - {calculate_usable_range(site_subnet, site_cidr)}"
            )
        )
        
        # VLANs and prefixes
        for prefix in vlan_prefixes:
            if prefix.get('vlan_id'):
                # VLAN import
                import_data['vlan'].append(
                    generate_vlan_import_line(
                        prefix['vlan_id'],
                        prefix['vlan_name'],
                        site_name,
                        prefix.get('full_description', prefix['description']),
                        prefix.get('role', '')
                    )
                )
                
                # Prefix import
                if prefix.get('prefix'):
                    import_data['prefix'].append(
                        generate_prefix_import_line(
                            prefix['prefix'],
                            site_name,
                            scope_id,
                            prefix['vlan_id'],
                            prefix.get('role', ''),
                            prefix.get('full_description', prefix['description'])
                        )
                    )
        
        return import_data