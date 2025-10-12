import discord
import logging
import threading
import base64
import requests
import uuid
import asyncio
import datetime
from flask import Flask, request, render_template
from discord.ext import commands
from discord.ui import Select, View
from discord.utils import get
from scheduler import run_alert_scheduler
import config
import tasks
from moongoo_commands import load_moon_goo_from_json
from datetime import datetime
from flask import Flask, send_from_directory
from administration import get_character_info, get_corporation_id
from commands import (
    handle_mongo_pricing, handle_setup, handle_authenticate, handle_update_moondrills, handle_checkgas, handle_spacegoblin, handle_showadmin, handle_help, handle_fetch_moon_goo_assets, handle_structure_pricing
)

# Fetch the configuration values
CALLBACK_URL = config.get_config('eve_online_callback_url')
CLIENT_ID = config.get_config('eve_online_client_id')
CLIENT_SECRET = config.get_config('eve_online_secret_key')

# Define intents
intents = discord.Intents.default()
intents.message_content = True  # Enable the message content intent

# Initialize the bot with the defined intents
bot = commands.Bot(command_prefix='!', intents=intents)

app = Flask(__name__)

bot_start_time = datetime.utcnow()

def run_flask():
    app.run(host='127.0.0.1', port=5005, ssl_context=None)

# Load tokens and server IDs from file
tokens = config.load_all_tokens()
server_ids = config.get_all_server_ids()

# Set up logging
logging.basicConfig(level=logging.DEBUG)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name} ({bot.user.id})')
    print('------')
    tasks.start_tasks(bot)

@bot.event
async def on_guild_join(guild):
    server_id = str(guild.id)
    config.add_server_id(server_id)
    print(f'Joined new server: {guild.name} ({server_id})')

@bot.event
async def on_interaction(interaction: discord.Interaction):
    # Check if it's a component interaction
    if interaction.type == discord.InteractionType.component:
        custom_id = interaction.data.get('custom_id', '')
        
        if custom_id == 'select_alert_channel':
            # Handle alert channel selection
            selected_channel_id = interaction.data.get("values", [])[0]
            server_id = str(interaction.guild.id)

            alert_channels = config.load_alert_channels()
            alert_channels[server_id] = selected_channel_id
            config.save_alert_channels(alert_channels)

            await interaction.response.send_message(f"Alert channel set to <#{selected_channel_id}>")

            # Start alert scheduler if not already running
            if not any(task.get_name() == f"alert_scheduler_{server_id}" for task in asyncio.all_tasks()):
                asyncio.create_task(run_alert_scheduler(bot, server_id), name=f"alert_scheduler_{server_id}")

        elif custom_id == 'select_structure':
            # Handle structure selection
            selected_structure_name = interaction.data['values'][0]
            moon_goo_data = await load_moon_goo_from_json(str(interaction.guild.id))

            if selected_structure_name in moon_goo_data:
                await handle_structure_pricing(interaction, selected_structure_name)
            else:
                await interaction.response.send_message("Selected structure not found in the moon goo data.")

                
async def is_admin(ctx):
    admin_roles = config.get_config('admin_role', [])
    return any(get(ctx.guild.roles, name=role_name) in ctx.author.roles for role_name in admin_roles)

@bot.command()
async def showadmin(ctx):
    if not await is_admin(ctx):
        await ctx.send("You are not authorized to use this command.")
        return
    await handle_showadmin(ctx.message)

@bot.command()
async def selectalertchannel(ctx):
    channels = ctx.guild.text_channels
    if not channels:
        await ctx.send("No text channels found in this server.")
        return

    options = [discord.SelectOption(label=channel.name, value=str(channel.id)) for channel in channels[:25]]
    if not options:
        await ctx.send("No text channels available for selection.")
        return

    select = Select(
        placeholder="Choose a channel...",
        options=options,
        custom_id="select_alert_channel"
    )
    
    view = View()

    async def select_callback(interaction: discord.Interaction):
        if interaction.user != ctx.author:
            await interaction.response.send_message("You are not allowed to use this menu.", ephemeral=True)
            return
        
        selected_channel_id = interaction.data.get("values", [])[0]

        alert_channels = config.load_alert_channels()
        server_id = str(ctx.guild.id)
        alert_channels[server_id] = selected_channel_id
        config.save_alert_channels(alert_channels)

        await interaction.response.send_message(f"Alert channel set to <#{selected_channel_id}>", ephemeral=True)

        if not any(task.get_name() == f"alert_scheduler_{server_id}" for task in asyncio.all_tasks()):
            asyncio.create_task(run_alert_scheduler(bot, server_id), name=f"alert_scheduler_{server_id}")

    select.callback = select_callback
    view.add_item(select)

    await ctx.send("Select an alert channel:", view=view)

# Assuming bot is already defined as 'bot'
@bot.command(name='report')
async def report(ctx):
    server_id = str(ctx.guild.id)
    moon_goo_data = await load_moon_goo_from_json(server_id)

    if moon_goo_data:
        # Create options for each station (assuming the station names can be used for selection)
        options = [discord.SelectOption(label=name, value=name) for name in moon_goo_data.keys()]
        select = discord.ui.Select(placeholder='Select a structure...', options=options, custom_id='select_structure')
        view = discord.ui.View()
        view.add_item(select)
        await ctx.send("Please select a structure to report on:", view=view)
    else:
        await ctx.send("No moon goo data found for this server.")

@bot.event
async def on_interaction(interaction: discord.Interaction):
    # Check if it's a component interaction
    if interaction.type == discord.InteractionType.component:
        # Fetch custom_id from the component interaction data
        custom_id = interaction.data.get('custom_id', '')
        
        if custom_id == 'select_structure':
            # Get the selected structure name
            selected_structure_name = interaction.data['values'][0]
            moon_goo_data = await load_moon_goo_from_json(str(interaction.guild.id))

            # Ensure the selected structure is in the data
            if selected_structure_name in moon_goo_data:
                await handle_structure_pricing(interaction, selected_structure_name)
            else:
                await interaction.response.send_message("Selected structure not found in the moon goo data.")


@bot.command()
async def authenticate(ctx):
    if not await is_admin(ctx):
        await ctx.send("You are not authorized to use this command.")
        return
    await handle_authenticate(ctx)

@bot.command()
async def updatemoondrills(ctx):
    if not await is_admin(ctx):
        await ctx.send("You are not authorized to use this command.")
        return
    await handle_update_moondrills(ctx)

@bot.command()
async def checkGoo(ctx):
    if not await is_admin(ctx):
        await ctx.send("You are not authorized to use this command.")
        return
    await ctx.send("Collecting your MoonGoo information. This may take a moment...")
    await handle_fetch_moon_goo_assets(ctx)

@bot.command()
async def checkGas(ctx):
    await ctx.send("Checking the Fuel Gauges of your Drills! This may take a moment...")
    await handle_checkgas(ctx)

@bot.command()
async def getMeGoo(ctx):
    if not await is_admin(ctx):
        await ctx.send("You are not authorized to use this command.")
        return
    await ctx.send("Running System Check and updating Data... Please Wait")
    await handle_setup(ctx)

@bot.command()
async def goohelp(ctx):
    await handle_help(ctx)

@bot.command()
async def spacegoblin(ctx):
    await handle_spacegoblin(ctx)

@bot.command(name="reportAll")
async def moongoo_report_all(ctx):
    try:
        await ctx.send("Generating the moon goo report. Please wait... \n ")

        # Run the overall handler function
        await handle_mongo_pricing(ctx)


    except Exception as e:
        logging.error(f"Error in moongoo_report_all command: {e}")
        await ctx.send(f"An error occurred: {e}")

@app.route('/images/<path:filename>')
def serve_image(filename):
    # Ensure that the 'images' folder is in the same directory as your app.py
    return send_from_directory('images', filename)


@app.route('/oauth-callback')
def oauth_callback():
    code = request.args.get('code')
    state = request.args.get('state')

    if not code or not state:
        return "Missing code or state parameter", 400

    server_id = config.states.pop(state, None)
    if not server_id:
        return "Invalid or expired state", 400

    token_url = 'https://login.eveonline.com/v2/oauth/token'
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    data = {
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': CALLBACK_URL,
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET
    }

    try:
        response = requests.post(token_url, headers=headers, data=data)
        response.raise_for_status()
        response_data = response.json()
    except requests.exceptions.RequestException as e:
        return f"Request error: {e}", 500

    if 'access_token' not in response_data:
        return f"Error obtaining tokens: {response_data.get('error_description', 'Unknown error')}", 500

    access_token = response_data['access_token']
    refresh_token = response_data.get('refresh_token', None)
    expires_in = response_data.get('expires_in', None)

    character_info = get_character_info(access_token)
    if not character_info:
        return "Failed to retrieve character info", 500

    character_id = character_info.get('CharacterID')
    if not character_id:
        return "Character ID not found in the character info", 500

    corporation_id = get_corporation_id(character_id, access_token)
    if corporation_id is None:
        return "Failed to retrieve corporation ID", 500

    created_at = datetime.utcnow().isoformat() + "Z"

    config.save_token(server_id, corporation_id, access_token, refresh_token, expires_in, created_at, character_id)

    return render_template('oauth_callback.html', character_info=character_info, corporation_id=corporation_id)

@app.route('/terms-of-service')
def tos():
    return render_template('tos.html')

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/privacy-policy')
def privacy():
    return render_template('policy.html')

bot_version = "0.7.2"

#### bot start, do not edit!
if __name__ == "__main__":
    try:
        config.load_config()
        tokens = config.load_all_tokens()
        server_ids = config.get_all_server_ids()
        print("Server IDs:", server_ids)

        flask_thread = threading.Thread(target=run_flask)
        flask_thread.start()

        bot.run(config.get_config('discord_bot_token', ''))
    except discord.errors.LoginFailure:
        print("Invalid Discord - Bot token. Please check your configuration.")
