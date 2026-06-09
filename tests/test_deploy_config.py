from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]


def _load_yaml(rel: str):
    return yaml.safe_load((REPO / rel).read_text(encoding="utf-8"))


def test_traefik_static_config():
    cfg = _load_yaml("deploy/traefik/traefik.yml")
    eps = cfg["entryPoints"]
    assert eps["web"]["address"] == ":80"
    redir = eps["web"]["http"]["redirections"]["entryPoint"]
    assert redir["to"] == "websecure"
    assert redir["scheme"] == "https"
    assert eps["websecure"]["address"] == ":443"
    acme = cfg["certificatesResolvers"]["le"]["acme"]
    assert "email" in acme
    assert acme["storage"] == "/acme/acme.json"
    assert "tlsChallenge" in acme
    docker = cfg["providers"]["docker"]
    assert docker["exposedByDefault"] is False
    assert docker["network"] == "proxy"
