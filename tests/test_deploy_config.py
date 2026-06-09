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


def test_traefik_compose():
    c = _load_yaml("deploy/traefik/docker-compose.yml")
    svc = c["services"]["traefik"]
    assert svc["image"].startswith("traefik:v3")
    assert "80:80" in svc["ports"]
    assert "443:443" in svc["ports"]
    vols = svc["volumes"]
    assert any("/var/run/docker.sock" in v and v.endswith(":ro") for v in vols)
    assert any("traefik.yml" in v and v.endswith(":ro") for v in vols)
    assert any("/acme" in v for v in vols)
    assert svc["networks"] == ["proxy"]
    assert c["networks"]["proxy"]["external"] is True


def test_reportbot_compose_has_traefik_labels_and_no_host_port():
    c = _load_yaml("docker-compose.yml")
    svc = c["services"]["report-bot"]
    joined = "\n".join(svc["labels"])
    assert "traefik.enable=true" in joined
    assert "Host(`report-bot.${PUBLIC_IP}.sslip.io`)" in joined
    assert "entrypoints=websecure" in joined
    assert "tls.certresolver=le" in joined
    assert "loadbalancer.server.port=8000" in joined
    assert svc["networks"] == ["proxy"]
    assert c["networks"]["proxy"]["external"] is True
    # 호스트 포트 공개 제거: 8000:8000 매핑이 없어야 한다(Traefik 경유)
    assert "8000:8000" not in svc.get("ports", [])


def test_root_env_example_and_gitignore():
    env = (REPO / ".env.example").read_text(encoding="utf-8")
    assert "PUBLIC_IP=" in env
    gi = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gi
    assert "acme" in gi
