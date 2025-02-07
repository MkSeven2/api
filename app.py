from flask import Flask, jsonify, abort, request
from data import DATA  # Assuming data.py contains your product data
import urllib.parse
import requests  # Import the requests library

app = Flask(__name__)

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
    decoded_product_name = urllib.parse.unquote(product_name)  # Decode the URL

    # Check if the product exists in the data.  Use .get() for safe access.
    product_data = DATA.get(decoded_product_name)
    if product_data is None:
        abort(404, description=f"Product '{decoded_product_name}' not found")

    # Construct the URL for the roproxy request
    roproxy_url = f"https://users.roproxy.com/v1/users/{roblox_id}"

    try:
        # Make the GET request to roproxy
        response = requests.get(roproxy_url)
        response.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)
        user_data = response.json()  # Parse the JSON response

        # Extract the username from the roproxy response
        username = user_data.get("name")
        if username is None:
            abort(404, description=f"Could not retrieve username for Roblox ID {roblox_id}")

    except requests.exceptions.RequestException as e:
        # Handle any errors that occur during the request to roproxy
        abort(500, description=f"Error fetching data from RoProxy: {e}")
    except ValueError:  # Handle JSON decoding errors
        abort(500, description="Invalid JSON response from RoProxy")


    # Search for the user in the product data (using the retrieved username).
    found = False
    for key, user in product_data.items():
        if user.lower() == username.lower():
            found = True
            break  # Exit the loop once the user is found

    # Create the response.
    response_data = {
        "username": username,  # Use the username from roproxy
        "isOwner": found,
        "product": decoded_product_name
    }

    return jsonify(response_data)

@app.errorhandler(404)
def resource_not_found(e):
    """
    404 error handler. Returns a JSON response with the error description.
    """
    return jsonify(error=str(e)), 404

@app.errorhandler(500) #added an 500 error handler.
def internal_server_error(e):
    """
    500 error handler. Returns a JSON response with error.
    """
    return jsonify(error=str(e)), 500

if __name__ == '__main__':
    # Run the application (only for local development, not for Heroku)
    # app.run(debug=True)  # debug=True - TURN OFF in production!
    pass  # Remove running via python app.py, heroku will start it via gunicorn
