from flask import Flask, jsonify, abort, request, g
import urllib.parse
import requests
import time
import json
from collections import deque
import threading
import os

app = Flask(__name__)

# --- Configuration (Editable Settings) ---

SETTINGS_FILE = 'settings.json'

def load_settings():
    """Loads settings from the settings.json file."""
    try:
        with open(SETTINGS_FILE, 'r') as f:
            settings = json.load(f)
            if not isinstance(settings, dict):
                raise ValueError("settings.json must contain a JSON object")
            for key in ['RATE_LIMIT_PER_PERIOD', 'RATE_LIMIT_PERIOD_SECONDS', 'BAN_DURATION_SECONDS', 'MAX_BAN_COUNT']:
                if key not in settings:
                    raise ValueError(f"Missing required setting: {key}")
                if not isinstance(settings[key], (int, float)):
                    raise ValueError(f"Setting '{key}' must be a number")
            if not isinstance(settings.get("WHITELISTED_IPS", []), list):
                raise ValueError("WHITELISTED_IPS must be a list")
            if not isinstance(settings.get("PASTEBIN_URL", ""), str):
                raise ValueError("PASTEBIN_URL must be a string.")

            return settings

    except FileNotFoundError:
        print(f"Error: {SETTINGS_FILE} not found.  Using default settings.")
        return {
            "RATE_LIMIT_PER_PERIOD": 10,
            "RATE_LIMIT_PERIOD_SECONDS": 60,
            "RATE_LIMIT_DURATION_SECONDS": 300,
            "MAX_BAN_COUNT": 3,
            "WHITELISTED_IPS": [],
            "PASTEBIN_URL": "https://pastebin.com/raw/JkPHuYjq"
        }
    except (json.JSONDecodeError, ValueError) as e:
        print(f"Error loading settings from {SETTINGS_FILE}: {e}. Using default settings.")
        return {
            "RATE_LIMIT_PER_PERIOD": 10,
            "RATE_LIMIT_PERIOD_SECONDS": 60,
            "RATE_LIMIT_DURATION_SECONDS": 300,
            "MAX_BAN_COUNT": 3,
            "WHITELISTED_IPS": [],
            "PASTEBIN_URL": "https://pastebin.com/raw/JkPHuYjq"
        }
SETTINGS = load_settings()



# --- Rate Limiting Data Structures ---

request_timestamps = {}
ban_list = {}
ban_counts = {}

# --- Helper Functions ---

def get_client_ip():
    """Gets the client's real IP address."""
    if "X-Forwarded-For" in request.headers:
        return request.headers.getlist("X-Forwarded-For")[0].split(',')[0]
    return request.remote_addr

def is_whitelisted(ip_address):
    """Checks if an IP address is whitelisted."""
    return ip_address in SETTINGS["WHITELISTED_IPS"]

def check_rate_limit(ip_address):
    """Checks and enforces the rate limit."""
    now = time.time()

    if ip_address in ban_list:
        if ban_list[ip_address] > now:
            return False  # Still banned
        else:
            del ban_list[ip_address]  # Remove from ban list

    if ip_address not in request_timestamps:
        request_timestamps[ip_address] = deque()

    while request_timestamps[ip_address] and request_timestamps[ip_address][0] < now - SETTINGS["RATE_LIMIT_PERIOD_SECONDS"]:
        request_timestamps[ip_address].popleft()

    if len(request_timestamps[ip_address]) >= SETTINGS["RATE_LIMIT_PER_PERIOD"]:
        ban_counts[ip_address] = ban_counts.get(ip_address, 0) + 1
        if ban_counts[ip_address] >= SETTINGS["MAX_BAN_COUNT"]:
            ban_list[ip_address] = now + SETTINGS["BAN_DURATION_SECONDS"]
        return False

    request_timestamps[ip_address].append(now)
    return True


# --- Pastebin Data Fetching ---
def fetch_data_from_pastebin():
    """Fetches data from Pastebin."""
    try:
        response = requests.get(SETTINGS["PASTEBIN_URL"])
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching data from Pastebin: {e}")
        return {}
    except ValueError:
        print("Error: Invalid JSON response from Pastebin")
        return {}

DATA = fetch_data_from_pastebin()

# --- RoProxy Data Fetching ---

def fetch_roproxy_data(url, *args):
    """Fetches data from RoProxy, handling variable arguments in the URL."""
    # Construct the URL by replacing placeholders
    try:
        final_url = url.format(*args)
        response = requests.get(final_url)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        abort(500, description=f"Error fetching data from RoProxy ({final_url}): {e}")
    except ValueError:
        abort(500, description=f"Invalid JSON response from RoProxy ({final_url})")
    except KeyError as e:
        abort(500, description=f"Missing URL parameter: {e}")  # Handle missing placeholders
    except IndexError as e:
        abort(500, description=f"Not enough arguments provided for URL format: {e}")


# --- Route Handlers ---
@app.before_request
def before_request():
    """Handles rate limiting and whitelisting."""
    ip_address = get_client_ip()
    g.ip_address = ip_address
    if is_whitelisted(ip_address):
        return
    if not check_rate_limit(ip_address):
        # Modified 429 error response
        abort(429, description=f"Too Many Requests. Please try again later. You are rate limited. Bans: {ban_counts.get(ip_address, 0)}")


@app.route('/users/v1/<roblox_id>/<product_name>', methods=['GET'])
def get_user_product(roblox_id, product_name):
    decoded_product_name = urllib.parse.unquote(product_name)
    global DATA
    DATA = fetch_data_from_pastebin()

    product_data = DATA.get(decoded_product_name)
    if product_data is None:
        abort(404, description=f"Product '{decoded_product_name}' not found")

    user_data = fetch_roproxy_data("https://users.roproxy.com/v1/users/{}", roblox_id)  # Use fetch_roproxy_data
    username = user_data.get("name")
    if username is None:
        abort(404, description=f"Could not retrieve username for Roblox ID {roblox_id}")

    found = False
    for key, user in product_data.items():
        if user.lower() == username.lower():
            found = True
            break

    response_data = {
        "username": username,
        "isOwner": found,
        "product": decoded_product_name
    }
    return jsonify(response_data)

@app.route('/users/v1/<roblox_id>/', methods=['GET'])
def get_all_user_data(roblox_id):
    user_data = fetch_roproxy_data("https://users.roproxy.com/v1/users/{}", roblox_id) # Use fetch_roproxy_data
    return jsonify(user_data)

@app.route('/users/v1/<roblox_id>/description', methods=['GET'])
def get_user_description(roblox_id):
    user_data = fetch_roproxy_data("https://users.roproxy.com/v1/users/{}", roblox_id) # Use fetch_roproxy_data
    return jsonify({"description": user_data.get("description", "")})

@app.route('/users/v1/<roblox_id>/isBanned', methods=['GET'])
def get_user_is_banned(roblox_id):
    user_data = fetch_roproxy_data("https://users.roproxy.com/v1/users/{}", roblox_id) # Use fetch_roproxy_data
    return jsonify({"isBanned": user_data.get("isBanned", False)})

@app.route('/users/v1/<roblox_id>/displayName', methods=['GET'])
def get_user_display_name(roblox_id):
    user_data = fetch_roproxy_data("https://users.roproxy.com/v1/users/{}", roblox_id) # Use fetch_roproxy_data
    return jsonify({"displayName": user_data.get("displayName", "")})

@app.route('/users/v1/<roblox_id>/created', methods=['GET'])
def get_user_created_date(roblox_id):
    user_data = fetch_roproxy_data("https://users.roproxy.com/v1/users/{}", roblox_id) # Use fetch_roproxy_data
    return jsonify({"created": user_data.get("created", "")})

@app.route('/users/v1/<roblox_id>/externalAppDisplayName', methods=['GET'])
def get_user_external_app_display_name(roblox_id):
    user_data = fetch_roproxy_data("https://users.roproxy.com/v1/users/{}", roblox_id) # Use fetch_roproxy_data
    return jsonify({"externalAppDisplayName": user_data.get("externalAppDisplayName")})

@app.route('/users/v1/<roblox_id>/hasVerifiedBadge', methods=['GET'])
def get_user_has_verified_badge(roblox_id):
    user_data = fetch_roproxy_data("https://users.roproxy.com/v1/users/{}", roblox_id) # Use fetch_roproxy_data
    return jsonify({"hasVerifiedBadge": user_data.get("hasVerifiedBadge", False)})

@app.route('/users/v1/<roblox_id>/id', methods=['GET'])
def get_user_id(roblox_id):
    user_data = fetch_roproxy_data("https://users.roproxy.com/v1/users/{}", roblox_id) # Use fetch_roproxy_data
    return jsonify({"id": user_data.get("id")})

@app.route('/users/v1/<roblox_id>/name', methods=['GET'])
def get_user_name(roblox_id):
    user_data = fetch_roproxy_data("https://users.roproxy.com/v1/users/{}", roblox_id) # Use fetch_roproxy_data
    return jsonify({"name": user_data.get("name", "")})

# --- BUNDLES ---
@app.route('/catalog/v1/assets/<path:asset_data>/bundles', methods=['GET'])
def get_asset_bundles(asset_data):
    return jsonify(fetch_roproxy_data('http://catalog.roproxy.com/v1/assets/{}/bundles', asset_data))

@app.route('/catalog/v1/bundles/<path:bundle_data>/details', methods=['GET'])
def get_bundle_details_v1(bundle_data):
    return jsonify(fetch_roproxy_data('https://catalog.roproxy.com/v1/bundles/{}/details', bundle_data))

@app.route('/bundles/<path:bundle_data>/details', methods=['GET'])
def get_bundle_details_v2(bundle_data):
    return jsonify(fetch_roproxy_data('https://catalog.roproxy.com/v1/bundles/{}/details', bundle_data))

@app.route('/catalog/v1/assets/<path:asset_data>/recommendations', methods=['GET'])
def get_asset_recommendations(asset_data):
    return jsonify(fetch_roproxy_data('https://catalog.roproxy.com/v1/bundles/{}/details', asset_data)) #Corrected URL

@app.route('/users/v1/bundles/<path:user_data>', methods=['GET'])
def get_user_bundles(user_data):
    return jsonify(fetch_roproxy_data('https://catalog.roproxy.com/v1/users/{}/bundles', user_data))

# --- FAVORITES ---
@app.route('/favorites/v1/assets/<path:asset_data>/count', methods=['GET'])
def get_asset_favorites_count(asset_data):
    return jsonify(fetch_roproxy_data('https://catalog.roproxy.com/v1/favorites/assets/{}/count', asset_data))

@app.route('/favorites/v1/bundles/<path:bundle_data>/count', methods=['GET'])
def get_bundle_favorites_count(bundle_data):
    return jsonify(fetch_roproxy_data('https://catalog.roproxy.com/v1/favorites/bundles/{}/count', bundle_data))

@app.route('/favorites/v1/users/<path:user_data>/assets/<path:asset_data>/favorite', methods=['GET'])
def get_user_asset_favorite(user_data, asset_data):
    return jsonify(fetch_roproxy_data('https://catalog.roproxy.com/v1/favorites/users/{}/assets/{}/favorite', user_data, asset_data))

# --- USERS ---
@app.route('/users/v1/search/<path:search_query>', methods=['GET'])
def search_users(search_query):
    return jsonify(fetch_roproxy_data('https://users.roproxy.com/v1/users/search?keyword={}', search_query))

# --- FRIENDS ---
@app.route('/friends/v1/followings/<path:user_data>/count', methods=['GET'])
def get_followings_count(user_data):
    return jsonify(fetch_roproxy_data('https://friends.roproxy.com/v1/users/{}/followers/count', user_data))  # Corrected URL


@app.errorhandler(404)
def resource_not_found(e):
    return jsonify(error=str(e)), 404

@app.errorhandler(500)
def internal_server_error(e):
    return jsonify(error=str(e)), 500

@app.errorhandler(429)
def rate_limit_error(e):
    # Include the IP address in the 429 error response
    return jsonify(error=str(e), ip=g.ip_address), 429

def watch_settings_file():
    """Reloads settings if the settings file changes."""
    last_modified = os.stat(SETTINGS_FILE).st_mtime
    while True:
        time.sleep(5)
        current_modified = os.stat(SETTINGS_FILE).st_mtime
        if current_modified != last_modified:
            print(f"{SETTINGS_FILE} changed. Reloading settings.")
            global SETTINGS
            SETTINGS = load_settings()
            last_modified = current_modified

if __name__ == '__main__':
    if os.path.exists(SETTINGS_FILE):
        watcher_thread = threading.Thread(target=watch_settings_file, daemon=True)
        watcher_thread.start()
    # app.run(debug=True)
    pass
