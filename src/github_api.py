# funciones para llamar a GitHub (con paginación)
import os
from datetime import datetime

import requests
from typing import Iterator

def gh_headers() -> dict[str, str]:
    """
    Construir encabezados HTTP para llamadas a la API de GitHub.
    Incluye versión de API, tipo de contenido y, si existe, el token en Authorization.
    """
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "github-cli/1.0",
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h

def _paginate(url: str, params: dict[str, str] | None = None) -> Iterator[dict]:
    """
    Iterar secuencialmente los ítems de un endpoint paginado de GitHub (paginación por página).
    Usa `per_page=100` por defecto y avanza hasta que no haya más resultados.
    """
    params = dict(params or {})
    params.setdefault("per_page", 100)
    page = 1
    while True:
        params["page"] = page
        r = requests.get(url, headers=gh_headers(), params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        if not data:
            break
        for item in data:
            yield item
        if len(data) < params["per_page"]:
            break
        page += 1

def iso_to_dt(s: str | None) -> datetime | None:
    """
    Convertir una cadena ISO8601 de GitHub (terminada en 'Z') a datetime naive en UTC.
    Si no hay valor, devuelve None.
    """
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)

def get_user(login: str) -> dict:
    """
    Obtener datos básicos de un usuario de GitHub o lanzar HTTPError si no existe.

    Parámetros:
        login (str): nombre de usuario de GitHub.

    Retorna:
        dict: campos normalizados del usuario.
    """
    url = f"https://api.github.com/users/{login}"
    r = requests.get(url, headers=gh_headers(), timeout=30)
    r.raise_for_status()
    u = r.json()

    return {
        "id": u["id"],
        "login": u["login"],
        "name": u.get("name"),
        "html_url": u.get("html_url"),
        "type": u.get("type"),
        "company": u.get("company"),
        "location": u.get("location"),
        "created_at": iso_to_dt(u.get("created_at")),
        "updated_at": iso_to_dt(u.get("updated_at")),
    }

def fetch_user_repos(login: str) -> list[dict]:
    """
    Obtener y normalizar los repositorios propiedad del usuario.

    Parámetros:
        login (str): nombre de usuario de GitHub.

    Retorna:
        list[dict]: lista de repositorios con campos normalizados.
    """
    url = f"https://api.github.com/users/{login}/repos"
    repos: list[dict] = []
    for r in _paginate(url, params={"type": "owner", "sort": "full_name"}):
        repos.append({
            "id": r["id"],
            "name": r["name"],
            "full_name": r["full_name"],
            "private": int(bool(r.get("private"))),
            "html_url": r.get("html_url"),
            "description": r.get("description"),
            "language": r.get("language"),
            "forks_count": r.get("forks_count", 0),
            "stargazers_count": r.get("stargazers_count", 0),
            "watchers_count": r.get("watchers_count", 0),
            "open_issues_count": r.get("open_issues_count", 0),
            "is_fork": int(bool(r.get("fork"))),
            "default_branch": r.get("default_branch"),
            "created_at": iso_to_dt(r.get("created_at")),
            "updated_at": iso_to_dt(r.get("updated_at")),
            "pushed_at": iso_to_dt(r.get("pushed_at")),
        })
    return repos

def fetch_user_followers(login: str) -> list[dict]:
    """
    Obtener la lista de seguidores (followers) de un usuario de GitHub.

    Parámetros:
        login (str): nombre de usuario de GitHub.

    Retorna:
        list[dict]: lista de seguidores con campos normalizados (id, login, html_url).
    """
    url = f"https://api.github.com/users/{login}/followers"
    followers: list[dict] = []
    for f in _paginate(url):
        followers.append({
            "id": f["id"],
            "login": f["login"],
            "html_url": f.get("html_url"),
        })
    return followers