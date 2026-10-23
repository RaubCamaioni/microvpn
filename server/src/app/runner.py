"""The `microvpn` console script: build the app from the environment and serve it with uvicorn."""

import importlib.util


def runner():
    import uvicorn

    from app.app import create_app
    from app.settings import settings_from_os

    settings = settings_from_os()
    app = create_app(settings)
    # root_path makes request.scope["root_path"] the mount prefix; routing still sees the paths below it.
    # proxy_headers: nginx's X-Forwarded-* and X-Real-IP are trusted from the Docker network only.
    uvicorn.run(
        app,
        host=settings.http_host,
        port=settings.http_port,
        root_path=settings.ROOT_PATH,
        proxy_headers=True,
        forwarded_allow_ips=settings.FORWARDED_ALLOW_IPS,
        loop="uvloop" if importlib.util.find_spec("uvloop") else "auto",
    )
