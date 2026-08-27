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


#