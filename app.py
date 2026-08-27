"""
NetBox Universal Library Hub - Main Application
Updated with IPAM provisioning endpoints
"""

import os
import json
import logging
from flask import Flask, render_template, request, jsonify, session
from flask_cors import CORS
from dotenv import load_dotenv

from core.provisioning_service import IPAMProvisioningService, NetBoxClient
from utils.ip_calculator import (
    calculate_next_subnet,
    calculate_usable_range,
    xlookup,
    slugify,
    IPCalculator
)

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
CORS(app)

# Configuration
NETBOX_URL = os.getenv('NETBOX_URL', 'https://ipam.aw.ads')
NETBOX_API_KEY = os.getenv('NETBOX_API_KEY', '')
OPENROUTER_BASE_URL = os.getenv('OPENROUTER_BASE_URL', 'http://omniroute:20128/v1')
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY', 'sk-omniroute-local')
OPENROUTER_MODELS = os.getenv('OPENROUTER_MODELS', '').split(',')

# Initialize services
netbox_client = None
if NETBOX_API_KEY:
    netbox_client = NetBoxClient(NETBOX_URL, NETBOX_API_KEY)

# Cache for site and prefix data
site_cache = []
prefix_cache = []

# Initialize provisioning service
provisioning_service = IPAMProvisioningService(
    netbox_client=netbox_client,
    site_data=site_cache,
    prefix_data=prefix_cache
)


@app.route('/')
def index():
    """Home page"""
    return render_template('index.html')


@app.route('/api/health')
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'netbox_configured': bool(NETBOX_API_KEY),
        'models_configured': len(OPENROUTER_MODELS)
    })


# ============ IPAM Provisioning Endpoints ============

@app.route('/api/provision/sites', methods=['GET'])
def get_sites():
    """Get all sites from NetBox"""
    try:
        if not netbox_client:
            return jsonify({
                'success': False,
                'error': 'NetBox not configured'
            }), 400
        
        sites = netbox_client.get_sites()
        return jsonify({
            'success': True,
            'sites': sites,
            'count': len(sites)
        })
    except Exception as e:
        logger.error(f"Error fetching sites: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/provision/sites/<site_name>', methods=['GET'])
def get_site(site_name):
    """Get site information by name"""
    try:
        if not netbox_client:
            return jsonify({
                'success': False,
                'error': 'NetBox not configured'
            }), 400
        
        site = netbox_client.get_site(site_name)
        if not site:
            return jsonify({
                'success': False,
                'error': f'Site "{site_name}" not found'
            }), 404
        
        # Get site prefixes
        prefixes = netbox_client.get_prefixes(site['id'])
        
        # Find site subnet
        site_subnet = None
        site_cidr = None
        for prefix in prefixes:
            prefix_str = prefix.get('prefix', '')
            if '/' in prefix_str:
                # Check if this is the site subnet (likely the largest)
                # For now, just take the first one as a fallback
                if not site_subnet:
                    site_subnet = prefix_str
                    try:
                        site_cidr = int(prefix_str.split('/')[1])
                    except (ValueError, IndexError):
                        pass
        
        # Try to find site subnet from prefixes
        # Look for prefixes without VLAN association
        for prefix in prefixes:
            if not prefix.get('vlan') and prefix.get('scope_type') == 'dcim.site':
                prefix_str = prefix.get('prefix', '')
                if '/' in prefix_str:
                    site_subnet = prefix_str
                    try:
                        site_cidr = int(prefix_str.split('/')[1])
                    except (ValueError, IndexError):
                        pass
                    break
        
        return jsonify({
            'success': True,
            'site': site,
            'prefixes': prefixes,
            'site_subnet': site_subnet,
            'site_cidr': site_cidr,
            'prefix_count': len(prefixes)
        })
    except Exception as e:
        logger.error(f"Error fetching site {site_name}: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/provision/calculate', methods=['POST'])
def calculate_prefixes():
    """
    Calculate prefixes for a site based on VLAN configs
    """
    try:
        data = request.json
        site_name = data.get('site_name')
        vlan_configs = data.get('vlan_configs', [])
        option_vlans = data.get('option_vlans', [])
        include_option_vlans = data.get('include_option_vlans', True)
        
        if not site_name:
            return jsonify({
                'success': False,
                'error': 'site_name is required'
            }), 400
        
        if not vlan_configs:
            return jsonify({
                'success': False,
                'error': 'vlan_configs is required'
            }), 400
        
        # Validate VLAN configs
        for vlan in vlan_configs:
            if 'id' not in vlan or 'cidr' not in vlan:
                return jsonify({
                    'success': False,
                    'error': 'Each VLAN must have id and cidr'
                }), 400
        
        # Get site info
        site_info = provisioning_service.get_site_info(site_name)
        if not site_info:
            return jsonify({
                'success': False,
                'error': f'Site "{site_name}" not found'
            }), 404
        
        # Get site subnet
        site_subnet = provisioning_service.get_site_subnet(site_info)
        site_cidr = provisioning_service.get_site_subnet_cidr(site_info)
        
        if not site_subnet or not site_cidr:
            return jsonify({
                'success': False,
                'error': f'Could not determine site subnet for "{site_name}"'
            }), 400
        
        # Calculate prefixes
        result = provisioning_service.provision_site_prefixes(
            site_name=site_name,
            vlan_configs=vlan_configs,
            option_vlans=option_vlans if include_option_vlans else [],
            include_option_vlans=include_option_vlans
        )
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error calculating prefixes: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/provision/import', methods=['POST'])
def import_to_netbox():
    """
    Import calculated prefixes to NetBox
    """
    try:
        data = request.json
        site_name = data.get('site_name')
        vlan_configs = data.get('vlan_configs', [])
        option_vlans = data.get('option_vlans', [])
        include_option_vlans = data.get('include_option_vlans', True)
        dry_run = data.get('dry_run', True)
        
        if not netbox_client:
            return jsonify({
                'success': False,
                'error': 'NetBox not configured'
            }), 400
        
        if not site_name:
            return jsonify({
                'success': False,
                'error': 'site_name is required'
            }), 400
        
        if not vlan_configs:
            return jsonify({
                'success': False,
                'error': 'vlan_configs is required'
            }), 400
        
        # Perform import
        result = provisioning_service.import_to_netbox(
            site_name=site_name,
            vlan_configs=vlan_configs,
            option_vlans=option_vlans,
            include_option_vlans=include_option_vlans,
            dry_run=dry_run
        )
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error importing to NetBox: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/provision/validate', methods=['POST'])
def validate_config():
    """
    Validate VLAN configuration
    """
    try:
        data = request.json
        vlan_configs = data.get('vlan_configs', [])
        
        errors = []
        warnings = []
        
        for i, vlan in enumerate(vlan_configs):
            # Check required fields
            if 'id' not in vlan:
                errors.append(f"Row {i+1}: Missing VLAN ID")
            elif not isinstance(vlan['id'], int) or vlan['id'] < 1 or vlan['id'] > 4094:
                errors.append(f"Row {i+1}: Invalid VLAN ID {vlan['id']}. Must be between 1-4094")
            
            if 'cidr' not in vlan:
                errors.append(f"Row {i+1}: Missing CIDR")
            elif not isinstance(vlan['cidr'], int) or vlan['cidr'] < 1 or vlan['cidr'] > 32:
                errors.append(f"Row {i+1}: Invalid CIDR {vlan['cidr']}. Must be between 1-32")
            
            if 'name' not in vlan or not vlan['name']:
                warnings.append(f"Row {i+1}: Missing VLAN name")
            
            # Check for duplicate VLAN IDs
            for j, other in enumerate(vlan_configs):
                if i != j and vlan.get('id') == other.get('id'):
                    errors.append(f"Row {i+1}: Duplicate VLAN ID {vlan['id']} (also in row {j+1})")
        
        return jsonify({
            'success': len(errors) == 0,
            'errors': errors,
            'warnings': warnings,
            'valid': len(errors) == 0
        })
        
    except Exception as e:
        logger.error(f"Error validating config: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# ============ IP Calculation Utilities (Standalone) ============

@app.route('/api/ip/calculate_next', methods=['POST'])
def api_calculate_next_subnet():
    """Standalone endpoint for calculating next subnet"""
    try:
        data = request.json
        previous_ip = data.get('previous_ip')
        prev_cidr = data.get('prev_cidr')
        req_cidr = data.get('req_cidr')
        align = data.get('align', True)
        
        if not all([previous_ip, prev_cidr, req_cidr]):
            return jsonify({
                'success': False,
                'error': 'previous_ip, prev_cidr, and req_cidr are required'
            }), 400
        
        result = calculate_next_subnet(previous_ip, prev_cidr, req_cidr, align)
        
        return jsonify({
            'success': True,
            'previous_ip': previous_ip,
            'prev_cidr': prev_cidr,
            'req_cidr': req_cidr,
            'next_ip': result,
            'align': align
        })
        
    except Exception as e:
        logger.error(f"Error calculating next subnet: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/ip/usable_range', methods=['POST'])
def api_calculate_usable_range():
    """Standalone endpoint for calculating usable IP range"""
    try:
        data = request.json
        ip = data.get('ip')
        cidr = data.get('cidr')
        
        if not all([ip, cidr]):
            return jsonify({
                'success': False,
                'error': 'ip and cidr are required'
            }), 400
        
        result = calculate_usable_range(ip, cidr)
        
        return jsonify({
            'success': True,
            'ip': ip,
            'cidr': cidr,
            'range': result
        })
        
    except Exception as e:
        logger.error(f"Error calculating usable range: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/ip/slugify', methods=['POST'])
def api_slugify():
    """Standalone endpoint for slug generation"""
    try:
        data = request.json
        text = data.get('text', '')
        
        result = slugify(text)
        
        return jsonify({
            'success': True,
            'original': text,
            'slug': result
        })
        
    except Exception as e:
        logger.error(f"Error generating slug: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# ============ Template Context ============

@app.context_processor
def utility_processor():
    """Add utility functions to template context"""
    return {
        'NETBOX_URL': NETBOX_URL,
        'OPENROUTER_BASE_URL': OPENROUTER_BASE_URL,
        'OPENROUTER_MODELS': OPENROUTER_MODELS,
        'VERSION': '2.3.5'
    }


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    debug = os.getenv('DEBUG', 'False').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug)