AppZone - Vercel deploy

Folder structure:
  app.py            -> Flask app (variable name `app` zaruri hai)
  requirements.txt  -> Flask
  templates/        -> index.html, details.html, 404.html
  public/           -> css/ aur icons/ (static files)

Local run:   pip install -r requirements.txt && python app.py
Vercel:      GitHub par push karo -> vercel.com > Add New > Project > Import
             (Framework: Flask / Other, koi build command nahi chahiye)
Naya app add karna: app.py me apps list me entry + icon public/icons/ me.
