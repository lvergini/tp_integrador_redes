"""Servicios de orquestación entre GitHub API y capa de datos (DB)."""
from src.github_api import get_user, fetch_user_repos, fetch_user_followers
from src.db import (
    upsert_user, get_user_by_login, get_user_id_by_login,
    upsert_repos, mark_last_sync_repos, select_repos_by_owner,
    upsert_user_followers, mark_last_sync_followers, select_followers_by_user,
    mark_user_tracked,
    count_repos_for_user, count_followers_for_user,
)


def set_current_user(conn, login: str) -> dict:
    """Valida el usuario en GitHub, lo guarda/actualiza en DB y retorna el registro."""
    u = get_user(login)           # puede lanzar HTTPError si no existe (consulta a api github: devuelve dict con datos)
    upsert_user(conn, u)  # inserta o actualiza usuario en tabla user
    mark_user_tracked(conn, u["id"])  # marca un usuario como gestionado
    return get_user_by_login(conn, u["login"]) #retorna diccionario con datos del usuario



def sync_repos(conn, login: str) -> int:
    """
    Traer los repositorios del usuario desde GitHub y guardarlos en la base.

    Parámetros:
        conn: conexión activa a MySQL.
        login (str): nombre de usuario de GitHub.

    Retorna:
        int: cantidad de repositorios procesados (upserts).
    """
    owner_id = get_user_id_by_login(conn, login) # obtiene id numérico (de GitHUB) de usuario almacenado en la base (db.py)
    repos = fetch_user_repos(login) # obtiene y normaliza los repos del usuario (gihub_api.py)
    n = upsert_repos(conn, owner_id, repos) # Insertar o actualizar repos en bd.
    mark_last_sync_repos(conn, owner_id) #Actualizar la marca de tiempo 'last_sync_repos' del usuario en bd.
    return n

def show_repos(conn, login: str) -> list[tuple[str, str | None, int]]:
    """
    Leer y devolver un listado compacto de repositorios guardados del usuario.

    Parámetros:
        conn: conexión activa a MySQL.
        login (str): nombre de usuario de GitHub.

    Retorna:
        list[tuple]: tuplas (name, language, stargazers_count).
    """
    return select_repos_by_owner(conn, login)

def sync_followers(conn, login: str) -> int:
    """
    Traer los followers del usuario 'login' desde GitHub y guardarlos en la base.

    Parámetros:
        conn: conexión activa a MySQL.
        login (str): nombre de usuario de GitHub.

    Retorna:
        int: cantidad de followers procesados.
    """
    user_id = get_user_id_by_login(conn, login)
    followers = fetch_user_followers(login)
    n = upsert_user_followers(conn, user_id, followers)
    mark_last_sync_followers(conn, user_id)
    return n

def show_followers(conn, login: str) -> list[tuple[str, str | None]]:
    """
    Leer y devolver los followers guardados para un usuario.

    Parámetros:
        conn: conexión activa a MySQL.
        login (str): nombre de usuario de GitHub.

    Retorna:
        list[tuple]: pares (login, html_url) de cada follower.
    """
    return select_followers_by_user(conn, login)

def get_user_status(conn, login: str) -> dict:
    """
    Obtener un resumen del estado de un usuario en la base de datos.

    El estado incluye:
      - si el usuario existe o no,
      - sus fechas de última sincronización de repos y followers,
      - la cantidad de repositorios y followers almacenados.

    Parámetros:
        conn: conexión activa a MySQL.
        login (str): nombre de usuario de GitHub.

    Retorna:
        dict: diccionario con las claves:
            - 'exists' (bool)
            - 'login' (str | None)
            - 'name' (str | None)
            - 'last_sync_repos' (datetime | None)
            - 'last_sync_followers' (datetime | None)
            - 'repos_count' (int)
            - 'followers_count' (int)
    """
    row = get_user_by_login(conn, login)

    # Usuario no existe en la base
    if row is None:
        return {
            "exists": False,
            "login": None,
            "name": None,
            "last_sync_repos": None,
            "last_sync_followers": None,
            "repos_count": 0,
            "followers_count": 0,
        }

    user_id = row["id"]
    repos_count = count_repos_for_user(conn, user_id)
    followers_count = count_followers_for_user(conn, user_id)

    return {
        "exists": True,
        "login": row["login"],
        "name": row.get("name"),
        "last_sync_repos": row.get("last_sync_repos"),
        "last_sync_followers": row.get("last_sync_followers"),
        "repos_count": repos_count,
        "followers_count": followers_count,
    }

