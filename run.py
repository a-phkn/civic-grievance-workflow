"""Start the app:  python run.py   (then open http://127.0.0.1:5000)"""
from app import create_app

app = create_app()

if __name__ == "__main__":
    # debug stays off: the debugger would let anyone run code from the browser
    app.run(host="127.0.0.1", port=5000, debug=False)
