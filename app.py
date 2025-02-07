from flask import Flask, jsonify, abort, request, g
import urllib.parse
import requests
import time
import json
from collections import deque
import threading
import os  # Import the os module


app = Flask(__name__)

# --- Configuration (Editable Settings) ---

# Load settings from a JSON file
SETTINGS_FILE = 'settings.json'

def load_settings():
    """Loads settings from the settings.json file."""
    try:
        with open(SETTINGS_FILE, 'r') as f:
            settings = json.load(f)
            # Validate settings (important for security)
            if not isinstance(settings, dict):
                raise ValueError("settings.json must contain a JSON object")
            for key in ['RATE_LIMIT_PER_PERIOD', 'RATE_LIMIT_PERIOD_SECONDS', 'BAN_DURATION_SECONDS', 'MAX_BAN_COUNT']:
                if key not in settings:
                    raise ValueError(f"Missing required setting: {key}")
                if not isinstance(settings[key], (int, float)):  #allow float for sub-second periods.
                    raise ValueError(f"Setting '{key}' must be a number")
            if not isinstance(settings.get("WHITELISTED_IPS", []), list):
                 raise ValueError("WHITELISTED_IPS must be a list")
            if not isinstance(settings.get("PASTEBIN_URL", ""), str):
                 raise ValueError("PASTEBIN_URL must be a string.")

            return settings

    except FileNotFoundError:
        print(f"Error: {SETTINGS_FILE} not found.  Using default settings.")
        return {  # Default settings (fallback)
            "RATE_LIMIT_PER_PERIOD": 10,
            "RATE_LIMIT_PERIOD_SECONDS": 60,
            "BAN_DURATION_SECONDS": 300,
            "MAX_BAN_COUNT": 3,
            "WHITELISTED_IPS": [],
            "PASTEBIN_URL": "https://pastebin.com/raw/JkPHuYjq" #  Default Pastebin URL
        }
    except (json.JSONDecodeError, ValueError) as e:
        print(f"Error loading settings from {SETTINGS_FILE}: {e}. Using default settings.")
        return {  # Default settings (fallback) - same as above
            "RATE_LIMIT_PER_PERIOD": 10,
            "RATE_LIMIT_PERIOD_SECONDS": 60,
            "BAN_DURATION_SECONDS": 300,
            "MAX_BAN_COUNT": 3,
            "WHITELISTED_IPS": [],
            "PASTEBIN_URL": "https://pastebin.com/raw/JkPHuYjq"
        }
SETTINGS = load_settings()



# --- Rate Limiting Data Structures ---

# Use a deque for efficient queue management (FIFO)
request_timestamps = {}  # Key: IP address, Value: deque of request timestamps
ban_list = {}  # Key: IP address, Value: unban timestamp
ban_counts = {} # Key: IP address, Value: number of times banned.

# --- Helper Functions ---

def get_client_ip():
    """Gets the client's real IP address, handling proxies correctly."""
    if "X-Forwarded-For" in request.headers:
        # Use the first IP in the X-Forwarded-For list (the client's IP)
        return request.headers.getlist("X-Forwarded-For")[0].split(',')[0]
    return request.remote_addr

def is_whitelisted(ip_address):
    """Checks if an IP address is whitelisted."""
    return ip_address in SETTINGS["WHITELISTED_IPS"]

def check_rate_limit(ip_address):
    """Checks and enforces the rate limit for a given IP address.
       Returns True if the request should be allowed, False if it should be blocked.
    """

    now = time.time()

    if ip_address in ban_list:
        if ban_list[ip_address] > now:
            return False  # Still banned
        else:
            del ban_list[ip_address]  # Remove from ban list

    if ip_address not in request_timestamps:
        request_timestamps[ip_address] = deque()

    # Remove timestamps older than the rate limit period
    while request_timestamps[ip_address] and request_timestamps[ip_address][0] < now - SETTINGS["RATE_LIMIT_PERIOD_SECONDS"]:
        request_timestamps[ip_address].popleft()

    if len(request_timestamps[ip_address]) >= SETTINGS["RATE_LIMIT_PER_PERIOD"]:
        # Rate limit exceeded.  Ban the IP if necessary
        ban_counts[ip_address] = ban_counts.get(ip_address, 0) + 1
        if ban_counts[ip_address] >= SETTINGS["MAX_BAN_COUNT"]:
             ban_list[ip_address] = now + SETTINGS["BAN_DURATION_SECONDS"]
        return False # Block request

    request_timestamps[ip_address].append(now)
    return True  # Allow request


# --- Pastebin Data Fetching ---
def fetch_data_from_pastebin():
    """Fetches data from Pastebin, handling errors robustly."""
    try:
        response = requests.get(SETTINGS["PASTEBIN_URL"])
        response.raise_for_status()  # Raise HTTPError for bad requests (4xx or 5xx)
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching data from Pastebin: {e}")
        return {}  # Return an empty dictionary on error
    except ValueError:
        print("Error: Invalid JSON response from Pastebin")
        return {}

# Load data at startup.  Good practice to make this a global variable.
DATA = fetch_data_from_pastebin()

# --- RoProxy Data Fetching ---

def fetch_roproxy_data(roblox_id):
    """Fetches user data from RoProxy, handling errors and returning appropriate status codes."""
    roproxy_url = f"https://users.roproxy.com/v1/users/{roblox_id}"
    try:
        response = requests.get(roproxy_url)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        abort(500, description=f"Error fetching data from RoProxy: {e}")
    except ValueError:
        abort(500, description="Invalid JSON response from RoProxy")

# --- Route Handlers ---
@app.before_request
def before_request():
    """Executed before each request. Handles rate limiting and whitelisting."""
    ip_address = get_client_ip()
    g.ip_address = ip_address # Store IP for logging/debugging in other routes.
    if is_whitelisted(ip_address):
        return  # Skip rate limiting for whitelisted IPs
    if not check_rate_limit(ip_address):
        abort(429, description=f"Too Many Requests. Please try again later. You are rate limited. Bans: {ban_counts.get(ip_address, 0)}")


@app.route('/users/v1/<roblox_id>/<product_name>', methods=['GET'])
def get_user_product(roblox_id, product_name):
    """Handles GET requests for product ownership, with improved error handling."""
    decoded_product_name = urllib.parse.unquote(product_name)

    # Fetch latest data
    global DATA
    DATA = fetch_data_from_pastebin()


    product_data = DATA.get(decoded_product_name)
    if product_data is None:
        abort(404, description=f"Product '{decoded_product_name}' not found")

    user_data = fetch_roproxy_data(roblox_id)
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


# --- Other Routes (Simplified - no changes needed) ---
@app.route('/users/v1/<roblox_id>/', methods=['GET'])
def get_all_user_data(roblox_id):
    user_data = fetch_roproxy_data(roblox_id)
    return jsonify(user_data)

@app.route('/users/v1/<roblox_id>/description', methods=['GET'])
def get_user_description(roblox_id):
    user_data = fetch_roproxy_data(roblox_id)
    return jsonify({"description": user_data.get("description", "")})

@app.route('/users/v1/<roblox_id>/isBanned', methods=['GET'])
def get_user_is_banned(roblox_id):
    user_data = fetch_roproxy_data(roblox_id)
    return jsonify({"isBanned": user_data.get("isBanned", False)})

@app.route('/users/v1/<roblox_id>/displayName', methods=['GET'])
def get_user_display_name(roblox_id):
    user_data = fetch_roproxy_data(roblox_id)
    return jsonify({"displayName": user_data.get("displayName", "")})

@app.route('/users/v1/<roblox_id>/created', methods=['GET'])
def get_user_created_date(roblox_id):
    user_data = fetch_roproxy_data(roblox_id)
    return jsonify({"created": user_data.get("created", "")})

@app.route('/users/v1/<roblox_id>/externalAppDisplayName', methods=['GET'])
def get_user_external_app_display_name(roblox_id):
    user_data = fetch_roproxy_data(roblox_id)
    return jsonify({"externalAppDisplayName": user_data.get("externalAppDisplayName")})

@app.route('/users/v1/<roblox_id>/hasVerifiedBadge', methods=['GET'])
def get_user_has_verified_badge(roblox_id):
    user_data = fetch_roproxy_data(roblox_id)
    return jsonify({"hasVerifiedBadge": user_data.get("hasVerifiedBadge", False)})

@app.route('/users/v1/<roblox_id>/id', methods=['GET'])
def get_user_id(roblox_id):
    user_data = fetch_roproxy_data(roblox_id)
    return jsonify({"id": user_data.get("id")})

@app.route('/users/v1/<roblox_id>/name', methods=['GET'])
def get_user_name(roblox_id):
    user_data = fetch_roproxy_data(roblox_id)
    return jsonify({"name": user_data.get("name", "")})

# --- Error Handlers ---
@app.errorhandler(404)
def resource_not_found(e):
    return jsonify(error=str(e)), 404

@app.errorhandler(500)
def internal_server_error(e):
    return jsonify(error=str(e)), 500

@app.errorhandler(429)  # Catch the Too Many Requests error
def rate_limit_error(e):
    return jsonify(error=str(e)), 429
# --- Reload Settings on File Change (Optional) ---
def watch_settings_file():
    """Reloads settings if the settings file changes."""
    last_modified = os.stat(SETTINGS_FILE).st_mtime
    while True:
        time.sleep(5)  # Check every 5 seconds
        current_modified = os.stat(SETTINGS_FILE).st_mtime
        if current_modified != last_modified:
            print(f"{SETTINGS_FILE} changed. Reloading settings.")
            global SETTINGS
            SETTINGS = load_settings()  # Reload
            last_modified = current_modified

# --- Main Execution ---

if __name__ == '__main__':
    # Start settings file watcher in a separate thread
    if os.path.exists(SETTINGS_FILE):  #Only start watcher if file exists.
        watcher_thread = threading.Thread(target=watch_settings_file, daemon=True)
        watcher_thread.start()
    # app.run(debug=True) #  Only for local development!  Use a production server for deployment.
    pass
