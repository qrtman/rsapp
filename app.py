# Import the Flask library
from flask import Flask, request, jsonify

# Create a new Flask application
app = Flask(__name__)

# Define the endpoint URL that WhatsApp will send requests to
@app.route('/api/whatsapp', methods=['POST'])
def whatsapp_endpoint():
    """
    This is the main endpoint for the WhatsApp Flow.
    It receives an encrypted payload, will decrypt it,
    process the request, and send back an encrypted response.
    """
    # Get the raw data from the request
    request_data = request.get_data()

    # For now, we'll just print it to see what we receive.
    # This helps with debugging.
    print("Received raw data:", request_data)

    # --- TODO: DECRYPTION LOGIC ---
    # Here is where you will eventually add the code to
    # decrypt the 'request_data' using your private key.

    # --- TODO: YOUR BUSINESS LOGIC ---
    # After decrypting, you'll process the user's request.
    # For example, check which button they pressed and decide
    # which screen to send back.

    # --- TODO: ENCRYPTION LOGIC ---
    # Before sending the response, you must encrypt it.
    # We will build a sample response and encrypt it here.

    # For now, we'll just send a simple, unencrypted JSON response for testing.
    response_payload = {
        "version": "1.0",
        "data": {
            "message": "Response from the endpoint!"
        }
    }

    # Return the response as JSON
    return jsonify(response_payload)

# This allows the app to run
if __name__ == '__main__':
    # The port is automatically handled by Render, so you don't need to specify it.
    app.run()
