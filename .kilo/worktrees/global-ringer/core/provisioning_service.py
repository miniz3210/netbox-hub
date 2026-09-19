"""
IPAM Provisioning Service
Handles site provisioning and NetBox integration
"""

import json
import logging
from typing import Dict, List, Optional, Any, Union
from datetime import datetime

from utils.ip_calculator import (
    IPCalculator,
    calculate_next_subnet,
    calculate_usable_range,
    xlookup,
    slugify,
    generate_vlan_description
)

# Configure logging
logger = logging.getLogger(__name__)


class NetBoxClient:
    """
    Simplified NetBox API client for provisioning
    """
    
    def __init__(self, base_url: str, api_key: str, verify_ssl: bool = True):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.verify_ssl = verify_ssl
        self.session = None
    
    def _get_session(self):
        """Get or create requests session"""
        if self.session is None:
            import requests
            self.session = requests.Session()
            self.session.headers.update({
                'Authorization': f'Token {self.api_key}',
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            })
            self.session.verify = self.verify_ssl
        return self.session
    
    def get(self, endpoint: str, params: Optional[Dict] = None) -> Dict:
        """GET request to NetBox API"""
        session = self._get_session()
        url = f"{self.base_url}/api/{endpoint.lstrip('/')}"
        response = session.get(url, params=params)
        response.raise_for_status()
        return response.json()
    
    def post(self, endpoint: str, data: Dict) -> Dict:
        """POST request to NetBox API"""
        session = self._get_session()
        url = f"{self.base_url}/api/{endpoint.lstrip('/')}"
        response = session.post(url, json=data)
        response.raise_for_status()
        return response.json()
    
    def get_site(self, site_name: str) -> Optional[Dict]:
        """Get site by name"""
        try:
            response = self.get('dcim/sites/', params={'name': site_name})
            results = response.get('results', [])
            return results[0] if results else None
        except Exception as e:
            logger.error(f"Error fetching site {site_name}: {e}")
            return None
    
    def get_sites(self) -> List[Dict]:
        """Get all sites"""
        try:
            response = self.get('dcim/sites/', params={'limit': 1000})
            return response.get('results', [])
        except Exception as e:
            logger.error(f"Error fetching sites: {e}")
            return []
    
    def get_prefixes(self, site_id: int) -> List[Dict]:
        """Get prefixes for a site"""
        try:
            response = self.get('ipam/prefixes/', params={
                'scope_type': 'dcim.site',
                'scope_id': site_id,
                'limit': 1000
            })
            return response.get('results', [])
        except Exception as e:
            logger.error(f"Error fetching prefixes for site {site_id}: {e}")
            return []
    
    def create_vlan(self, vlan_data: Dict) -> Dict:
        """Create a VLAN"""
        return self.post('ipam/vlans/', vlan_data)
    
    def create_prefix(self, prefix_data: Dict) -> Dict:
        """Create a prefix"""
        return self.post('ipam/prefixes/', prefix_data)


class IPAMProvisioningService:
    """
    Main provisioning service for IPAM
    """
    
    def __init__(
        self,
        netbox_client: Optional[NetBoxClient] = None,
        site_data: Optional[List[Dict]] = None,
        prefix_data: Optional[List[Dict]] = None
    ):
        self.netbox_client = netbox_client
        self.site_data = site_data or []
        self.prefix_data = prefix_data or []
        self.calculator = IPCalculator()
    
    def get_site_info(self, site_name: str) -> Dict:
        """
        Get site information from NetBox or local cache
        
        Args:
            site_name: Name of the site
        
        Returns:
            Site information dictionary
        """
        # Try to get from cache first
        for site in self.site_data:
            if site.get('name', '').lower() == site_name.lower():
                return site
        
        # Try to get from NetBox
        if self.netbox_client:
            site = self.netbox_client.get_site(site_name)
            if site:
                self.site_data.append(site)
                return site
        
        return {}
    
    def get_site_subnet(self, site_info: Dict) -> Optional[str]:
        """
        Get site subnet from site info or prefixes.

        Returns the largest matching block (smallest CIDR value) because
        the site supernet must contain all VLAN subnets.
        
        Args:
            site_info: Site information dictionary
        
        Returns:
            Site subnet or None
        """
        # Try to get from prefixes — pick the largest block (smallest CIDR)
        if self.prefix_data:
            best: Optional[str] = None
            best_cidr: int = 33  # larger than any valid /32
            for prefix in self.prefix_data:
                if (prefix.get('scope_type') == 'dcim.site' and
                    prefix.get('scope_id') == site_info.get('id')):
                    prefix_str = prefix.get('prefix', '')
                    if '/' in prefix_str:
                        try:
                            cidr = int(prefix_str.split('/')[1])
                        except (ValueError, IndexError):
                            continue
                        if cidr < best_cidr:
                            best = prefix_str
                            best_cidr = cidr
            if best:
                return best
        
        # Try to get from site info
        if site_info.get('site_subnet'):
            return site_info['site_subnet']
        
        return None
    
    def get_site_subnet_cidr(self, site_info: Dict) -> Optional[int]:
        """
        Get site subnet CIDR from site info or prefixes.

        Returns the smallest CIDR (largest block) for the matching site,
        mirroring the logic in get_site_subnet.
        
        Args:
            site_info: Site information dictionary
        
        Returns:
            Site CIDR or None
        """
        # Try to get from prefixes — pick the largest block (smallest CIDR)
        if self.prefix_data:
            best_cidr: Optional[int] = None
            for prefix in self.prefix_data:
                if (prefix.get('scope_type') == 'dcim.site' and
                    prefix.get('scope_id') == site_info.get('id')):
                    prefix_str = prefix.get('prefix', '')
                    if '/' in prefix_str:
                        try:
                            cidr = int(prefix_str.split('/')[1])
                        except (ValueError, IndexError):
                            continue
                        if best_cidr is None or cidr < best_cidr:
                            best_cidr = cidr
            if best_cidr is not None:
                return best_cidr
        
        # Try to get from site info
        if site_info.get('site_cidr') is not None:
            return int(site_info['site_cidr'])
        
        return None
    
    def provision_site_prefixes(
        self,
        site_name: str,
        vlan_configs: List[Dict[str, Any]],
        option_vlans: Optional[List[Dict[str, Any]]] = None,
        include_option_vlans: bool = True
    ) -> Dict[str, Any]:
        """
        Generate prefixes for a site based on VLAN configs
        
        Args:
            site_name: Name of the site
            vlan_configs: List of VLAN configurations
            option_vlans: Optional VLAN configurations
            include_option_vlans: Whether to include optional VLANs
        
        Returns:
            Dictionary with provisioning results
        """
        # Get site info
        site_info = self.get_site_info(site_name)
        if not site_info:
            return {
                "success": False,
                "error": f"Site '{site_name}' not found",
                "site_info": None,
                "prefixes": []
            }
        
        # Get site subnet
        site_subnet = self.get_site_subnet(site_info)
        site_cidr = self.get_site_subnet_cidr(site_info)
        
        if not site_subnet or not site_cidr:
            return {
                "success": False,
                "error": f"Site subnet not found for '{site_name}'",
                "site_info": site_info,
                "prefixes": []
            }
        
        # Add site info to VLAN configs
        for vlan in vlan_configs:
            vlan['site_name'] = site_name
        
        # Calculate prefixes for main VLANs
        results = self.calculator.calculate_next_ip_sequence(
            start_ip=site_subnet,
            start_cidr=site_cidr,
            vlan_configs=vlan_configs
        )
        
        # Calculate prefixes for option VLANs if enabled
        option_results = []
        if include_option_vlans and option_vlans:
            # Get the last IP from main VLANs
            last_prefix = results[-1] if results else None
            if last_prefix and last_prefix.get('ip') and last_prefix.get('cidr'):
                # Add site info to option VLAN configs
                for vlan in option_vlans:
                    vlan['site_name'] = site_name
                
                # Calculate option VLAN prefixes
                option_results = self.calculator.calculate_next_ip_sequence(
                    start_ip=last_prefix['ip'],
                    start_cidr=last_prefix['cidr'],
                    vlan_configs=option_vlans
                )
        
        # Combine results
        all_prefixes = results + option_results
        
        # Generate import data
        site_slug = slugify(site_name)
        scope_id = site_info.get('id')
        
        import_data = self.calculator.generate_all_import_data(
            site_name=site_name,
            site_slug=site_slug,
            scope_id=scope_id,
            site_subnet=site_subnet,
            site_cidr=site_cidr,
            vlan_prefixes=all_prefixes
        )
        
        return {
            "success": True,
            "site_info": site_info,
            "site_subnet": site_subnet,
            "site_cidr": site_cidr,
            "prefixes": all_prefixes,
            "import_data": import_data,
            "count": len(all_prefixes)
        }
    
    def import_to_netbox(
        self,
        site_name: str,
        vlan_configs: List[Dict[str, Any]],
        option_vlans: Optional[List[Dict[str, Any]]] = None,
        include_option_vlans: bool = True,
        dry_run: bool = True
    ) -> Dict[str, Any]:
        """
        Provision and import to NetBox
        
        Args:
            site_name: Name of the site
            vlan_configs: List of VLAN configurations
            option_vlans: Optional VLAN configurations
            include_option_vlans: Whether to include optional VLANs
            dry_run: If True, only generate data without importing
        
        Returns:
            Dictionary with import results
        """
        if not self.netbox_client:
            return {
                "success": False,
                "error": "NetBox client not configured",
                "dry_run": dry_run
            }
        
        # Generate provisioning data
        provision_result = self.provision_site_prefixes(
            site_name=site_name,
            vlan_configs=vlan_configs,
            option_vlans=option_vlans,
            include_option_vlans=include_option_vlans
        )
        
        if not provision_result.get('success'):
            return provision_result
        
        import_data = provision_result.get('import_data', {})
        
        if dry_run:
            return {
                "success": True,
                "dry_run": True,
                "import_data": import_data,
                "prefixes": provision_result.get('prefixes', [])
            }
        
        # Actual import
        results = {
            "success": True,
            "dry_run": False,
            "created": {
                "vlans": [],
                "prefixes": []
            },
            "errors": []
        }
        
        # Use a real CSV parser — the generators wrap fields in quotes which
        # would be mangled by a naive .split(',').
        import csv as _csv
        from io import StringIO

        # Import VLANs
        for vlan_line in import_data.get('vlan', []):
            try:
                row = next(_csv.reader(StringIO(vlan_line)))
                vlan_data = {
                    'vid': int(row[0]),
                    'name': row[1],
                    'status': row[2],
                    'site': row[3],
                    'group': row[4],
                    'description': row[5] if len(row) > 5 else '',
                    'role': row[6] if len(row) > 6 else ''
                }
                # Create VLAN in NetBox
                response = self.netbox_client.create_vlan(vlan_data)
                results['created']['vlans'].append(response)
            except Exception as e:
                results['errors'].append(f"Error creating VLAN {vlan_line}: {e}")
        
        # Import prefixes
        for prefix_line in import_data.get('prefix', []):
            try:
                row = next(_csv.reader(StringIO(prefix_line)))
                prefix_data = {
                    'prefix': row[0],
                    'status': row[1] if len(row) > 1 else 'active',
                    'scope_type': row[2] if len(row) > 2 else 'dcim.site',
                    'scope_id': int(row[3]) if len(row) > 3 and row[3] else None,
                    'vlan_group': row[4] if len(row) > 4 else '',
                    'vlan': int(row[5]) if len(row) > 5 and row[5] else None,
                    'role': row[6] if len(row) > 6 else '',
                    'description': row[7] if len(row) > 7 else ''
                }
                # Create prefix in NetBox
                response = self.netbox_client.create_prefix(prefix_data)
                results['created']['prefixes'].append(response)
            except Exception as e:
                results['errors'].append(f"Error creating prefix {prefix_line}: {e}")
        
        return results


class ExcelFormulaMapper:
    """
    Maps Excel formulas to Python functions
    """
    
    @staticmethod
    def map_formula(formula_type: str, **params) -> Any:
        """
        Map Excel formula to Python implementation
        
        Args:
            formula_type: Type of formula (e.g., 'NEXT_SUBNET', 'USABLE_RANGE')
            params: Parameters for the formula
        
        Returns:
            Result of the formula
        """
        formula_map = {
            'NEXT_SUBNET': calculate_next_subnet,
            'USABLE_RANGE': calculate_usable_range,
            'XLOOKUP': xlookup,
            'SLUGIFY': slugify,
            'VLAN_DESCRIPTION': generate_vlan_description
        }
        
        func = formula_map.get(formula_type)
        if func:
            return func(**params)
        return None
    
    @staticmethod
    def parse_excel_let_formula(formula: str) -> Dict:
        """
        Parse Excel LET formula to extract variables and expression
        
        Args:
            formula: Excel LET formula string
        
        Returns:
            Dictionary with variables and expression
        """
        # Remove _xlfn.LET( and trailing ))
        if '_xlfn.LET(' in formula:
            formula = formula.replace('_xlfn.LET(', '')
        if formula.endswith('))'):
            formula = formula[:-2]
        elif formula.endswith(')'):
            formula = formula[:-1]
        
        # Split by commas (handle nested)
        parts = []
        current = ""
        depth = 0
        for char in formula:
            if char == '(':
                depth += 1
            elif char == ')':
                depth -= 1
            elif char == ',' and depth == 0:
                parts.append(current.strip())
                current = ""
                continue
            current += char
        if current.strip():
            parts.append(current.strip())
        
        # First part is the expression, rest are variable definitions
        if not parts:
            return {'variables': [], 'expression': ''}
        
        expression = parts[-1]
        variables = parts[:-1]
        
        parsed_vars = []
        for var in variables:
            if '=' in var:
                name, value = var.split('=', 1)
                parsed_vars.append({
                    'name': name.strip(),
                    'value': value.strip()
                })
            else:
                parsed_vars.append({
                    'name': var.strip(),
                    'value': ''
                })
        
        return {
            'variables': parsed_vars,
            'expression': expression
        }