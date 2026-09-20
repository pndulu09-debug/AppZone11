import os
from flask import Flask, abort, render_template, send_from_directory

BASE = os.path.dirname(os.path.abspath(__file__))

# Flat structure: saari files (html, css, png) repo ke root me hain.
# static_folder=None -> app.py jaisi files kabhi public nahi hongi.
app = Flask(__name__, template_folder=BASE, static_folder=None)


@app.route("/css/details.css")
def details_css():
    return send_from_directory(BASE, "details.css", mimetype="text/css")


@app.route("/icons/<path:filename>")
def icons(filename):
    # sirf .png files allow hain
    if "/" in filename or not filename.lower().endswith(".png"):
        abort(404)
    return send_from_directory(BASE, filename)


apps = [
    {
        "name": "CapCut",
        "version": "v28.0.0",
        "size": "278.2 MB",
        "icon": "/icons/capcut.png",
        "description": "CapCut video editor.",
        "download": "https://vplink.in/aFaBR"
    },
    {
        "name": "YouTube",
        "version": "v21.07.247",
        "size": "187.6 MB",
        "icon": "/icons/youtube.png",
        "description": "YouTube app.",
        "download": "https://vplink.in/wUDAn1"
    },
    {
        "name": "Truecaller",
        "version": "Latest",
        "size": "Varies",
        "icon": "/icons/truecaller.png",
        "description": "Truecaller caller ID and spam blocking app.",
        "download": "https://vplink.in/kaAj"
    },
    {
        "name": "Telegram",
        "version": "Latest",
        "size": "Varies",
        "icon": "/icons/telegram.png",
        "description": "Telegram messenger.",
        "download": "https://vplink.in/OIBgy3"
    },
    {
        "name": "TeraBox",
        "version": "v4.24.0",
        "size": "Varies",
        "icon": "/icons/terabox.png",
        "description": "TeraBox cloud storage app.",
        "download": "https://vplink.in/hc8sKV"
    },
    {
        "name": "Getmodpc Services",
        "version": "Latest",
        "size": "Varies",
        "icon": "/icons/getmodepc.png",
        "description": "Getmodpc Services APK.",
        "download": "https://www.mediafire.com/file/grlgisfuewmpjtr/Getmodpc+Services.apk/file"
    }
]


@app.route("/")
def home():
    return render_template("index.html", apps=apps)


@app.route("/app/<int:app_id>")
def app_details(app_id):
    if app_id < 0 or app_id >= len(apps):
        return render_template("404.html"), 404

    others = [
        {"id": i, **a}
        for i, a in enumerate(apps)
        if i != app_id
    ][:4]

    return render_template(
        "details.html",
        app=apps[app_id],
        others=others,
    )


@app.errorhandler(404)
def not_found(_):
    return render_template("404.html"), 404


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=os.environ.get("FLASK_DEBUG") == "1")
