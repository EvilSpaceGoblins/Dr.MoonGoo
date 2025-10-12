import asyncio
from datetime import datetime, timedelta
import json
import os
import logging
import aiohttp
from administration import get_access_token, extract_corporation_id_from_filename
import config

# Configure logging
logging.basicConfig(level=logging.INFO)

# File to store alert channels
ALERT_CHANNELS_FILE = 'alert_channels.json'

# Dictionary to track the last alert time for each structure
last_alert_times = {
    'magmatic_gas': {},
    'fuel_blocks': {}
}

async def run_alert_scheduler(bot, server_id):
    logging.info(f"Starting alert scheduler for server {server_id}")
    while True:
        alert_channels = config.load_alert_channels()
        alert_channel_id = alert_channels.get(server_id)
        if not alert_channel_id:
            await asyncio.sleep(3600)  # Sleep for an hour if no alert channel is set
            continue

        alert_channel = bot.get_channel(int(alert_channel_id))
        if alert_channel:
            await check_gas_and_send_alerts(alert_channel, server_id)

        await asyncio.sleep(3600)  # Check every hour

# Function to check gas and send alerts for a specific server
async def check_gas_and_send_alerts(alert_channel, server_id):
    # Dynamically determine the corporation_id and structure file
    corporation_id = extract_corporation_id_from_filename(server_id)
    if not corporation_id:
        logging.error(f"Could not determine corporation ID for server {server_id}.")
        return

    structure_file = f"{server_id}_{corporation_id}_structures.json"
    
    if not os.path.exists(structure_file):
        logging.error(f"Structure file {structure_file} not found.")
        return

    with open(structure_file, 'r') as file:
        structure_info = json.load(file)

    moon_drill_ids = config.get_config('metenox_moon_drill_ids', server_id)
    
    try:
        all_assets_info = await get_all_structure_assets_for_server(moon_drill_ids, server_id)
    except Exception as e:
        logging.error(f"Error getting assets info: {e}")
        await alert_channel.send("Failed to retrieve structure assets.")
        return

    if isinstance(all_assets_info, str):
        await alert_channel.send(all_assets_info)
        return

    current_time = datetime.utcnow()

    for structure_id, assets_info in all_assets_info.items():
        structure_name = structure_info.get(structure_id, 'Unknown Structure')
        asset_totals = {'Magmatic Gas': 0, 'Fuel Blocks': 0}

        for asset in assets_info:
            type_id = asset.get('type_id')
            quantity = asset.get('quantity')
            if type_id == 81143:  # Magmatic Gas
                asset_totals['Magmatic Gas'] += quantity
            elif type_id == 4312:  # Fuel Blocks
                asset_totals['Fuel Blocks'] += quantity

        magmatic_gas_amount = asset_totals['Magmatic Gas']
        fuel_blocks_amount = asset_totals['Fuel Blocks']

        magmatic_gas_depletion_time, magmatic_gas_days, magmatic_gas_hours = calculate_depletion_time(magmatic_gas_amount, 200)
        fuel_blocks_depletion_time, fuel_blocks_days, fuel_blocks_hours = calculate_depletion_time(fuel_blocks_amount, 5)

        # Use helper function for alerts
        await handle_alerts(alert_channel, structure_name, 'magmatic_gas', magmatic_gas_days, magmatic_gas_hours, magmatic_gas_amount, current_time)
        await handle_alerts(alert_channel, structure_name, 'fuel_blocks', fuel_blocks_days, fuel_blocks_hours, fuel_blocks_amount, current_time)


async def handle_alerts(alert_channel, structure_name, alert_type, days, hours, amount, current_time):
    if days < 2 or (days == 1 and hours < 24):
        if structure_name not in last_alert_times[alert_type]:
            last_alert_times[alert_type][structure_name] = {
                '48h': None,
                '24h': None
            }

        # Check 48h alert
        if days == 2 and (last_alert_times[alert_type][structure_name]['48h'] is None or 
                          current_time - last_alert_times[alert_type][structure_name]['48h'] >= timedelta(days=1)):
            await alert_channel.send(f"**{structure_name}**: {alert_type.replace('_', ' ').title()} is running low!\n{alert_type.replace('_', ' ').title()}: ***{amount}***\nRuns out in: {days} Days {hours} Hours")
            last_alert_times[alert_type][structure_name]['48h'] = current_time

        # Check 24h alert
        elif days == 1 and (last_alert_times[alert_type][structure_name]['24h'] is None or 
                            current_time - last_alert_times[alert_type][structure_name]['24h'] >= timedelta(days=1)):
            await alert_channel.send(f"**{structure_name}**: {alert_type.replace('_', ' ').title()} is running low!\n{alert_type.replace('_', ' ').title()}: ***{amount}***\nRuns out in: {days} Days {hours} Hours")
            last_alert_times[alert_type][structure_name]['24h'] = current_time


# Helper function for structure alerts
async def get_all_structure_assets_for_server(structure_ids, server_id):
    corporation_id = extract_corporation_id_from_filename(server_id)
    if not corporation_id:
        return 'Failed to get corporation ID'

    access_token = get_access_token(server_id, corporation_id)
    if not access_token:
        return 'Failed to get access token'

    headers = {'Authorization': f'Bearer {access_token}'}
    url = f'https://esi.evetech.net/latest/corporations/{corporation_id}/assets/?datasource=tranquility'

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                data = await response.json()

        if 'error' in data:
            return f"Error fetching structure assets: {data['error']}"

        all_assets = {}
        for structure_id in structure_ids:
            assets = [asset for asset in data if asset.get('location_id') == structure_id and asset.get('location_flag') == 'StructureFuel']
            if assets:
                all_assets[structure_id] = assets

        return all_assets

    except aiohttp.ClientError as e:
        logging.error(f"HTTP Client Error: {e}")
        return "Failed to fetch structure assets."

    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        return "An unexpected error occurred."

# Function to calculate depletion time
def calculate_depletion_time(amount, rate_per_hour):
    if amount <= 0 or rate_per_hour <= 0:
        return "Unknown", 0, 0

    depletion_hours = amount / rate_per_hour
    depletion_time = datetime.utcnow() + timedelta(hours=depletion_hours)
    remaining_time = depletion_time - datetime.utcnow()

    days, remainder = divmod(remaining_time.total_seconds(), 86400)
    hours, _ = divmod(remainder, 3600)

    return depletion_time, int(days), int(hours)