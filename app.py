from flask import Flask, jsonify, abort, request
import urllib.parse
import requests

app = Flask(__name__)

# Fetch data from Pastebin
def fetch_data_from_pastebin():
    try:
        response = requests.get("https://pastebin.com/raw/JkPHuYjq")
        response.raise_for_status()  # Raise HTTPError for bad requests (4xx or 5xx)
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching data from Pastebin: {e}")
        return {}  # Return an empty dictionary on error
    except ValueError:
        print("Error: Invalid JSON response from Pastebin")
        return {}

# Load data at startup.  This is fine for this example, but for a
# production app, you'd want to handle updates/reloading.
DATA = fetch_data_from_pastebin()


def fetch_roproxy_data(roblox_id):
    """Fetches user data from RoProxy."""
    roproxy_url = f"https://users.roproxy.com/v1/users/{roblox_id}"
    try:
        response = requests.get(roproxy_url)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        abort(500, description=f"Error fetching data from RoProxy: {e}")
    except ValueError:
        abort(500, description="Invalid JSON response from RoProxy")


@app.route('/users/v1/<roblox_id>/<product_name>', methods=['GET'])
def get_user_product(roblox_id, product_name):
    """
    Handles GET requests to the API.

    Args:
        roblox_id (str):  The Roblox user ID.
        product_name (str): The name of the product (with spaces encoded as %20).

    Returns:
        JSON: A JSON response containing user and product information.
              If the user owns the product, isOwner will be True, otherwise False.
              Returns 404 Not Found in case of errors.
    """
    decoded_product_name = urllib.parse.unquote(product_name)

    # Fetch latest data (consider caching to reduce Pastebin requests)
    global DATA  # Access the global DATA variable
    DATA = fetch_data_from_pastebin() #Update our data.

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
    return jsonify({"externalAppDisplayName": user_data.get("externalAppDisplayName")}) #Can be null

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
@app.route('/users/v1/<roblox_id>', methods=['GET'])
def get_all_user_data(roblox_id):
     user_data = fetch_roproxy_data(roblox_id)
     return jsonify(user_data)


@app.errorhandler(404)
def resource_not_found(e):
    return jsonify(error=str(e)), 404

@app.errorhandler(500)
def internal_server_error(e):
    return jsonify(error=str(e)), 500

if __name__ == '__main__':
    # app.run(debug=True)    # Only for local development
    pass  #Heroku configuration
