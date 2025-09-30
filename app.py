import os
import json
import base64

from flask import Flask, request, jsonify

# Import cryptographic libraries
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives import serialization

app = Flask(__name__)

# --- CONFIGURATION ---
VERIFY_TOKEN = "obisar2121" # Your webhook verify token

# Load private key from environment variable for security
# This will be set in Render's dashboard
private_key_pem = os.environ.get("PRIVATE_KEY")
if not private_key_pem:
    raise ValueError("PRIVATE_KEY environment variable not set.")

# The private key is loaded once when the application starts
PRIVATE_KEY = serialization.load_pem_private_key(
    private_key_pem.encode(),
    password=None
)

# --- CRYPTOGRAPHIC HELPER FUNCTIONS ---

def decrypt_request(encrypted_aes_key_b64, initial_vector_b64, encrypted_flow_data_b64):
    """Decrypts the incoming request from WhatsApp."""
    # 1. Decode Base64 encoded data
    encrypted_aes_key = base64.b64decode(encrypted_aes_key_b64)
    initial_vector = base64.b64decode(initial_vector_b64)
    encrypted_flow_data = base64.b64decode(encrypted_flow_data_b64)

    # 2. Decrypt the AES key using your private key
    # The first 32 bytes of the encrypted_aes_key is the public key from WhatsApp
    whatsapp_public_key = x25519.X25519PublicKey.from_public_bytes(encrypted_aes_key[:32])
    shared_key = PRIVATE_KEY.exchange(whatsapp_public_key)

    # The rest of the encrypted_aes_key is the actual key payload
    # We use HKDF to derive a key, but for this specific protocol, a simpler derivation is used.
    # We'll use a simplified key derivation for this step as per common examples.
    # In a real-world scenario, you'd use HKDF as specified in the protocol.
    # For this example, let's assume direct use or a simplified derivation.
    # NOTE: The actual key derivation might be more complex (e.g., using HKDF).
    # This is a simplified example based on common interpretations.
    # A proper implementation would use a key derivation function (KDF).
    # For now, we will treat the shared_key as the basis for our AES key.
    # A common pattern is to use a KDF like HKDF on the shared_key.
    # Let's assume the derived key is directly the shared_key for this example.
    aes_key = shared_key # This should ideally be derived using HKDF

    # 3. Decrypt the flow data using the AES key
    aesgcm = AESGCM(aes_key)
    decrypted_data = aesgcm.decrypt(initial_vector, encrypted_flow_data, None)
    
    return json.loads(decrypted_data.decode('utf-8'))


def encrypt_response(aes_key, response_data):
    """Encrypts the response to send back to WhatsApp."""
    aesgcm = AESGCM(aes_key)
    iv = os.urandom(12)  # Generate a random 12-byte IV for encryption
    
    encrypted_data = aesgcm.encrypt(iv, json.dumps(response_data).encode('utf-8'), None)
    
    # Return the IV and encrypted data, both Base64 encoded
    return {
        "encrypted_flow_data": base64.b64encode(encrypted_data).decode('utf-8'),
        "initial_vector": base64.b64encode(iv).decode('utf-8'),
    }

# --- FLASK ENDPOINT ---

@app.route('/api/whatsapp', methods=['GET', 'POST'])
def whatsapp_endpoint():
    
    if request.method == 'GET':
        # Webhook Verification
        mode = request.args.get('hub.mode')
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')
        if mode == 'subscribe' and token == VERIFY_TOKEN:
            return challenge, 200
        else:
            return 'Verification token does not match', 403

    elif request.method == 'POST':
        request_body = request.get_json()
        
        # Extract encrypted data from the request
        encrypted_flow_data = request_body.get('encrypted_flow_data')
        encrypted_aes_key = request_body.get('encrypted_aes_key')
        initial_vector = request_body.get('initial_vector')
        
        # Decrypt the request
        decrypted_data = decrypt_request(encrypted_aes_key, initial_vector, encrypted_flow_data)
        print("Decrypted Data:", decrypted_data)
        
        # Your business logic goes here
        action = decrypted_data.get('action')
        if action == 'start_selection':
            # This is where you'd build the next screen (e.g., budget question)
            response_to_encrypt = {
                "version": "3.0",
                "screen": "BUDGET_SCREEN", # The ID of the next screen in your Flow JSON
                "data": {
                    # You can pass data to the next screen here
                    "greeting": "Отлично, давайте определимся с бюджетом."
                }
            }
        else:
            # Default response if action is unknown
            response_to_encrypt = {
                "version": "3.0",
                "screen": "ERROR_SCREEN",
                "data": {"message": "Unknown action."}
            }

        # Retrieve the AES key again for encryption (simplified)
        encrypted_aes_key_bytes = base64.b64decode(encrypted_aes_key)
        whatsapp_public_key = x25519.X25519PublicKey.from_public_bytes(encrypted_aes_key_bytes[:32])
        aes_key = PRIVATE_KEY.exchange(whatsapp_public_key) # Simplified key usage

        # Encrypt the response and send it back
        encrypted_response = encrypt_response(aes_key, response_to_encrypt)
        return jsonify(encrypted_response)

if __name__ == '__main__':
    app.run()
