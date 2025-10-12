import discord
import json
import logging
import os
import aiohttp
from collections import defaultdict
from datetime import datetime, timedelta
from config import save_server_structures
from structurecommands import get_all_structure_assets, get_moon_drills
from administration import extract_corporation_id_from_filename, fetch_corporation_name
from moongoo import get_moon_goo_items

logging.basicConfig(level=logging.INFO)

async def save_moon_goo_to_json(moon_drill_assets, server_id):
    try:
        # Convert defaultdict to a regular dictionary
        regular_dict = {k: dict(v) for k, v in moon_drill_assets.items()}

        # Create filename based on server_id
        filename = f"{server_id}_metenox_goo.json"
        logging.info(f"Saving moon goo data to JSON: {regular_dict}")

        # Create a backup of existing file if it exists
        if os.path.exists(filename):
            backup_name = f"{filename}.bak"
            try:
                os.replace(filename, backup_name)
                logging.info(f"Created backup of {filename} to {backup_name}")
            except Exception as e:
                logging.warning(f"Failed to create backup: {e}")

        with open(filename, 'w') as file:
            json.dump(regular_dict, file, indent=4)
        
        # Verify the file was written correctly
        if os.path.exists(filename) and os.path.getsize(filename) > 0:
            logging.info(f"Successfully saved moon goo data to {filename}")
        else:
            logging.error(f"File {filename} was not created or is empty")
            
    except IOError as e:
        logging.error(f"Error saving moon goo info to JSON file: {e}")

async def load_moon_goo_from_json(server_id):
    filename = f"{server_id}_metenox_goo.json"
    try:
        if os.path.exists(filename):
            # Check if file is not empty
            if os.path.getsize(filename) > 0:
                with open(filename, 'r') as file:
                    data = json.load(file) or {}
                    # Convert loaded data back to a regular dict
                    moon_goo_items = dict(data)
                    logging.info(f"Loaded moon goo data from {filename}: {moon_goo_items}")
                    return moon_goo_items
            else:
                logging.warning(f"{filename} exists but is empty.")
                return {}
        else:
            logging.warning(f"{filename} not found.")
            return {}
    except FileNotFoundError:
        logging.error(f"File not found: {filename}")
        return {}
    except json.JSONDecodeError as e:
        logging.error(f"Error decoding JSON from file {filename}: {e}")
        # Try to recover from backup
        backup_name = f"{filename}.bak"
        if os.path.exists(backup_name):
            try:
                logging.info(f"Attempting to recover from backup {backup_name}")
                with open(backup_name, 'r') as backup_file:
                    return json.load(backup_file) or {}
            except Exception as backup_error:
                logging.error(f"Failed to recover from backup: {backup_error}")
        return {}
    
def load_moon_goo_data(server_id):
    try:
        # Construct the filename pattern based on server_id
        filename_pattern = f"{server_id}_*_metenox_goo.json"
        matching_files = [f for f in os.listdir('.') if f.startswith(f"{server_id}_") and f.endswith("_metenox_goo.json")]

        if not matching_files:
            logging.warning(f"No moon goo JSON files found for server {server_id}.")
            return {}

        moon_goo_data = {}
        for file_name in matching_files:
            with open(file_name, 'r') as file:
                data = json.load(file)
                # Use the file name to determine the station or any other relevant key
                station_id = file_name.split('_')[1]  # Adjust this split based on your file naming
                moon_goo_data[station_id] = data
        
        logging.info(f"Loaded moon goo data from files: {matching_files}")
        return moon_goo_data
    
    except Exception as e:
        logging.error(f"Error loading moon goo data: {str(e)}")
        return {}   

async def handle_fetch_moon_goo_assets(ctx, structure_name=None):
    server_id = str(ctx.guild.id)
    moon_goo_items = get_moon_goo_items()
    logging.info(f"Loaded moon goo items: {moon_goo_items}")

    # Collect all files for the server (assuming they are stored as {server_id}_{corporation_id}_structures.json)
    structure_files = [f for f in os.listdir('.') if f.startswith(f"{server_id}_") and f.endswith("_structures.json")]

    if not structure_files:
        await ctx.send(f"No structure info files found for server {server_id}.")
        return

    moon_drill_assets = defaultdict(lambda: defaultdict(int))

    async def fetch_and_aggregate_assets(ids, corporation_id, corp_name):
        all_assets_info = await get_all_structure_assets(ids, server_id)  # Fetch assets for the structure IDs
        if isinstance(all_assets_info, str):
            await ctx.send(all_assets_info)
            return

        for structure_id, assets_info in all_assets_info.items():
            # Get the correct structure name from the JSON or fall back to Unknown Structure
            structure_name_in_info = server_structures.get('structure_info', {}).get(str(structure_id), f"Unknown Structure (ID: {structure_id})")
            
            # If structure_name is provided in the function argument, filter based on that
            if structure_name and structure_name_in_info != structure_name:
                continue

            for asset in assets_info:
                type_id = asset.get('type_id')
                quantity = asset.get('quantity', 0)

                if type_id in moon_goo_items:
                    item_name = moon_goo_items[type_id]
                    # Aggregating by corporation name and structure name
                    moon_drill_assets[f"{corp_name} - {structure_name_in_info}"][item_name] += quantity

    # Loop through each corporation's structure file
    for structure_file in structure_files:
        # Extract corporation ID from the file name (assuming format {server_id}_{corporation_id}_structures.json)
        corporation_id = structure_file.replace(f"{server_id}_", "").replace("_structures.json", "")

        # Fetch the corporation's name using the ESI API
        corp_name = await fetch_corporation_name(corporation_id)

        # Load structure info for the corporation
        try:
            with open(structure_file, 'r') as file:
                server_structures = json.load(file)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logging.error(f"Error loading or parsing {structure_file}: {e}")
            await ctx.send(f"Error reading '{structure_file}'. Please ensure it is correctly formatted.")
            continue

        # Get all moon drill structure IDs from the configuration
        moon_drill_ids = server_structures.get('metenox_moon_drill_ids', [])

        if not moon_drill_ids:
            moon_drill_ids = await get_moon_drills(server_id)
            if moon_drill_ids:
                server_structures['metenox_moon_drill_ids'] = moon_drill_ids
                save_server_structures(server_structures, server_id, corporation_id)
            else:
                await ctx.send(f"No moon drills found for corporation {corporation_id} or an error occurred.")
                continue

        # Fetch all assets for the corporation and aggregate them
        chunk_size = 100  # Adjust this as needed
        for i in range(0, len(moon_drill_ids), chunk_size):
            await fetch_and_aggregate_assets(moon_drill_ids[i:i + chunk_size], corporation_id, corp_name)

    if not moon_drill_assets:
        await ctx.send("No moon goo data found.")
        return

    # Prepare the response message with proper structure names and corporation distinctions
    response_message = ""
    for structure_name, assets in moon_drill_assets.items():
        response_message += f"**{structure_name}**\n"
        for item_name, total_quantity in assets.items():
            response_message += f"__{item_name}__: ***{total_quantity}***\n"
        response_message += "\n"  # Add a newline for separation

    # Save the aggregated moon drill assets to JSON
    await save_moon_goo_to_json(moon_drill_assets, server_id)

    # Send the message in chunks if necessary
    if len(response_message) > 2000:
        chunks = [response_message[i:i + 2000] for i in range(0, len(response_message), 2000)]
        for chunk in chunks:
            await ctx.send(chunk)
    else:
        await ctx.send(response_message)

#async def update_moon_goo_items_in_json():
#    moon_goo_items = get_moon_goo_items()
#    logging.info(f"Loaded moon goo items: {moon_goo_items}")
#
#    moon_drill_ids = config.get_config('metenox_moon_drill_ids', [])
#    logging.info(f"Loaded moon drill IDs: {moon_drill_ids}")
#
#    all_assets_info = await get_all_structure_assets(moon_drill_ids, config.get_config('server_id'))  # Pass server_id here
#    
#    if isinstance(all_assets_info, str):
#        logging.error(all_assets_info)
#        return
#    
#    logging.info(f"Fetched assets info: {all_assets_info}")
#
#    aggregated_data = defaultdict(dict)
#    
#    for structure_id, assets_info in all_assets_info.items():
#        structure_name = f"Station {structure_id}"
#        
#        for asset in assets_info:
#            type_id = asset.get('type_id')
#            quantity = asset.get('quantity', 0)
#            
#            if type_id in moon_goo_items:
#                item_name = moon_goo_items[type_id]
#                if item_name in aggregated_data[structure_name]:
#                    aggregated_data[structure_name][item_name] += quantity
#                else:
#                    aggregated_data[structure_name][item_name] = quantity
#    
#    if not aggregated_data:
#        logging.info("No moon goo data found.")
#    else:
#        await save_moon_goo_to_json(aggregated_data)
#