# conexión, DDL (crear tablas), upserts
import os
import mysql.connector
from mysql.connector import Error
from dotenv import load_dotenv

load_dotenv()  # lee variables desde .env

def get_conn():
    """
    Devolver una conexión a MySQL usando variables de entorno.

    Retorna:
        mysql.connector.connection.MySQLConnection: conexión activa.
    Lanza:
        RuntimeError: si la conexión falla.
    """
    try:
        conn = mysql.connector.connect(
            host=os.getenv("MYSQL_HOST", "localhost"),
            port=int(os.getenv("MYSQL_PORT", "3306")),
            user=os.getenv("MYSQL_USER", "root"),
            password=os.getenv("MYSQL_PASSWORD", ""),
            database=os.getenv("MYSQL_DB", "githubdb"),
            charset="utf8mb4",
            collation="utf8mb4_unicode_ci",
        )
        return conn
    except Error as e:
        raise RuntimeError(f"Error conectando a MySQL: {e}")

def init_db(conn) -> None:
    """
    Crear las tablas si no existen: users, repositorios, followers.

    Parámetros:
        conn: conexión activa a MySQL.
    """
    ddl_users = """
    CREATE TABLE IF NOT EXISTS users (
      id BIGINT UNSIGNED PRIMARY KEY,           -- id de GitHub
      login VARCHAR(100) NOT NULL UNIQUE,
      name VARCHAR(255),
      html_url VARCHAR(500),
      type VARCHAR(30),
      company VARCHAR(255),
      location VARCHAR(255),
      created_at DATETIME,
      updated_at DATETIME,
      last_sync_repos DATETIME NULL,
      last_sync_followers DATETIME NULL,
      is_tracked TINYINT(1) NOT NULL DEFAULT 0,  -- marcado manual
      INDEX ix_login (login),
      INDEX ix_is_tracked (is_tracked)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
    """

    ddl_repositorios = """
    CREATE TABLE IF NOT EXISTS repositorios (
      id BIGINT UNSIGNED PRIMARY KEY,           -- id de repo en GitHub
      owner_id BIGINT UNSIGNED NOT NULL,        -- FK a users.id
      name VARCHAR(255) NOT NULL,
      full_name VARCHAR(255) NOT NULL,
      private TINYINT(1) NOT NULL,
      html_url VARCHAR(500),
      description TEXT,
      language VARCHAR(100),
      forks_count INT UNSIGNED,
      stargazers_count INT UNSIGNED,
      watchers_count INT UNSIGNED,
      open_issues_count INT UNSIGNED,
      is_fork TINYINT(1) NOT NULL,
      default_branch VARCHAR(100),
      created_at DATETIME,
      updated_at DATETIME,
      pushed_at DATETIME,
      CONSTRAINT fk_repo_owner FOREIGN KEY (owner_id) REFERENCES users(id),
      INDEX ix_owner_id (owner_id),
      INDEX ix_language (language)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
    """

    ddl_followers = """
    CREATE TABLE IF NOT EXISTS followers (
      followed_id BIGINT UNSIGNED NOT NULL,     -- a quién siguen
      follower_id BIGINT UNSIGNED NOT NULL,     -- quién lo sigue
      PRIMARY KEY (followed_id, follower_id),
      CONSTRAINT fk_followed FOREIGN KEY (followed_id) REFERENCES users(id),
      CONSTRAINT fk_follower FOREIGN KEY (follower_id) REFERENCES users(id),
      INDEX ix_follower (follower_id)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
    """

    cur = conn.cursor()
    try:
        cur.execute(ddl_users)
        cur.execute(ddl_repositorios)
        cur.execute(ddl_followers)
        conn.commit()
    finally:
        cur.close()

def upsert_user(conn, user: dict) -> None:
    """
    Insertar o actualizar un usuario en la tabla 'users'.

    Parámetros:
        conn: conexión activa a MySQL.
        user (dict): datos normalizados del usuario (id, login, name, html_url, type, company,
                     location, created_at, updated_at).

    Retorna:
        None
    """
    
    sql = """
    INSERT INTO users (id, login, name, html_url, type, company, location, created_at, updated_at)
    VALUES (%(id)s, %(login)s, %(name)s, %(html_url)s, %(type)s, %(company)s, %(location)s, %(created_at)s, %(updated_at)s)
    ON DUPLICATE KEY UPDATE
      login=VALUES(login),
      name=VALUES(name),
      html_url=VALUES(html_url),
      type=VALUES(type),
      company=VALUES(company),
      location=VALUES(location),
      created_at=VALUES(created_at),
      updated_at=VALUES(updated_at)
    """
    cur = conn.cursor()
    try:
        cur.execute(sql, user)
        conn.commit()
    finally:
        cur.close()

def mark_user_tracked(conn, user_id: int) -> None:
    """
    Marcar a un usuario como gestionado manualmente (is_tracked = 1).

    Parámetros:
        conn: conexión activa a MySQL.
        user_id (int): PK de 'users'.

    Retorna:
        None
    """
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE users SET is_tracked = 1 WHERE id = %(user_id)s",
            {"user_id": user_id},
        )
        conn.commit()
    finally:
        cur.close()

def get_user_by_login(conn, login: str):
    """
    Obtener un usuario de la base de datos a partir de su login.

    Parámetros:
        conn: conexión activa a MySQL.
        login (str): nombre de usuario de GitHub.

    Retorna:
        dict | None: un diccionario con todos los campos de la tabla 'users'
                      si el usuario existe; de lo contrario, None.
    """
    cur = conn.cursor(dictionary=True) # Devuelve diccionario donde cadaa columna viene con su nombre, en lugar de tupla por posición (opción por defecto en cursor)
    try:
        cur.execute("SELECT * FROM users WHERE login = %(login)s", {"login": login})
        row = cur.fetchone()
        return row
    finally:
        cur.close()

def get_user_id_by_login(conn, login: str) -> int:
    """
    Obtener el ID numérico (id de GitHub) de un usuario almacenado en la base.

    Parámetros:
        conn: conexión activa a MySQL.
        login (str): nombre de usuario de GitHub.

    Retorna:
        int: el ID numérico del usuario (primary key de la tabla 'users').

    Lanza:
        ValueError: si no existe un usuario con ese login en la base.
    """
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM users WHERE login = %(login)s", {"login": login})
        row = cur.fetchone()
    finally:
        cur.close()

    if not row:
        raise ValueError(f"El usuario '{login}' no existe en la base de datos.")


    return int(row[0])


def upsert_repos(conn, owner_id: int, repos: list[dict]) -> int:
    """
    Insertar o actualizar una lista de repositorios para un owner_id.

    Parámetros:
        conn: conexión activa a MySQL.
        owner_id (int): id del propietario del repositorio (FK a users.id).
        repos (list[dict]): lista de repositorios normalizados.

    Retorna:
        int: cantidad de registros procesados.
    """

    sql = """
    INSERT INTO repositorios
      (id, owner_id, name, full_name, private, html_url, description, language,
       forks_count, stargazers_count, watchers_count, open_issues_count, is_fork,
       default_branch, created_at, updated_at, pushed_at)
    VALUES
      (%(id)s, %(owner_id)s, %(name)s, %(full_name)s, %(private)s, %(html_url)s, %(description)s,
       %(language)s, %(forks_count)s, %(stargazers_count)s, %(watchers_count)s, %(open_issues_count)s,
       %(is_fork)s, %(default_branch)s, %(created_at)s, %(updated_at)s, %(pushed_at)s)
    ON DUPLICATE KEY UPDATE
      owner_id=VALUES(owner_id),
      name=VALUES(name),
      full_name=VALUES(full_name),
      private=VALUES(private),
      html_url=VALUES(html_url),
      description=VALUES(description),
      language=VALUES(language),
      forks_count=VALUES(forks_count),
      stargazers_count=VALUES(stargazers_count),
      watchers_count=VALUES(watchers_count),
      open_issues_count=VALUES(open_issues_count),
      is_fork=VALUES(is_fork),
      default_branch=VALUES(default_branch),
      created_at=VALUES(created_at),
      updated_at=VALUES(updated_at),
      pushed_at=VALUES(pushed_at)
    """
    cur = conn.cursor()
    try:
        for r in repos:
            r = dict(r)
            r["owner_id"] = owner_id
            cur.execute(sql, r)
        conn.commit()
    finally:
        cur.close()
    return len(repos)

def mark_last_sync_repos(conn, user_id: int) -> None:
    """
    Actualizar la marca de tiempo 'last_sync_repos' del usuario indicado.

    Parámetros:
        conn: conexión activa a MySQL.
        user_id (int): identificador del usuario (PK en 'users').

    Retorna:
        None
    """
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE users SET last_sync_repos = NOW() WHERE id = %(user_id)s",
            {"user_id": user_id},
        )
        conn.commit()
    finally:
        cur.close()

def select_repos_by_owner(conn, login: str) -> list[tuple]:
    """
    Obtener repositorios de un usuario (por login).

    Parámetros:
        conn: conexión activa a MySQL.
        login (str): nombre de usuario de GitHub (owner).

    Retorna:
        list[tuple]: filas (name, language, stargazers_count) ordenadas por estrellas DESC y nombre ASC.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT r.name, r.language, r.stargazers_count
            FROM repositorios r
            JOIN users u ON u.id = r.owner_id
            WHERE u.login = %(login)s
            ORDER BY r.stargazers_count DESC, r.name ASC
            """,
            {"login": login},
        )
        rows = cur.fetchall()
        return rows
    finally:
        cur.close()

def upsert_user_followers(conn, followed_id: int, followers: list[dict]) -> int:
    """
    Registrar relaciones de seguimiento para un usuario dado: (followed <- follower).

    Parámetros:
        conn: conexión activa a MySQL.
        followed_id (int): id del usuario seguido (owner).
        followers (list[dict]): lista de seguidores con claves 'id', 'login', 'html_url'.

    Retorna:
        int: cantidad de relaciones procesadas.
    """
    # 1) asegurar que cada follower exista en 'users' (upsert básico)
    sql_user = """
    INSERT INTO users (id, login, html_url)
    VALUES (%(id)s, %(login)s, %(html_url)s)
    ON DUPLICATE KEY UPDATE
    login=VALUES(login),
    html_url=VALUES(html_url)
    """
    # 2) upsert de la relación en 'user_followers'
    sql_rel = """
    INSERT INTO followers (followed_id, follower_id)
    VALUES (%(followed_id)s, %(follower_id)s)
    ON DUPLICATE KEY UPDATE follower_id = VALUES(follower_id)
    """
    cur = conn.cursor()
    try:
        for f in followers:
            payload_user = {
                "id": f["id"],
                "login": f["login"],
                "html_url": f.get("html_url"),
            }
            payload_rel = {
                "followed_id": followed_id,
                "follower_id": f["id"],
            }
            cur.execute(sql_user, payload_user)
            cur.execute(sql_rel, payload_rel)
        conn.commit()
    finally:
        cur.close()
    return len(followers)

def mark_last_sync_followers(conn, user_id: int) -> None:
    """
    Actualizar la marca de tiempo 'last_sync_followers' del usuario indicado.

    Parámetros:
        conn: conexión activa a MySQL.
        user_id (int): identificador del usuario (PK en 'users').

    Retorna:
        None
    """
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE users SET last_sync_followers = NOW() WHERE id = %(user_id)s",
            {"user_id": user_id},
        )
        conn.commit()
    finally:
        cur.close()

def select_followers_by_user(conn, login: str) -> list[tuple]:
    """
    Obtener los seguidores de un usuario (por login) desde la base de datos.

    Parámetros:
        conn: conexión activa a MySQL.
        login (str): nombre de usuario de GitHub (seguido).

    Retorna:
        list[tuple]: filas (login, html_url) de cada follower.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT uf.follower_id, u2.login, u2.html_url
            FROM followers uf
            JOIN users u1 ON u1.id = uf.followed_id
            JOIN users u2 ON u2.id = uf.follower_id
            WHERE u1.login = %(login)s
            ORDER BY u2.login ASC
            """,
            {"login": login},
        )
        rows = cur.fetchall()
        # devolver solo (login, html_url)
        return [(r[1], r[2]) for r in rows]
    finally:
        cur.close()

def count_repos_for_user(conn, owner_id: int) -> int:
    """
    Contar la cantidad de repositorios almacenados para un usuario dado.

    Parámetros:
        conn: conexión activa a MySQL.
        owner_id (int): identificador del usuario (FK en 'repositorios.owner_id').

    Retorna:
        int: cantidad de repositorios asociados al usuario.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT COUNT(*) FROM repositorios WHERE owner_id = %(owner_id)s",
            {"owner_id": owner_id},
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0
    finally:
        cur.close()


def count_followers_for_user(conn, followed_id: int) -> int:
    """
    Contar la cantidad de followers almacenados para un usuario dado.

    Parámetros:
        conn: conexión activa a MySQL.
        followed_id (int): identificador del usuario seguido
                           (FK en 'followers.followed_id').

    Retorna:
        int: cantidad de followers asociados al usuario.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT COUNT(*) FROM followers WHERE followed_id = %(followed_id)s",
            {"followed_id": followed_id},
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0
    finally:
        cur.close()


"""

def select_all_users(conn) -> list[dict]:
    
    Devolver todos los usuarios almacenados en la base de datos.

    Parámetros:
        conn: conexión activa a MySQL.

    Retorna:
        list[dict]: filas con id, login, last_sync_repos y last_sync_followers.
    
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
           
            SELECT id, login, last_sync_repos, last_sync_followers
            FROM users
            WHERE is_tracked = 1
            ORDER BY login ASC;
            
        )
        rows = cursor.fetchall()
        return rows
    finally:
        cursor.close()


"""
