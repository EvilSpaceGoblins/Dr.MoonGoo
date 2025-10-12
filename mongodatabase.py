import pymongo
import logging
from datetime import datetime
from collections import defaultdict
import os
import yaml
import json
from bson import ObjectId
from administration import get_access_token, fetch_corporation_name, extract_corporation_id_from_filename
from structurecommands import get_all_structure_assets, get_moon_drills, get_structure_name
from moongoo import get_moon_goo_items
from moongoo_commands import load_moon_goo_from_json
from config import save_server_structures, load_server_structures, get_config
import config
import requests

# Load MongoDB config from MongoDB-config.yaml
def load_mongodb_config():
    with open('MongoDB-config.yaml', 'r') as file:
        config_data = yaml.safe_load(file)
        return config_data['mongodb']

# MongoDB connection setup
def get_mongo_client():
    try:
        mongodb_config = load_mongodb_config()
        username = mongodb_config.get("username")
        password = mongodb_config.get("password")
        
        # Use direct string interpolation for username and password
        uri = mongodb_config.get("uri")
        uri = uri.replace("${MONGODB_USERNAME}", username)
        uri = uri.replace("${MONGODB_PASSWORD}", password)
        
        logging.info(f"Connecting to MongoDB with URI: {uri.replace(password, '********')}")
        
        client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=5000)
        # Force a connection to verify it works
        client.server_info()
        logging.info("Successfully connected to MongoDB")
        return client
    except pymongo.errors.ServerSelectionTimeoutError as e:
        logging.error(f"MongoDB connection timeout: {str(e)}")
        raise
    except pymongo.errors.ConnectionFailure as e:
        logging.error(f"MongoDB connection failure: {str(e)}")
        raise
    except Exception as e:
        logging.error(f"MongoDB connection error: {str(e)}")
        raise

# Function to save to MongoDB
def save_to_mongodb(data, collection_name, server_id):
    client = None
    try:
        client = get_mongo_client()
        db = client[load_mongodb_config()['database']]
        collection = db[f"{collection_name}_{server_id}"]

        # Add a timestamp to the data
        data_with_timestamp = {
            "timestamp": datetime.utcnow(),
            "data": data
        }

        result = collection.insert_one(data_with_timestamp)
        logging.info(f"Data successfully saved to MongoDB in {collection_name}_{server_id} with ID: {result.inserted_id}")
        return True
    except pymongo.errors.ServerSelectionTimeoutError as e:
        logging.error(f"MongoDB connection timeout when saving to {collection_name}_{server_id}: {str(e)}")
        return False
    except pymongo.errors.ConnectionFailure as e:
        logging.error(f"MongoDB connection failure when saving to {collection_name}_{server_id}: {str(e)}")
        return False
    except Exception as e:
        logging.error(f"Failed to save data to MongoDB collection {collection_name}_{server_id}: {str(e)}")
        return False
    finally:
        if client:
            client.close()

# Async function to collect gas data
async def collect_gas_data(server_id):
    try:
        # Extract corporation_id from the filename
        corporation_id = extract_corporation_id_from_filename(server_id)
        if not corporation_id:
            logging.error(f"Failed to extract corporation ID for server {server_id}")
            return None
            
        # Fetch the access token for the server and corporation
        access_token = get_access_token(server_id, corporation_id)
        if not access_token:
            logging.error(f"Failed to get access token for server {server_id} and corporation {corporation_id}")
            return None

        headers = {'Authorization': f'Bearer {access_token}'}
        url = f'https://esi.evetech.net/latest/corporations/{corporation_id}/assets/?datasource=tranquility'
        
        # Log the request details
        logging.info(f"Sending request to {url} for corporation {corporation_id}")
        
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            logging.error(f"Error fetching assets: HTTP {response.status_code}, Response: {response.text}")
            return None
            
        data = response.json()
        if isinstance(data, dict) and 'error' in data:
            logging.error(f"Error fetching gas data: {data['error']}")
            return None

        # Load structure info for structure names
        structure_info = {}
        try:
            server_structures = load_server_structures(server_id, corporation_id)
            structure_info = server_structures.get('structure_info', {})
        except Exception as e:
            logging.error(f"Error loading structure information: {str(e)}")
        
        # Process and extract gas-related assets
        gas_data = {}
        for asset in data:
            location_id = asset.get('location_id')
            type_id = asset.get('type_id')
            
            # Check for both Magmatic Gas (81143) and Fuel Blocks (4312, 4246, 4247, 4051)
            if asset.get('location_flag') == 'StructureFuel':
                # If this structure isn't in our gas data yet, add it
                if location_id not in gas_data:
                    gas_data[location_id] = {
                        "magmatic_gas": 0,
                        "fuel_blocks": 0,
                        "structure_name": structure_info.get(str(location_id), f"Unknown Structure ({location_id})")
                    }
                
                # Add quantities based on the type
                if type_id == 81143:  # Magmatic Gas
                    gas_data[location_id]["magmatic_gas"] += asset.get('quantity', 0)
                elif type_id in [4312, 4246, 4247, 4051]:  # Fuel Blocks
                    gas_data[location_id]["fuel_blocks"] += asset.get('quantity', 0)

        # Save the data to MongoDB if it's not empty
        if gas_data and config.USE_MONGODB:
            save_to_mongodb(gas_data, "gas_data", server_id)
            logging.info(f"Saved gas data to MongoDB for server {server_id}")
            
        return gas_data

    except requests.exceptions.RequestException as e:
        logging.error(f"Request error collecting gas data for server {server_id}: {str(e)}")
        return None
    except Exception as e:
        logging.error(f"Failed to collect gas data for server {server_id}: {str(e)}")
        return None

# Async function to collect goo data
async def collect_goo_data(server_id):
    try:
        # Load moon goo items from predefined dictionary
        moon_goo_items = get_moon_goo_items()
        logging.info(f"Loaded moon goo items: {moon_goo_items}")
        
        # Extract corporation_id from the filename
        corporation_id = extract_corporation_id_from_filename(server_id)
        if not corporation_id:
            logging.error(f"Failed to extract corporation ID for server {server_id}")
            return None

        # Fetch the access token for the server and corporation
        access_token = get_access_token(server_id, corporation_id)
        if not access_token:
            logging.error(f"Failed to get access token for server {server_id} and corporation {corporation_id}")
            return None

        headers = {'Authorization': f'Bearer {access_token}'}
        url = f'https://esi.evetech.net/latest/corporations/{corporation_id}/assets/?datasource=tranquility'
        
        # Log the request details
        logging.info(f"Sending request to {url} for corporation {corporation_id}")
        
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            logging.error(f"Error fetching assets: HTTP {response.status_code}, Response: {response.text}")
            return None
            
        data = response.json()

        if 'error' in data:
            return f"Error fetching goo data: {data['error']}"

        # Initialize data structure for storing goo-related assets
        goo_data = defaultdict(lambda: defaultdict(int))

        # Loop through assets and check for goo type IDs
        for asset in data:
            location_flag = asset.get('location_flag')
            type_id = asset.get('type_id')
            quantity = asset.get('quantity', 0)
            location_id = asset.get('location_id')

            if location_flag == 'StructureFuel' and type_id in moon_goo_items:
                structure_name = get_structure_name(location_id, server_id)  # Function to get structure name
                item_name = moon_goo_items[type_id]  # Get item name from moon goo items
                goo_data[structure_name][item_name] += quantity

        return goo_data

    except Exception as e:
        logging.error(f"Failed to collect goo data for server {server_id}: {str(e)}")
        return None

async def collect_moon_goo_data_and_save(server_id, structure_name=None):
    moon_goo_items = get_moon_goo_items()
    logging.info(f"Loaded moon goo items: {moon_goo_items}")

    # Collect all structure files for the server
    structure_files = [f for f in os.listdir('.') if f.startswith(f"{server_id}_") and f.endswith("_structures.json")]

    if not structure_files:
        logging.error(f"No structure info files found for server {server_id}.")
        return

    moon_drill_assets = defaultdict(lambda: defaultdict(int))

    async def fetch_and_aggregate_assets(ids, corporation_id, corp_name):
        all_assets_info = await get_all_structure_assets(ids, server_id)
        if isinstance(all_assets_info, str):
            logging.error(all_assets_info)
            return

        for structure_id, assets_info in all_assets_info.items():
            structure_name_in_info = server_structures.get('structure_info', {}).get(str(structure_id), f"Unknown Structure (ID: {structure_id})")
            
            if structure_name and structure_name_in_info != structure_name:
                continue

            for asset in assets_info:
                type_id = asset.get('type_id')
                quantity = asset.get('quantity', 0)

                if type_id in moon_goo_items:
                    item_name = moon_goo_items[type_id]
                    moon_drill_assets[f"{corp_name} - {structure_name_in_info}"][item_name] += quantity

    for structure_file in structure_files:
        corporation_id = structure_file.replace(f"{server_id}_", "").replace("_structures.json", "")
        corp_name = await fetch_corporation_name(corporation_id)

        try:
            with open(structure_file, 'r') as file:
                server_structures = json.load(file)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logging.error(f"Error loading or parsing {structure_file}: {e}")
            continue

        moon_drill_ids = server_structures.get('metenox_moon_drill_ids', [])

        if not moon_drill_ids:
            moon_drill_ids = await get_moon_drills(server_id)
            if moon_drill_ids:
                server_structures['metenox_moon_drill_ids'] = moon_drill_ids
                save_server_structures(server_structures, server_id, corporation_id)
            else:
                logging.error(f"No moon drills found for corporation {corporation_id} or an error occurred.")
                continue

        chunk_size = 100
        for i in range(0, len(moon_drill_ids), chunk_size):
            await fetch_and_aggregate_assets(moon_drill_ids[i:i + chunk_size], corporation_id, corp_name)

    if not moon_drill_assets:
        logging.info("mongodb Error: No moon goo data found.")
        return

    # Save the aggregated moon drill assets to MongoDB
    save_to_mongodb(moon_drill_assets, "moon_goo_data", server_id)