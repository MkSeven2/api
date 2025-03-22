from flask import Flask, jsonify, abort, request, g
import urllib.parse
import requests
import time
import json
from collections import deque
import threading
import os
import logging
from typing import Dict, Deque, Union, List, Any  # For type hinting

app = Flask(__name__)

# --- Configuration (Editable Settings) ---

SETTINGS_FILE = 'settings.json'

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO,  # Set the logging level (INFO, DEBUG, ERROR, etc.)
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def load_settings() -> Dict[str, Union[int, float, List[str], str]]:
    """Loads settings from the settings.json file with robust error handling."""
    default_settings = {
        "RATE_LIMIT_PER_PERIOD": 10,
        "RATE_LIMIT_PERIOD_SECONDS": 60,
        "BAN_DURATION_SECONDS": 300,
        "MAX_BAN_COUNT": 3,
        "WHITELISTED_IPS": [],
        "PASTEBIN_URL": "https://pastebin.com/raw/JkPHuYjq"  # Default Pastebin URL
    }
    try:
        with open(SETTINGS_FILE, 'r') as f:
            settings = json.load(f)
            if not isinstance(settings, dict):
                raise ValueError("settings.json must contain a JSON object")

            # Validate required settings
            for key in ['RATE_LIMIT_PER_PERIOD', 'RATE_LIMIT_PERIOD_SECONDS', 'BAN_DURATION_SECONDS', 'MAX_BAN_COUNT']:
                if key not in settings:
                    raise ValueError(f"Missing required setting: {key}")
                if not isinstance(settings[key], (int, float)):
                    raise ValueError(f"Setting '{key}' must be a number")

            # Validate optional settings (with defaults)
            if not isinstance(settings.get("WHITELISTED_IPS", []), list):
                raise ValueError("WHITELISTED_IPS must be a list")
            if not isinstance(settings.get("PASTEBIN_URL", ""), str):  #Check Pastebin URL
                raise ValueError("PASTEBIN_URL must be a string")

            # Update default settings with loaded settings.  This handles missing keys.
            default_settings.update(settings)
            return default_settings

    except FileNotFoundError:
        logger.error(f"Error: {SETTINGS_FILE} not found. Using default settings.")
        return default_settings
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"Error loading settings from {SETTINGS_FILE}: {e}. Using default settings.")
        return default_settings

SETTINGS = load_settings()


# --- Rate Limiting Data Structures ---

request_timestamps: Dict[str, Deque[float]] = {}
ban_list: Dict[str, float] = {}
ban_counts: Dict[str, int] = {}

# --- Helper Functions ---

def get_client_ip() -> str:
    """Gets the client's real IP address, handling reverse proxies."""
    if "X-Forwarded-For" in request.headers:
        # Use the first IP address in the X-Forwarded-For header
        return request.headers.getlist("X-Forwarded-For")[0].split(',')[0].strip()
    return request.remote_addr  # Fallback to remote_addr if no X-Forwarded-For


def is_whitelisted(ip_address: str) -> bool:
    """Checks if an IP address is whitelisted."""
    return ip_address in SETTINGS["WHITELISTED_IPS"]


def check_rate_limit(ip_address: str) -> bool:
    """Checks and enforces the rate limit, including banning."""
    now = time.time()

    # --- Ban Check ---
    if ip_address in ban_list:
        if ban_list[ip_address] > now:
            logger.warning(f"IP {ip_address} is currently banned.")
            return False  # Still banned
        else:
            del ban_list[ip_address]  # Remove from ban list (ban expired)
            logger.info(f"IP {ip_address} ban has expired.")

    # --- Initialize Request Timestamps ---
    if ip_address not in request_timestamps:
        request_timestamps[ip_address] = deque()

    # --- Remove Old Timestamps ---
    while request_timestamps[ip_address] and request_timestamps[ip_address][0] < now - SETTINGS["RATE_LIMIT_PERIOD_SECONDS"]:
        request_timestamps[ip_address].popleft()

    # --- Rate Limit Check and Banning ---
    if len(request_timestamps[ip_address]) >= SETTINGS["RATE_LIMIT_PER_PERIOD"]:
        ban_counts[ip_address] = ban_counts.get(ip_address, 0) + 1  # Increment ban count
        logger.warning(f"IP {ip_address} exceeded rate limit.  Ban count: {ban_counts[ip_address]}")

        if ban_counts[ip_address] >= SETTINGS["MAX_BAN_COUNT"]:
            ban_list[ip_address] = now + SETTINGS["BAN_DURATION_SECONDS"]
            logger.warning(f"IP {ip_address} banned for {SETTINGS['BAN_DURATION_SECONDS']} seconds.")
            # Reset ban count after a ban.  Avoids permanent banning after MAX_BAN_COUNT.
            ban_counts[ip_address] = 0
        return False  # Rate limit exceeded

    # --- Record Request ---
    request_timestamps[ip_address].append(now)
    return True  # Request allowed


# --- Pastebin Data Fetching ---
def fetch_data_from_pastebin() -> Dict[str, Any]:
    """Fetches data from Pastebin and handles potential errors."""
    try:
        response = requests.get(SETTINGS["PASTEBIN_URL"])
        response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching data from Pastebin: {e}")
        return {}  # Return an empty dictionary on error
    except ValueError:
        logger.error("Error: Invalid JSON response from Pastebin")
        return {}


# Initialize Pastebin data.  This is done *once* at startup.
DATA = fetch_data_from_pastebin()

# --- RoProxy Data Fetching ---

def fetch_roproxy_data(url: str, *args: str) -> Dict[str, Any]:
    """Fetches data from RoProxy, handling variable arguments and errors."""
    try:
        final_url = url.format(*args)
        response = requests.get(final_url)
        response.raise_for_status()  # Raise an exception for bad status codes
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching data from RoProxy ({final_url}): {e}")
        # Use Flask's `abort` to return an appropriate HTTP error response
        abort(500, description=f"Error fetching data from RoProxy ({final_url}): {e}")
    except ValueError:
        logger.error(f"Invalid JSON response from RoProxy ({final_url})")
        abort(500, description=f"Invalid JSON response from RoProxy ({final_url})")
    except (KeyError, IndexError) as e:  # Catch formatting errors
        logger.error(f"URL formatting error: {e}")
        abort(500, description=f"URL formatting error: {e}")

# --- Route Handlers ---
@app.before_request
def before_request():
    """Handles rate limiting and whitelisting before each request."""
    ip_address = get_client_ip()
    g.ip_address = ip_address  # Store IP address in Flask's `g` object

    if is_whitelisted(ip_address):
        return  # Skip rate limiting for whitelisted IPs

    if not check_rate_limit(ip_address):
        # More descriptive 429 error message
        abort(429, description=f"Too Many Requests.  Please try again later. You are rate limited.  Bans: {ban_counts.get(ip_address, 0)}")


@app.route('/users/v1/<roblox_id>/<product_name>', methods=['GET'])
def get_user_product(roblox_id: str, product_name: str) -> jsonify:
    """Checks if a user owns a specific product."""
    decoded_product_name = urllib.parse.unquote(product_name)
    global DATA
    # Refresh Pastebin data on *every* request for this endpoint.
    DATA = fetch_data_from_pastebin()

    product_data = DATA.get(decoded_product_name)
    if product_data is None:
        abort(404, description=f"Product '{decoded_product_name}' not found")

    user_data = fetch_roproxy_data("https://users.roproxy.com/v1/users/{}", roblox_id)
    username = user_data.get("name")
    if username is None:  # Check for username retrieval failure
        abort(404, description=f"Could not retrieve username for Roblox ID {roblox_id}")

    # Case-insensitive check for ownership
    found = any(user.lower() == username.lower() for user in product_data.values())

    response_data = {
        "username": username,
        "isOwner": found,
        "product": decoded_product_name
    }
    return jsonify(response_data)


# --- Other User Routes (Simplified - use fetch_roproxy_data) ---
@app.route('/users/v1/<roblox_id>/', methods=['GET'])
def get_all_user_data(roblox_id: str) -> jsonify:
    return jsonify(fetch_roproxy_data("https://users.roproxy.com/v1/users/{}", roblox_id))

@app.route('/users/v1/<roblox_id>/description', methods=['GET'])
def get_user_description(roblox_id: str) -> jsonify:
    user_data = fetch_roproxy_data("https://users.roproxy.com/v1/users/{}", roblox_id)
    return jsonify({"description": user_data.get("description", "")})  # Default to empty string

@app.route('/users/v1/<roblox_id>/isBanned', methods=['GET'])
def get_user_is_banned(roblox_id: str) -> jsonify:
    user_data = fetch_roproxy_data("https://users.roproxy.com/v1/users/{}", roblox_id)
    return jsonify({"isBanned": user_data.get("isBanned", False)}) # Default to False

@app.route('/users/v1/<roblox_id>/displayName', methods=['GET'])
def get_user_display_name(roblox_id: str) -> jsonify:
    user_data = fetch_roproxy_data("https://users.roproxy.com/v1/users/{}", roblox_id)
    return jsonify({"displayName": user_data.get("displayName", "")})

@app.route('/users/v1/<roblox_id>/created', methods=['GET'])
def get_user_created_date(roblox_id: str) -> jsonify:
    user_data = fetch_roproxy_data("https://users.roproxy.com/v1/users/{}", roblox_id)
    return jsonify({"created": user_data.get("created", "")})

@app.route('/users/v1/<roblox_id>/externalAppDisplayName', methods=['GET'])
def get_user_external_app_display_name(roblox_id: str) -> jsonify:
    user_data = fetch_roproxy_data("https://users.roproxy.com/v1/users/{}", roblox_id)
    return jsonify({"externalAppDisplayName": user_data.get("externalAppDisplayName")}) # Can return None

@app.route('/users/v1/<roblox_id>/hasVerifiedBadge', methods=['GET'])
def get_user_has_verified_badge(roblox_id: str) -> jsonify:
    user_data = fetch_roproxy_data("https://users.roproxy.com/v1/users/{}", roblox_id)
    return jsonify({"hasVerifiedBadge": user_data.get("hasVerifiedBadge", False)})

@app.route('/users/v1/<roblox_id>/id', methods=['GET'])
def get_user_id(roblox_id: str) -> jsonify:
    user_data = fetch_roproxy_data("https://users.roproxy.com/v1/users/{}", roblox_id)
    return jsonify({"id": user_data.get("id")})

@app.route('/users/v1/<roblox_id>/name', methods=['GET'])
def get_user_name(roblox_id: str) -> jsonify:
    user_data = fetch_roproxy_data("https://users.roproxy.com/v1/users/{}", roblox_id)
    return jsonify({"name": user_data.get("name", "")})

# --- Bundles ---
@app.route('/catalog/v1/assets/<path:asset_data>/bundles', methods=['GET'])
def get_asset_bundles(asset_data: str) -> jsonify:
    return jsonify(fetch_roproxy_data('http://catalog.roproxy.com/v1/assets/{}/bundles', asset_data))

@app.route('/catalog/v1/bundles/<path:bundle_data>/details', methods=['GET'])
@app.route('/bundles/<path:bundle_data>/details', methods=['GET'])  # Combined route
def get_bundle_details(bundle_data: str) -> jsonify:
    return jsonify(fetch_roproxy_data('https://catalog.roproxy.com/v1/bundles/{}/details', bundle_data))

@app.route('/catalog/v1/assets/<path:asset_data>/recommendations', methods=['GET'])
def get_asset_recommendations(asset_data: str) -> jsonify:
    return jsonify(fetch_roproxy_data('https://catalog.roproxy.com/v1/assets/{}/recommendations', asset_data))

@app.route('/users/v1/bundles/<path:user_data>', methods=['GET'])
def get_user_bundles(user_data: str) -> jsonify:
    return jsonify(fetch_roproxy_data('https://catalog.roproxy.com/v1/users/{}/bundles', user_data))

# --- Favorites ---
@app.route('/favorites/v1/assets/<path:asset_data>/count', methods=['GET'])
def get_asset_favorites_count(asset_data: str) -> jsonify:
    return jsonify(fetch_roproxy_data('https://catalog.roproxy.com/v1/favorites/assets/{}/count', asset_data))

@app.route('/favorites/v1/bundles/<path:bundle_data>/count', methods=['GET'])
def get_bundle_favorites_count(bundle_data: str) -> jsonify:
    return jsonify(fetch_roproxy_data('https://catalog.roproxy.com/v1/favorites/bundles/{}/count', bundle_data))

@app.route('/favorites/v1/users/<path:user_data>/assets/<path:asset_data>/favorite', methods=['GET'])
def get_user_asset_favorite(user_data: str, asset_data: str) -> jsonify:
    return jsonify(fetch_roproxy_data('https://catalog.roproxy.com/v1/favorites/users/{}/assets/{}/favorite', user_data, asset_data))

# --- USERS ---
@app.route('/users/v1/search/<path:search_query>', methods=['GET'])
def search_users(search_query: str) -> jsonify:
    return jsonify(fetch_roproxy_data('https://users.roproxy.com/v1/users/search?keyword={}', search_query))

# --- FRIENDS ---
@app.route('/friends/v1/followings/<path:user_data>/count', methods=['GET'])
def get_followings_count(user_data: str) -> jsonify:
    return jsonify(fetch_roproxy_data('https://friends.roproxy.com/v1/users/{}/followers/count', user_data))

# --- Error Handlers ---
@app.errorhandler(404)
def resource_not_found(e):
    """Handles 404 errors with a JSON response."""
    return jsonify(error=str(e)), 404

@app.errorhandler(500)
def internal_server_error(e):
    """Handles 500 errors with a JSON response."""
    return jsonify(error=str(e)), 500

@app.errorhandler(429)
def rate_limit_error(e):
    """Handles 429 (Rate Limit Exceeded) errors, including the IP address."""
    # Include the IP address in the 429 error response
    return jsonify(error=str(e), ip=g.ip_address), 429

# --- Settings File Watcher ---
def watch_settings_file():
    """Reloads settings if the settings file changes."""
    try:
        last_modified = os.stat(SETTINGS_FILE).st_mtime
    except FileNotFoundError:
        logger.warning(f"{SETTINGS_FILE} not found, hot-reloading will not be available.")
        return

    while True:
        time.sleep(5)  # Check every 5 seconds
        try:
            current_modified = os.stat(SETTINGS_FILE).st_mtime
            if current_modified != last_modified:
                logger.info(f"{SETTINGS_FILE} changed. Reloading settings.")
                global SETTINGS
                SETTINGS = load_settings()  # Reload the settings
                last_modified = current_modified
        except FileNotFoundError:
            logger.error(f"{SETTINGS_FILE} was removed during runtime!")
            #  Could potentially exit, or try to re-create default settings.
            break  # Stop watching if the file is gone
        except Exception as e:
            logger.error(f"Error watching settings file: {e}")
            break

if __name__ == '__main__':
    # Start the settings file watcher thread (if the file exists)
    if os.path.exists(SETTINGS_FILE):
        watcher_thread = threading.Thread(target=watch_settings_file, daemon=True)
        watcher_thread.start()

    # Use Flask's built-in development server for debugging
    #  *IMPORTANT*:  Do NOT use `debug=True` in a production environment!
    app.run(debug=True, host='0.0.0.0', port=5000) # Make the server publicly available on port 5000.
    # In production use a production WSGI server like gunicorn or uWSGI.
    # Example using gunicorn:  gunicorn -w 4 -b 0.0.0.0:5000 app:app
    # (-w 4 means 4 worker processes. Adjust as needed)
