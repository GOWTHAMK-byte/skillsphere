from flask import Flask, request, jsonify, g
import firebase_admin
from firebase_admin import credentials, auth
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # allow all origins

cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)

def require_auth(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization", None)
        if not auth_header or not auth_header.startswith("Bearer "):
            return jsonify({"error": "Unauthorized"}), 401

        id_token = auth_header.split(" ")[1]
        print("Received ID Token:", id_token)  # DEBUG

        try:
            decoded = auth.verify_id_token(id_token, check_revoked=False)  # 🔹 Allow testing
            g.firebase_user = decoded
        except Exception as e:
            print("Token verification failed:", e)  # DEBUG
            return jsonify({"error": "Invalid or expired token"}), 401

        return f(*args, **kwargs)
    return wrapper

@app.route("/secure")
@require_auth
def secure():
    return jsonify({
        "message": "Token valid!",
        "uid": g.firebase_user.get("uid"),
        "email": g.firebase_user.get("email"),
        "name": g.firebase_user.get("name")  # displayName if set
    })

if __name__ == "__main__":
    app.run(debug=True)
