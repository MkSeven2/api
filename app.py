from flask import Flask, jsonify, abort, request, g
import urllib.parse
import requests
import time
import json
from collections import deque
import threading
import os
import mysql.connector  # Import the MySQL connector
from functools import wraps

app = Flask(__name__)

# --- Configuration (Editable Settings) ---

SETTINGS_FILE = 'settings.json'

def load_settings():
    """Loads settings from the settings.json file."""
    try:
        with open(SETTINGS_FILE, 'r') as f:
            settings = json.load(f)
            # Validate the settings
            required_keys = ['RATE_LIMIT_PER_PERIOD', 'RATE_LIMIT_PERIOD_SECONDS',
                             'BAN_DURATION_SECONDS', 'MAX_BAN_COUNT', 'DB_HOST',
                             'DB_USER', 'DB_PASSWORD', 'DB_NAME', 'API_TOKEN_LENGTH']
            for key in required_keys:
                if key not in settings:
                    raise ValueError(f"Missing required setting: {key}")
            return settings
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as e:
        print(f"Error loading settings: {e}.  Exiting.")
        exit(1)  # Exit if settings are invalid.

SETTINGS = load_settings()



# --- Database Connection ---
def get_db():
    """Gets a database connection.  Creates the connection if it doesn't exist."""
    if 'db' not in g:
        try:
            g.db = mysql.connector.connect(
                host=SETTINGS['DB_HOST'],
                user=SETTINGS['DB_USER'],
                password=SETTINGS['DB_PASSWORD'],
                database=SETTINGS['DB_NAME']
            )
            g.db.autocommit = True # set autocommit
        except mysql.connector.Error as err:
            print(f"Error connecting to database: {err}")
            abort(500, description="Failed to connect to the database.")
            return None # return none is necesary othervise you get error
    return g.db

@app.teardown_appcontext
def close_db(error):
    """Closes the database connection at the end of the request."""
    if 'db' in g:
        g.db.close()


# --- API Token Authentication ---

def generate_api_token(length=24):
    """Generates a random API token."""
    import secrets
    return secrets.token_urlsafe(length)

def create_api_tokens_table():
    """Creates the API tokens table if it doesn't exist."""
    db = get_db()
    if db is None:
        return
    cursor = db.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS api_tokens (
            id INT AUTO_INCREMENT PRIMARY KEY,
            token VARCHAR(255) UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_used TIMESTAMP NULL,
            is_valid BOOLEAN DEFAULT TRUE,
            user_id VARCHAR(255),
            description VARCHAR(255)
        )
    """)
    db.commit()  # commit is necesary.

def is_valid_api_token(token):
    """Checks if an API token is valid."""
    db = get_db()
    if db is None:
        return False
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM api_tokens WHERE token = %s AND is_valid = TRUE", (token,))
    token_data = cursor.fetchone()
    if token_data:
        # Update last_used timestamp
        cursor.execute("UPDATE api_tokens SET last_used = CURRENT_TIMESTAMP WHERE id = %s", (token_data['id'],))
        db.commit()
        return True
    return False

def require_api_token(f):
    """Decorator to require a valid API token."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'API-TOKEN' not in request.headers:
            abort(401, description="API token required.")
        token = request.headers['API-TOKEN']
        if not is_valid_api_token(token):
            abort(403, description="Invalid API token.")
        return f(*args, **kwargs)
    return decorated_function


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
    db = get_db()
    if db is None:
        return False
    cursor = db.cursor()

    cursor.execute("SELECT ip FROM whitelisted_ips")
    whitelisted_ips_from_db = [row[0] for row in cursor.fetchall()]
    return ip_address in SETTINGS.get("WHITELISTED_IPS", []) or ip_address in whitelisted_ips_from_db



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
    if 'PASTEBIN_URL' not in SETTINGS:
        print("Warning: PASTEBIN_URL not set in settings.  Pastebin integration disabled.")
        return {}
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

# DATA = fetch_data_from_pastebin()  # Don't fetch here. Fetch on demand.


# --- RoProxy Data Fetching ---

def fetch_roproxy_data(url, *args):
    """Fetches data from RoProxy, handling variable arguments in the URL."""
    try:
        final_url = url.format(*args)
        response = requests.get(final_url)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        abort(500, description=f"Error fetching data from RoProxy ({final_url}): {e}")
    except ValueError:
        abort(500, description=f"Invalid JSON response from RoProxy ({final_url})")
    except (KeyError, IndexError) as e:
        abort(500, description=f"URL formatting error: {e}")


# --- Route Handlers ---
@app.before_request
def before_request():
    """Handles rate limiting, whitelisting and API token check."""
    ip_address = get_client_ip()
    g.ip_address = ip_address

    if is_whitelisted(ip_address):
        return

    if not check_rate_limit(ip_address):
        abort(429, description=f"Too Many Requests. Please try again later. You are rate limited. Bans: {ban_counts.get(ip_address, 0)}")


@app.route('/users/v1/<roblox_id>/<product_name>', methods=['GET'])
@require_api_token  # Apply the API token decorator
def get_user_product(roblox_id, product_name):
    decoded_product_name = urllib.parse.unquote(product_name)
    DATA = fetch_data_from_pastebin()  # Fetch on demand

    if not DATA:
        abort(500, description="Pastebin data is unavailable.")

    product_data = DATA.get(decoded_product_name)
    if product_data is None:
        abort(404, description=f"Product '{decoded_product_name}' not found")

    user_data = fetch_roproxy_data("https://users.roproxy.com/v1/users/{}", roblox_id)
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
@require_api_token
def get_all_user_data(roblox_id):
    user_data = fetch_roproxy_data("https://users.roproxy.com/v1/users/{}", roblox_id)
    return jsonify(user_data)

@app.route('/users/v1/<roblox_id>/description', methods=['GET'])
@require_api_token
def get_user_description(roblox_id):
    user_data = fetch_roproxy_data("https://users.roproxy.com/v1/users/{}", roblox_id)
    return jsonify({"description": user_data.get("description", "")})

@app.route('/users/v1/<roblox_id>/isBanned', methods=['GET'])
@require_api_token
def get_user_is_banned(roblox_id):
    user_data = fetch_roproxy_data("https://users.roproxy.com/v1/users/{}", roblox_id)
    return jsonify({"isBanned": user_data.get("isBanned", False)})

@app.route('/users/v1/<roblox_id>/displayName', methods=['GET'])
@require_api_token
def get_user_display_name(roblox_id):
    user_data = fetch_roproxy_data("https://users.roproxy.com/v1/users/{}", roblox_id)
    return jsonify({"displayName": user_data.get("displayName", "")})

@app.route('/users/v1/<roblox_id>/created', methods=['GET'])
@require_api_token
def get_user_created_date(roblox_id):
    user_data = fetch_roproxy_data("https://users.roproxy.com/v1/users/{}", roblox_id)
    return jsonify({"created": user_data.get("created", "")})

@app.route('/users/v1/<roblox_id>/externalAppDisplayName', methods=['GET'])
@require_api_token
def get_user_external_app_display_name(roblox_id):
    user_data = fetch_roproxy_data("https://users.roproxy.com/v1/users/{}", roblox_id)
    return jsonify({"externalAppDisplayName": user_data.get("externalAppDisplayName")})

@app.route('/users/v1/<roblox_id>/hasVerifiedBadge', methods=['GET'])
@require_api_token
def get_user_has_verified_badge(roblox_id):
    user_data = fetch_roproxy_data("https://users.roproxy.com/v1/users/{}", roblox_id)
    return jsonify({"hasVerifiedBadge": user_data.get("hasVerifiedBadge", False)})

@app.route('/users/v1/<roblox_id>/id', methods=['GET'])
@require_api_token
def get_user_id(roblox_id):
    user_data = fetch_roproxy_data("https://users.roproxy.com/v1/users/{}", roblox_id)
    return jsonify({"id": user_data.get("id")})

@app.route('/users/v1/<roblox_id>/name', methods=['GET'])
@require_api_token
def get_user_name(roblox_id):
    user_data = fetch_roproxy_data("https://users.roproxy.com/v1/users/{}", roblox_id)
    return jsonify({"name": user_data.get("name", "")})

# --- BUNDLES ---
@app.route('/catalog/v1/assets/<path:asset_data>/bundles', methods=['GET'])
@require_api_token
def get_asset_bundles(asset_data):
    return jsonify(fetch_roproxy_data('http://catalog.roproxy.com/v1/assets/{}/bundles', asset_data))

@app.route('/catalog/v1/bundles/<path:bundle_data>/details', methods=['GET'])
@require_api_token
def get_bundle_details_v1(bundle_data):
    return jsonify(fetch_roproxy_data('https://catalog.roproxy.com/v1/bundles/{}/details', bundle_data))

@app.route('/bundles/<path:bundle_data>/details', methods=['GET'])
@require_api_token
def get_bundle_details_v2(bundle_data):
    return jsonify(fetch_roproxy_data('https://catalog.roproxy.com/v1/bundles/{}/details', bundle_data))

@app.route('/catalog/v1/assets/<path:asset_data>/recommendations', methods=['GET'])
@require_api_token
def get_asset_recommendations(asset_data):
    return jsonify(fetch_roproxy_data('https://catalog.roproxy.com/v1/bundles/{}/details', asset_data))

@app.route('/users/v1/bundles/<path:user_data>', methods=['GET'])
@require_api_token
def get_user_bundles(user_data):
    return jsonify(fetch_roproxy_data('https://catalog.roproxy.com/v1/users/{}/bundles', user_data))

# --- FAVORITES ---
@app.route('/favorites/v1/assets/<path:asset_data>/count', methods=['GET'])
@require_api_token
def get_asset_favorites_count(asset_data):
    return jsonify(fetch_roproxy_data('https://catalog.roproxy.com/v1/favorites/assets/{}/count', asset_data))

@app.route('/favorites/v1/bundles/<path:bundle_data>/count', methods=['GET'])
@require_api_token
def get_bundle_favorites_count(bundle_data):
    return jsonify(fetch_roproxy_data('https://catalog.roproxy.com/v1/favorites/bundles/{}/count', bundle_data))

@app.route('/favorites/v1/users/<path:user_data>/assets/<path:asset_data>/favorite', methods=['GET'])
@require_api_token
def get_user_asset_favorite(user_data, asset_data):
    return jsonify(fetch_roproxy_data('https://catalog.roproxy.com/v1/favorites/users/{}/assets/{}/favorite', user_data, asset_data))

# --- USERS ---
@app.route('/users/v1/search/<path:search_query>', methods=['GET'])
@require_api_token
def search_users(search_query):
    return jsonify(fetch_roproxy_data('https://users.roproxy.com/v1/users/search?keyword={}', search_query))

# --- FRIENDS ---
@app.route('/friends/v1/followings/<path:user_data>/count', methods=['GET'])
@require_api_token
def get_followings_count(user_data):
    return jsonify(fetch_roproxy_data('https://friends.roproxy.com/v1/users/{}/followers/count', user_data))


# --- Error Handlers ---
@app.errorhandler(404)
def resource_not_found(e):
    return jsonify(error=str(e)), 404

@app.errorhandler(500)
def internal_server_error(e):
    return jsonify(error=str(e)), 500

@app.errorhandler(429)
def rate_limit

def rate_limit_error(e):
    # Include the IP address in the 429 error response
    return jsonify(error=str(e), ip=g.ip_address), 429

@app.errorhandler(401)
def unauthorized_error(e):
    return jsonify(error=str(e)), 401

@app.errorhandler(403)
def forbidden_error(e):
    return jsonify(error=str(e)), 403
# --- Settings File Watcher ---

def watch_settings_file():
    """Reloads settings if the settings file changes."""
    try:
        last_modified = os.stat(SETTINGS_FILE).st_mtime
    except FileNotFoundError:
        print(f"Error: {SETTINGS_FILE} not found. Settings watcher will not work.")
        return

    while True:
        time.sleep(5)
        try:
            current_modified = os.stat(SETTINGS_FILE).st_mtime
            if current_modified != last_modified:
                print(f"{SETTINGS_FILE} changed. Reloading settings.")
                global SETTINGS
                SETTINGS = load_settings()
                last_modified = current_modified
        except FileNotFoundError:
            print(f"Error: {SETTINGS_FILE} was deleted. Settings watcher will not work.")
            break #exit the loop in this case
        except Exception as e:
            print(f"An unexpected error occurred in watch_settings_file: {e}")


# --- API Token Management Routes (Example) ---
@app.route('/manage/api_tokens', methods=['GET'])
@require_api_token
def list_api_tokens():
    """Lists all API tokens (for admin or token owner)."""
    db = get_db()
    if db is None:
        return jsonify({"error":"Database is not available"}), 500
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT id, token, created_at, last_used, is_valid, user_id, description FROM api_tokens")
    tokens = cursor.fetchall()
    return jsonify(tokens)


@app.route('/manage/api_tokens', methods=['POST'])
@require_api_token # Require a token to even *create* tokens.  Adjust as needed.
def create_api_token():
    """Creates a new API token."""
    db = get_db()
    if db is None:
        return jsonify({"error":"Database is not available"}), 500
    cursor = db.cursor()
    data = request.get_json()
    user_id = data.get('user_id') if data else None # Get User Id from request body, could be null
    description = data.get('description') if data else None # Get description from request body

    if not user_id: #Check if is user_id
        return jsonify({'error': 'user_id is required'}), 400
    if not description: #Check if is description
        return jsonify({'error': 'description is required'}), 400

    new_token = generate_api_token(SETTINGS['API_TOKEN_LENGTH'])
    try:
        cursor.execute("INSERT INTO api_tokens (token, user_id, description) VALUES (%s, %s, %s)", (new_token, user_id, description))
        db.commit()
        return jsonify({'token': new_token, 'message': 'API token created successfully.'}), 201
    except mysql.connector.IntegrityError:
        # Handle duplicate token (unlikely, but good to handle)
        return jsonify({'error': 'Token collision.  Please try again.'}), 500

@app.route('/manage/api_tokens/<token>', methods=['DELETE'])
@require_api_token
def revoke_api_token(token):
    """Revokes (invalidates) an API token."""
    db = get_db()
    if db is None:
        return jsonify({"error":"Database is not available"}), 500
    cursor = db.cursor()
    cursor.execute("UPDATE api_tokens SET is_valid = FALSE WHERE token = %s", (token,))
    db.commit()
    if cursor.rowcount == 0:  # Check if any rows were affected
        return jsonify({'error': 'Token not found or already invalid.'}), 404
    return jsonify({'message': 'API token revoked successfully.'}), 200

@app.route('/manage/whitelist', methods=['POST'])
@require_api_token
def add_to_whitelist():
    """Adds an IP address to the whitelist (stored in the database)."""
    db = get_db()
    if db is None:
        return jsonify({"error":"Database is not available"}), 500
    cursor = db.cursor()
    data = request.get_json()

    if not data or 'ip' not in data:
        return jsonify({'error': 'Missing IP address in request body.'}), 400
    ip_address = data['ip']

    try:
        cursor.execute("INSERT INTO whitelisted_ips (ip) VALUES (%s)", (ip_address,))
        db.commit()
        return jsonify({'message': f'IP address {ip_address} added to whitelist.'}), 201
    except mysql.connector.IntegrityError:
        return jsonify({'error': f'IP address {ip_address} already whitelisted.'}), 409 # Conflict

@app.route('/manage/whitelist/<ip_address>', methods=['DELETE'])
@require_api_token
def remove_from_whitelist(ip_address):
    """Removes an IP address from the whitelist."""
    db = get_db()
    if db is None:
        return jsonify({"error":"Database is not available"}), 500
    cursor = db.cursor()
    cursor.execute("DELETE FROM whitelisted_ips WHERE ip = %s", (ip_address,))
    db.commit()
    if cursor.rowcount == 0:
        return jsonify({'error': 'IP address not found in whitelist.'}), 404
    return jsonify({'message': f'IP address {ip_address} removed from whitelist.'}), 200

@app.route('/manage/whitelist', methods=['GET'])
@require_api_token
def get_whitelist():
    """Gets all whitelisted IP addresses."""
    db = get_db()
    if db is None:
        return jsonify({"error":"Database is not available"}), 500
    cursor = db.cursor()
    cursor.execute("SELECT ip FROM whitelisted_ips")
    whitelisted_ips = [row[0] for row in cursor.fetchall()]
    return jsonify({'whitelisted_ips': whitelisted_ips}), 200



# --- Initialization ---
if __name__ == '__main__':
    create_api_tokens_table()  # Create the table on startup
    db = get_db() #Try connect to database
    if db is None:
        exit(1)
    cursor = db.cursor()

    # Whitelist table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS whitelisted_ips (
            id INT AUTO_INCREMENT PRIMARY KEY,
            ip VARCHAR(45) UNIQUE NOT NULL
        )
    """)

    if os.path.exists(SETTINGS_FILE):
        watcher_thread = threading.Thread(target=watch_settings_file, daemon=True)
        watcher_thread.start()
    app.run(debug=True) #Set debug True for Development
