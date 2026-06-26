import os
from flask import Flask, jsonify

app = Flask(__name__)

@app.route('/')
def health_check():
    """Basic health check for uptime monitoring."""
    return 'OK', 200

@app.route('/health')
def detailed_health():
    """Optional detailed health check with status."""
    return jsonify({
        "status": "healthy",
        "service": "YouTube Download Bot",
        "version": "4.0"
    }), 200

if __name__ == '__main__':
    # Use PORT from environment (default 8000)
    port = int(os.environ.get("PORT", 8000))
    app.run(host='0.0.0.0', port=port)
