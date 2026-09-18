# Partner web static assets

This directory is the Flask static_folder.  The frontend source lives
at ``partner/web/frontend_src`` and is built by Vite into this directory:

    cd partner/web/frontend_src
    npm install
    npm run build       # produces ../static/index.html + ../static/assets/

Until the build runs, Flask will 404 on ``/static/index.html``.  The
verification pass must run the build before testing the web UI.
