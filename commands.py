import logging
import yaml
import json
import uuid
import config
import moongoo
import pandas as pd
import os
import discord
from moongoo_commands import handle_fetch_moon_goo_assets
from config import save_server_structures, load_server_structures
from datetime import datetime, timedelta
from administration import extract_corporation_id_from_filename
from moongoo import get_moon_goo_items
from structurecommands import get_all_structure_assets, get_moon_drills, update_structure_info
from moongoo_commands import load_moon_goo_from_json
from market_calculation import format_number, load_market_stats, send_message_in_chunks

logging.basicConfig(level=logging.INFO)


# Access config values using the get_config function
ADMIN_CHANNELS = config.get_config('admin_channels', [])
ADMIN_ROLE = config.get_config('admin_role', 'Admin')
CLIENT_ID = config.get_config('eve_online_client_id', '')
CLIENT_SECRET = config.get_config('eve_online_secret_key', '')
CALLBACK_URL = config.get_config('eve_online_callback_url', '')
CORPORATION_ID = config.get_config('corporation_id', '')
MOON_DRILL_IDS = config.get_config('metenox_moon_drill_ids', [])


# Use the updated methods and variables from config.py
tokens = config.tokens
states = config.states



async def handle_setup(message):
    # Setup completed
    alert_channel_id = str(message.channel.id)
    
    # Set the current channel as an admin channel
    if alert_channel_id not in config.get_config('admin_channels', []):
        admin_channels = config.get_config('admin_channels', [])
        admin_channels.append(alert_channel_id)
        config.set_config('admin_channels', admin_channels)
        config.save_config()

    await message.channel.send(f"Setup complete. Admin channel added.")
    
    # Call the update_moondrills function
    await handle_update_moondrills(message)
    
    # Call the handle_checkgas function
    await handle_checkgas(message)
    
    # Call the handle_fetch_moon_goo_assets function
    await handle_fetch_moon_goo_assets(message)


async def handle_add_alert_channel(ctx):
    # Retrieve the current channel ID and server ID
    alert_channel_id = str(ctx.channel.id)
    server_id = str(ctx.guild.id)

    # Load the existing alert channels
    alert_channels = config.load_alert_channels()

    # Check if the server already has an alert channel set
    if server_id in alert_channels:
        current_alert_channel_id = alert_channels[server_id]
        await ctx.send(f"Alert channel is already set to <#{current_alert_channel_id}>")
    else:
        # Set the new alert channel ID for this server
        alert_channels[server_id] = alert_channel_id
        config.save_alert_channels(alert_channels)
        await ctx.send(f"Alert channel set to <#{alert_channel_id}>")


def generate_state():
    return str(uuid.uuid4())

async def handle_authenticate(message):
    server_id = message.guild.id  # Get the server ID from the message's guild
    state = generate_state()  # Generate a new state
    states[state] = server_id  # Store the server ID associated with the state

    auth_url = (
        f"https://login.eveonline.com/v2/oauth/authorize/?response_type=code"
        f"&redirect_uri={CALLBACK_URL}&client_id={CLIENT_ID}"
        f"&scope=esi-search.search_structures.v1+esi-universe.read_structures.v1"
        f"+esi-assets.read_assets.v1+esi-corporations.read_structures.v1"
        f"+esi-assets.read_corporation_assets.v1+publicData&state={state}"
    )

    await message.channel.send(f"Please [click here]({auth_url}) to authenticate.")

    return True

async def handle_setadmin(message):
    admin_channels = config.get_config('admin_channels', [])
    if message.channel.id in admin_channels:
        await message.channel.send("This channel is already an admin channel.")
        return

    admin_channels.append(message.channel.id)
    config.get_config['admin_channels'] = admin_channels
    config.save_config()
    await message.channel.send(f"Admin channel added: {message.channel.id}\nCurrent admin channels: {admin_channels}")

async def handle_showadmin(message):
    admin_channels = config.get_config('admin_channels', [])
    await message.channel.send(f"Current admin channels: {admin_channels}")

async def handle_update_moondrills(ctx):
    server_id = str(ctx.guild.id)  # Get server_id from the context
    await ctx.send("Updating moon drills... Please wait....\n\n")


    # Fetch the new moon drill IDs
    moon_drill_ids = await get_moon_drills(server_id)

    # Determine the corporation_id dynamically
    corporation_id = extract_corporation_id_from_filename(server_id)
    if not corporation_id:
        await ctx.send("Could not determine the corporation ID. Please contact support.")
        return


    # Backup and delete existing file if it exists
    filename = f"{server_id}_{corporation_id}_structures.json"
    if os.path.exists(filename):
        os.remove(filename)
        logging.info(f"Deleted existing file {filename} before saving the updated structures.")



    # Load existing server structures for the specific server and corporation
    server_structures = load_server_structures(server_id, corporation_id)

    # Ensure server_structures is a dictionary
    if not isinstance(server_structures, dict):
        logging.error(f"Expected server_structures to be a dict, got {type(server_structures)} instead.")
        await ctx.send("An internal error occurred. Please try again later.")
        return

    # Initialize missing keys
    if 'metenox_moon_drill_ids' not in server_structures:
        server_structures['metenox_moon_drill_ids'] = []
    if 'structure_info' not in server_structures:
        server_structures['structure_info'] = {}

    # Update the server's moon drill IDs
    server_structures['metenox_moon_drill_ids'] = moon_drill_ids

    # Fetch and update structure information
    try:
        await update_structure_info(server_id, moon_drill_ids)
    except Exception as e:
        logging.error(f"Error updating structure info: {e}")
        await ctx.send("Failed to update structure information. Please try again later.")
        return

    # Reload the updated structure info from the saved file
    server_structures = load_server_structures(server_id, corporation_id)
    structure_info = server_structures.get('structure_info', {})

    # Debug step: Print the loaded structure_info to check if it matches the expected JSON format
    logging.debug(f"Loaded structure_info: {json.dumps(structure_info, indent=4)}")

    # Prepare the response with structure names and IDs
    response_message = "Metenox Moondrills successfully updated.\n"
    
    for moon_drill_id in moon_drill_ids:
        # Convert the moon_drill_id to a string to match the JSON keys
        structure_name = structure_info.get(str(moon_drill_id), 'Unknown structure')
        response_message += f"{moon_drill_id} - {structure_name}\n"

    await ctx.send(response_message)


    # Save the updated server structures to JSON
    try:
        save_server_structures(server_structures, server_id, corporation_id)
    except ValueError as ve:
        logging.error(f"Error in save_server_structures: {ve}")
        await ctx.send("Failed to save server structures. Please try again later.")
        
    # If no moon drill IDs were found, update the server structures accordingly
    if not moon_drill_ids:
        if 'metenox_moon_drill_ids' not in server_structures:
            server_structures['metenox_moon_drill_ids'] = []

        # Save the updated server structures to JSON
        try:
            save_server_structures(server_structures, server_id, corporation_id)
        except ValueError as ve:
            logging.error(f"Error in save_server_structures: {ve}")
            await ctx.send("Failed to save server structures. Please try again later.")
        
        await ctx.send("No moon drills found or an error occurred.")


async def handle_checkgas(ctx):
    server_id = str(ctx.guild.id)  # Get the server ID from the context

    # Debug: Log the server ID
    logging.info(f"Server ID from context: {server_id}")

    # Get the corporation ID dynamically from the filename
    corporation_id = extract_corporation_id_from_filename(server_id)
    if not corporation_id:
        await ctx.send("Corporation ID could not be determined.")
        return

    # Load server structures from JSON file
    server_structures = load_server_structures(server_id, corporation_id)

    # Debug: Log the loaded data
    logging.info(f"Loaded server structures for server_id {server_id} and corporation_id {corporation_id}: {json.dumps(server_structures, indent=4)}")

    # Check if the expected keys exist in server_structures
    if 'structure_info' not in server_structures or 'metenox_moon_drill_ids' not in server_structures:
        logging.error(f"Keys 'structure_info' and 'metenox_moon_drill_ids' not found in data: {list(server_structures.keys())}")
        await ctx.send("Expected data format not found.")
        return

    # Access the structure info and moon drill IDs directly
    structure_info = server_structures['structure_info']
    moon_drill_ids = server_structures['metenox_moon_drill_ids']

    # Debug: Print structure_info and moon_drill_ids
    logging.info(f"Structure Info: {json.dumps(structure_info, indent=4)}")
    logging.info(f"Moon Drill IDs: {moon_drill_ids}")

    # If moon_drill_ids is empty, update moon drills
    if not moon_drill_ids:
        moon_drill_ids = await get_moon_drills(server_id)
        if moon_drill_ids:
            server_structures['metenox_moon_drill_ids'] = moon_drill_ids
            # Save structure info in the JSON file
            await update_structure_info(server_id, moon_drill_ids)
            save_server_structures(server_structures)
            await ctx.send(f"Metenox Moondrills successfully updated.\n > Moondrill-IDs: \n > {moon_drill_ids}")
        else:
            await ctx.send("No moon drills found or an error occurred.")
            return

    # Fetch and process asset information
    gas_info = ""
    all_assets_info = await get_all_structure_assets(moon_drill_ids, server_id)

    if isinstance(all_assets_info, str):
        await ctx.send(all_assets_info)
        return

    if isinstance(all_assets_info, list):  # Handle list if returned
        logging.error("Unexpected data format received: list")
        await ctx.send("Unexpected data format received.")
        return

    for structure_id, assets_info in all_assets_info.items():
        # Retrieve the correct structure name using the structure ID
        structure_name = structure_info.get(str(structure_id), 'Unknown Structure')
        
        asset_totals = {'Magmatic Gas': 0, 'Fuel Blocks': 0}

        for asset in assets_info:
            type_id = asset.get('type_id')
            quantity = asset.get('quantity', 0)

            if type_id == 81143:  # Type ID for Magmatic Gas
                asset_totals['Magmatic Gas'] += quantity
            elif type_id in [4312, 4246, 4247, 4051]:  # Type IDs for Fuel Blocks
                asset_totals['Fuel Blocks'] += quantity

        magmatic_gas_amount = asset_totals['Magmatic Gas']
        fuel_blocks_amount = asset_totals['Fuel Blocks']

        def calculate_depletion_time(amount, rate_per_hour):
            if amount <= 0 or rate_per_hour <= 0:
                return "Unknown"

            depletion_hours = amount / rate_per_hour
            depletion_time = datetime.utcnow() + timedelta(hours=depletion_hours)
            remaining_time = depletion_time - datetime.utcnow()

            days, remainder = divmod(remaining_time.total_seconds(), 86400)
            hours, remainder = divmod(remainder, 3600)
            minutes, _ = divmod(remainder, 60)

            return f"> {depletion_time.strftime('%Y-%m-%d %H:%M:%S')} UTC - {int(days)} Days {int(hours)} Hours remaining"

        magmatic_gas_depletion_time = calculate_depletion_time(magmatic_gas_amount, 200)  # 200 units per hour (updated by CCP)
        fuel_blocks_depletion_time = calculate_depletion_time(fuel_blocks_amount, 5)  # 5 units per hour

        gas_info += f"**{structure_name}**\n"
        gas_info += f"> __Magmatic Gas__: ***{magmatic_gas_amount}*** is left\n"
        gas_info += f" Gas runs out in: {magmatic_gas_depletion_time}\n"
        gas_info += f"> __Fuel Blocks__: ***{fuel_blocks_amount}*** are left\n"
        gas_info += f" Fuel runs out in: {fuel_blocks_depletion_time}\n"
        gas_info += "\n"

    if len(gas_info) > 2000:
        chunks = [gas_info[i:i + 2000] for i in range(0, len(gas_info), 2000)]
        for chunk in chunks:
            await ctx.send(chunk)
    else:
        await ctx.send(gas_info)

async def handle_mongo_pricing(ctx):
    server_id = str(ctx.guild.id)

    try:
        logging.debug("Starting moon goo pricing process")

        # Load moon goo data from the JSON file based on the server ID
        moon_goo_data = await load_moon_goo_from_json(server_id)
        logging.debug(f"Loaded moon goo data for server {server_id}: {moon_goo_data}")

        # Load market stats data
        market_stats = load_market_stats()
        logging.debug(f"Loaded market stats: {market_stats}")

        # Fetch the MOON_GOO_ITEMS mapping
        moon_goo_items = moongoo.get_moon_goo_items()

        # Initialize output string
        output = []

        # Loop through stations and items in moon goo data
        for station_name, items in moon_goo_data.items():
            # Add station name in bold
            output.append(f"**{station_name}:**")
            for item_name, amount in items.items():
                try:
                    # Fetch the item ID from the `MOON_GOO_ITEMS` mapping
                    item_id = None
                    for key, value in moon_goo_items.items():
                        if value == item_name:
                            item_id = key
                            break
                    
                    if item_id is None:
                        logging.warning(f"Item '{item_name}' not found in MOON_GOO_ITEMS mapping.")
                        continue

                    # Get market stats for the item
                    item_stats = market_stats.get(str(item_id), {})
                    buy_price = item_stats.get('buyAvgFivePercent', 0)
                    sell_price = item_stats.get('sellAvgFivePercent', 0)

                    total_buy_price = buy_price * amount
                    total_sell_price = sell_price * amount

                    # Append formatted data to output
                    formatted_buy_price = format_number(total_buy_price)
                    formatted_sell_price = format_number(total_sell_price)
                    
                    output.append(f"> **{amount}** - {item_name} -> **Buy**: {formatted_buy_price} **Sell**: {formatted_sell_price}")

                except Exception as item_error:
                    logging.error(f"Error processing item {item_name} in station {station_name}: {str(item_error)}")

        # Convert the output list to a single string
        output_message = "\n".join(output)
        logging.debug(f"Generated output message: {output_message}")

        # Split the message into chunks if necessary
        max_message_length = 2000
        while len(output_message) > max_message_length:
            # Find the last newline within the limit to avoid breaking a line
            split_index = output_message.rfind('\n', 0, max_message_length)
            if split_index == -1:
                split_index = max_message_length
            
            # Send the chunked message
            await ctx.send(output_message[:split_index])
            logging.debug(f"Sent message chunk: {output_message[:split_index]}")
            output_message = output_message[split_index:].lstrip('\n')

        # Send the remaining part of the message
        if output_message:
            await ctx.send(output_message)
            logging.debug(f"Sent remaining message: {output_message}")

    except Exception as e:
        # Log the error and send the message only if an actual exception occurs
        logging.error(f"Error in overall moon goo handler: {str(e)}")

async def handle_structure_pricing(interaction: discord.Interaction, structure_name: str):
    server_id = str(interaction.guild.id)

    try:
        logging.debug("Starting moon goo pricing process for structure")

        # Load moon goo data from the JSON file based on the server ID
        moon_goo_data = await load_moon_goo_from_json(server_id)
        logging.debug(f"Loaded moon goo data for server {server_id}: {moon_goo_data}")

        # Load market stats data
        market_stats = load_market_stats()
        logging.debug(f"Loaded market stats: {market_stats}")

        # Fetch the MOON_GOO_ITEMS mapping
        moon_goo_items = get_moon_goo_items()

        # Initialize output string
        output = []

        # Process the data only for the selected structure
        if structure_name in moon_goo_data:
            items = moon_goo_data[structure_name]
            output.append(f"**{structure_name}:**")
            for item_name, amount in items.items():
                try:
                    # Fetch the item ID from the `MOON_GOO_ITEMS` mapping
                    item_id = None
                    for key, value in moon_goo_items.items():
                        if value == item_name:
                            item_id = key
                            break

                    if item_id is None:
                        logging.warning(f"Item '{item_name}' not found in MOON_GOO_ITEMS mapping.")
                        continue

                    # Get market stats for the item
                    item_stats = market_stats.get(str(item_id), {})
                    buy_price = item_stats.get('buyAvgFivePercent', 0)
                    sell_price = item_stats.get('sellAvgFivePercent', 0)

                    total_buy_price = buy_price * amount
                    total_sell_price = sell_price * amount

                    # Append formatted data to output
                    formatted_buy_price = format_number(total_buy_price)
                    formatted_sell_price = format_number(total_sell_price)

                    output.append(f"**{item_name}**: {amount} --- **Buy**: {formatted_buy_price} --- **Sell**: {formatted_sell_price}")

                except Exception as item_error:
                    logging.error(f"Error processing item {item_name} in structure {structure_name}: {str(item_error)}")

        # Convert the output list to a single string
        output_message = "\n".join(output)
        logging.debug(f"Generated output message: {output_message}")

        # Send the message in chunks if necessary
        await send_message_in_chunks(interaction, output_message)

    except Exception as e:
        logging.error(f"Error in overall moon goo handler: {str(e)}")
        await interaction.response.send_message("An error occurred while processing the moon goo data.")


async def handle_help(message):
    await message.channel.send(
        "Hello My Name is Dr. MoonGoo, here are some basic commands.\n\n"
        "**Common commands:**\n"
        "**!authenticate**: Authenticate the bot against the EvE Online ESI API\n"
        "**!selectalertchannel**: Select the channel where the alert scheduler will send messages to\n"
        "**!checkgas**: Prints the amount of Magmatic Gas and Fuel Blocks within the Moondrill with the date/time when it runs out.\n"
        "**!checkGoo**: Prints the amount of Goo the Moondrills have collected till now.\n"
        "When setup with !GooAlert I will send you a message in a channel where you run the command if fuel runs out within the next 48 hours\n\n"
        "Feel free to open a GitHub issue here: https://github.com/DrDeef/Dr.MoonGoo"
    )



async def handle_spacegoblin(message):
    await message.channel.send(
        "**Guess who is a good Spacegoblin?**.\n\n"
        "Not you, since you are not an Evil Space Goblin\n"
        ";....;"
    )
