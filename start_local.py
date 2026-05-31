"""Local development entry point — loads .env then starts Flask on port 8080."""
from dotenv import load_dotenv
load_dotenv()

from app import app

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
