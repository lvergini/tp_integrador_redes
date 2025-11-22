"""Servicios de orquestación entre GitHub API y capa de datos (DB)."""
from src.github_api import get_user, fetch_user_repos, fetch_user_followers
from src.db import (
    upsert_user, get_user_by_login, get_user_id_by_login,
    upsert_repos, mark_last_sync_repos, select_repos_by_owner,
    upsert_user_followers, mark_last_sync_followers, select_followers_by_user,
    select_all_users,mark_user_tracked,  
)


def set_current_user(conn, login: str) -> dict:
    """Valida el usuario en GitHub, lo guarda/actualiza en DB y retorna el registro."""
    u = get_user(login)           # puede lanzar HTTPError si no existe
    upsert_user(conn, u)
    mark_user_tracked(conn, u["id"])
    return get_user_by_login(conn, u["login"])

def refresh_current_user_row(conn, login: str) -> dict | None:
    """
    Recargar desde la base la fila del usuario actual.
    Útil tras una sincronización para reflejar las fechas last_sync actualizadas.

    Parámetros:
        conn: conexión activa a MySQL.
        login (str): nombre de usuario de GitHub.

    Retorna:
        dict | None: fila completa actualizada del usuario en DB, o None si no existe.
    """
    return get_user_by_login(conn, login)

def sync_repos(conn, login: str) -> int:
    """
    Traer los repositorios del usuario desde GitHub y guardarlos en la base.

    Parámetros:
        conn: conexión activa a MySQL.
        login (str): nombre de usuario de GitHub.

    Retorna:
        int: cantidad de repositorios procesados (upserts).
    """
    owner_id = get_user_id_by_login(conn, login)
    repos = fetch_user_repos(login)
    n = upsert_repos(conn, owner_id, repos)
    mark_last_sync_repos(conn, owner_id)
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


def list_stored_users(conn) -> list[dict]:
    """
    Devolver todos los usuarios almacenados en la base de datos.

    Parámetros:
        conn: conexión activa a MySQL.

    Retorna:
        list[dict]: filas con id, login, last_sync_repos y last_sync_followers.
    """
    return select_all_users(conn)
